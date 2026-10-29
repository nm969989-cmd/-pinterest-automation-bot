"""
telegram_bot.py - Telegram control interface for the Animanoizing Pinterest Bot.

Commands:
  /start         - Register as admin, get your chat ID
  /help          - Show all commands
  /status        - Bot running? Mode? Uptime?
  /stats         - Pins today, total, queue size
  /preview       - Last generated pin (image + caption)
  /logs          - Show last 10 log lines
  /channels      - List monitored channels
  /addchannel    - Add a new source channel
  /removechannel - Remove a source channel
  /setdelay      - Change posting delay (minutes)
  /setmax        - Change max pins per day
  /dryrun        - Toggle dry-run on/off
  /golive        - Switch to live Pinterest posting
  /autopilot     - Toggle auto-post vs Telegram approval mode
  /testpost      - Send a test pin via Make.com webhook
  /pause         - Pause posting
  /resume        - Resume posting
  /queue         - Show pending queue with per-date schedule breakdown
  /post_now      - Force-post next queued pin immediately (bypass time slot)
  /clearqueue    - Wipe all pending pins from the queue
  /fixqueue      - Re-upload stale Telegram CDN URLs to permanent host
  /ping          - Check if bot responds
  /arena         - Show Are.na channel stats (block count, URL)
  /arena_test    - Post a test block to Are.na to verify token
"""

import os
import json
import threading
import datetime
import asyncio
import time
from logger import get_logger

logger = get_logger(__name__)

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler,
        MessageHandler, ContextTypes, filters
    )
    _TG_AVAILABLE = True
except ImportError:
    _TG_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Run: pip install python-telegram-bot")

# -- Shared state -------------------------------------------------------------
_state = {
    "start_time":    datetime.datetime.now(),
    "posts_today":   0,
    "posts_total":   0,
    "queue_size":    0,
    "last_pin":      None,
    "is_paused":     False,
    "dry_run":       True,
    "auto_post":     True,   # True = fully automatic, False = require Telegram button approval
    "channels":      [],
    "admin_chat_id": None,
    "post_delay":    10,
    "max_per_day":   5,
}

# Pending approval queue: maps callback_data key -> upload args dict
_pending_approvals: dict = {}

# Pending Quick-Drop studio drafts: maps drop_id -> draft args dict
_pending_drops: dict = {}

# Reference to scheduler (set by main.py)
_scheduler_ref = None


def set_scheduler(scheduler):
    global _scheduler_ref
    _scheduler_ref = scheduler


def update_state(**kwargs):
    """Called by main.py to update shared state."""
    _state.update(kwargs)


def record_pin(anime_name, title, description, link, image_path):
    """Called whenever a pin is processed."""
    _state["last_pin"] = {
        "anime":       anime_name,
        "title":       title,
        "description": description,
        "link":        link,
        "image_path":  image_path,
        "time":        datetime.datetime.now().strftime("%H:%M:%S"),
    }
    _state["posts_today"] += 1
    _state["posts_total"] += 1


# -- Auth helper --------------------------------------------------------------
def _is_admin(update: "Update") -> bool:
    """Fail closed: with no admin configured nobody is admin.

    /start no longer writes admin_chat_id, so a stranger cannot become admin.
    """
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not admin_id:
        return False
    chat = update.effective_chat
    if chat is None:
        return False
    return str(chat.id) == str(admin_id)


def get_queue_keyboard() -> "InlineKeyboardMarkup | None":
    """Returns interactive inline buttons for quick 1-tap actions."""
    if not _TG_AVAILABLE:
        return None
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Post Next Now", callback_data="btn_postnow"),
            InlineKeyboardButton("🔄 Scrape Channels", callback_data="btn_scrape"),
        ],
        [
            InlineKeyboardButton("📋 View Queue", callback_data="btn_queue"),
            InlineKeyboardButton("⏰ Today's Schedule", callback_data="btn_schedule"),
        ],
        [
            InlineKeyboardButton("📊 Stats", callback_data="btn_stats"),
            InlineKeyboardButton("📈 Digest", callback_data="btn_digest"),
            InlineKeyboardButton("🩺 Doctor Check", callback_data="btn_doctor"),
        ],
    ])


def _is_valid_http_url(url: str) -> bool:
    """Helper to ensure Telegram InlineKeyboardButton never receives non-HTTP or invalid URLs."""
    if not url or not isinstance(url, str):
        return False
    u = url.strip()
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if any(c.isspace() for c in u):
        return False
    # Filter out common placeholder/status strings that could break Telegram API
    if any(k in u.lower() for k in ["cooling down", "failed", "disabled", "null", "none", "circuit breaker"]):
        return False
    return len(u) > 10


def get_post_confirmation_keyboard(
    amazon_url: str = "",
    pinterest_url: str = "",
    bluesky_url: str = "",
    mastodon_url: str = "",
    arena_url: str = "",
    raindrop_url: str = "",
    pixelfed_url: str = "",
    freeimage_url: str = "",
    imghippo_url: str = "",
    wallhaven_url: str = "",
    deviantart_url: str = "",
    imgbb_url: str = "",
    imagekit_url: str = "",
    imgpile_url: str = "",
) -> "InlineKeyboardMarkup | None":

    """Returns interactive direct link buttons for a posted pin across all active platforms."""
    if not _TG_AVAILABLE:
        return None
    try:
        import config

        rows = []

        # Row 1: Pinterest + Amazon
        row1 = []
        p_url = pinterest_url if _is_valid_http_url(pinterest_url) else getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
        if _is_valid_http_url(p_url):
            row1.append(InlineKeyboardButton("📌 View Pinterest", url=p_url))
        if _is_valid_http_url(amazon_url):
            amazon_label = "🎯 Amazon Product" if "/dp/" in amazon_url else "🔍 Browse Amazon"
            row1.append(InlineKeyboardButton(amazon_label, url=amazon_url))
        if row1:
            rows.append(row1)

        # Secondary platforms (paired 2 per row - only enabled & active platforms)
        platform_btns = []

        if getattr(config, "BLUESKY_ENABLED", False):
            b_handle = getattr(config, "BLUESKY_HANDLE", "muthelyrics.bsky.social")
            fallback_bsky = f"https://bsky.app/profile/{b_handle}"
            b_link = bluesky_url if _is_valid_http_url(bluesky_url) else fallback_bsky
            btn_txt = "🦋 View on Bluesky" if (bluesky_url and "/post/" in bluesky_url) else "🦋 Bluesky Feed"
            if _is_valid_http_url(b_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=b_link))

        if getattr(config, "MASTODON_ENABLED", False):
            fallback_masto = "https://mastodon.social/@muthelyrics"
            m_link = mastodon_url if _is_valid_http_url(mastodon_url) else fallback_masto
            btn_txt = "🐘 View on Mastodon" if (mastodon_url and "/@" in mastodon_url and mastodon_url != fallback_masto) else "🐘 Mastodon Feed"
            if _is_valid_http_url(m_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=m_link))

        if getattr(config, "RAINDROP_ENABLED", False):
            r_col = getattr(config, "RAINDROP_COLLECTION_ID", "")
            fallback_raindrop = f"https://app.raindrop.io/my/{r_col}" if r_col else "https://app.raindrop.io"
            r_link = raindrop_url if _is_valid_http_url(raindrop_url) else fallback_raindrop
            if _is_valid_http_url(r_link):
                platform_btns.append(InlineKeyboardButton("💧 Raindrop Bookmark", url=r_link))

        if getattr(config, "FREEIMAGE_ENABLED", False):
            fallback_fi = "https://freeimage.host/muthelyrics"
            fi_link = freeimage_url if _is_valid_http_url(freeimage_url) else fallback_fi
            btn_txt = "🖼️ View on Freeimage" if (freeimage_url and "/i/" in freeimage_url) else "🖼️ Freeimage Gallery"
            if _is_valid_http_url(fi_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=fi_link))

        if getattr(config, "WALLHAVEN_ENABLED", False):
            wh_user = getattr(config, "WALLHAVEN_USERNAME", "animizing")
            fallback_wh = f"https://wallhaven.cc/user/{wh_user}"
            wh_link = wallhaven_url if _is_valid_http_url(wallhaven_url) else fallback_wh
            btn_txt = "🌌 View on Wallhaven" if (wallhaven_url and "/w/" in wallhaven_url) else "🌌 Wallhaven Art"
            if _is_valid_http_url(wh_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=wh_link))

        if getattr(config, "ARENA_ENABLED", False) and _is_valid_http_url(arena_url):
            a_slug = getattr(config, "ARENA_CHANNEL_SLUG", "")
            fallback_arena = f"https://www.are.na/channel/{a_slug}" if a_slug else "https://www.are.na/manoj-muthelyrics"
            a_link = arena_url if _is_valid_http_url(arena_url) else fallback_arena
            if _is_valid_http_url(a_link):
                platform_btns.append(InlineKeyboardButton("🔮 View on Are.na", url=a_link))

        if getattr(config, "PIXELFED_ENABLED", False) and _is_valid_http_url(pixelfed_url):
            inst = getattr(config, "PIXELFED_INSTANCE_URL", "https://pixelfed.social")
            fallback_pix = f"{inst}/PinterestAutomationBot"
            pix_link = pixelfed_url if _is_valid_http_url(pixelfed_url) else fallback_pix
            btn_txt = "📷 View on Pixelfed" if (pixelfed_url and "/p/" in pixelfed_url) else "📷 Pixelfed Profile"
            if _is_valid_http_url(pix_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=pix_link))

        if getattr(config, "IMGHIPPO_ENABLED", False) and _is_valid_http_url(imghippo_url):
            fallback_hippo = "https://www.imghippo.com/dashboard"
            hippo_link = imghippo_url if _is_valid_http_url(imghippo_url) else fallback_hippo
            btn_txt = "🦛 View on Imghippo" if (imghippo_url and "/files/" in imghippo_url) else "🦛 Imghippo Gallery"
            if _is_valid_http_url(hippo_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=hippo_link))

        if getattr(config, "DEVIANTART_ENABLED", False):
            fallback_da = "https://www.deviantart.com/muthelyrics"
            da_link = deviantart_url if _is_valid_http_url(deviantart_url) else fallback_da
            btn_txt = "🎨 View on DeviantArt" if (deviantart_url and "deviantart.com" in deviantart_url and deviantart_url != fallback_da) else "🎨 DeviantArt Gallery"
            if _is_valid_http_url(da_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=da_link))

        if getattr(config, "IMGBB_ENABLED", False):
            fallback_ibb = "https://muthelyrics.imgbb.com/"
            ibb_link = imgbb_url if _is_valid_http_url(imgbb_url) else fallback_ibb
            btn_txt = "🖼️ View on ImgBB" if (imgbb_url and "ibb.co" in imgbb_url and imgbb_url != fallback_ibb) else "🖼️ ImgBB Gallery"
            if _is_valid_http_url(ibb_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=ibb_link))

        if getattr(config, "IMAGEKIT_ENABLED", False):
            fallback_ik = getattr(config, "IMAGEKIT_URL_ENDPOINT", "https://ik.imagekit.io/zno3dgy8z")
            ik_link = imagekit_url if _is_valid_http_url(imagekit_url) else fallback_ik
            btn_txt = "🖼️ View on ImageKit" if (imagekit_url and "ik.imagekit.io" in imagekit_url and imagekit_url != fallback_ik) else "🖼️ ImageKit CDN"
            if _is_valid_http_url(ik_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=ik_link))

        if getattr(config, "IMGPILE_ENABLED", False):
            fallback_ip = "https://imgpile.com"
            ip_link = imgpile_url if _is_valid_http_url(imgpile_url) else fallback_ip
            btn_txt = "🗄️ View on ImgPile" if (imgpile_url and "imgpile.com" in imgpile_url and imgpile_url != fallback_ip) else "🗄️ ImgPile Gallery"
            if _is_valid_http_url(ip_link):
                platform_btns.append(InlineKeyboardButton(btn_txt, url=ip_link))


        # Append platforms in pairs of 2
        for i in range(0, len(platform_btns), 2):
            rows.append(platform_btns[i:i+2])

        # Control actions
        rows.append([
            InlineKeyboardButton("🚀 Post Next Now", callback_data="btn_postnow"),
            InlineKeyboardButton("📋 View Queue", callback_data="btn_queue"),
        ])

        return InlineKeyboardMarkup(rows)
    except Exception as _kb_err:
        logger.warning(f"[TG BOT] Failed to build confirmation keyboard: {_kb_err}")
        return None


def format_crosspost_status_block(results: dict) -> str:
    """
    Formats the cross-post syndication status block for Telegram notifications.
    Displays ONLY active/enabled platforms with status indicators (✅/❌).
    Completely omits disabled platforms and unattempted/skipped platforms (status None, no '—').
    """
    import config

    platforms_meta = [
        ("bluesky",    "BLUESKY_ENABLED",    "🦋 Bluesky"),
        ("raindrop",   "RAINDROP_ENABLED",   "💧 Raindrop"),
        ("mastodon",   "MASTODON_ENABLED",   "🐘 Mastodon"),
        ("freeimage",  "FREEIMAGE_ENABLED",  "🖼️ Freeimage"),
        ("wallhaven",  "WALLHAVEN_ENABLED",  "🌌 Wallhaven"),
        ("arena",      "ARENA_ENABLED",      "🔮 Are.na"),
        ("pixelfed",   "PIXELFED_ENABLED",   "📷 Pixelfed"),
        ("imghippo",   "IMGHIPPO_ENABLED",   "🦛 Imghippo"),
        ("deviantart", "DEVIANTART_ENABLED", "🎨 DeviantArt"),
        ("imgbb",      "IMGBB_ENABLED",      "🖼️ ImgBB"),
        ("imagekit",   "IMAGEKIT_ENABLED",   "🖼️ ImageKit"),
        ("imgpile",    "IMGPILE_ENABLED",    "🗄️ ImgPile"),
    ]


    active_items = []
    for key, config_flag, label in platforms_meta:
        # Check 1: Must be enabled in config
        if not getattr(config, config_flag, False):
            continue

        # Check 2: Must have an active execution result (True or False)
        status = results.get(f"{key}_ok")
        if status is True:
            active_items.append(f"{label} ✅")
        elif status is False:
            active_items.append(f"{label} ❌")
        # If status is None: completely omit!

    if not active_items:
        return ""

    grouped_lines = []
    for i in range(0, len(active_items), 3):
        grouped_lines.append("  ".join(active_items[i:i+3]))
    return f"\n{'─' * 26}\n" + "\n".join(grouped_lines)


# -- Command handlers ---------------------------------------------------------

async def cmd_start(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    chat_id = update.effective_chat.id
    configured = os.getenv("TELEGRAM_ADMIN_CHAT_ID") or _state.get("admin_chat_id")
    if configured and str(configured) != str(chat_id):
        logger.warning(f"[TG BOT] Rejected /start from non-admin chat_id={chat_id}")
        return
    await update.message.reply_text(
        f"Welcome to Animanoizing Bot!\n\n"
        f"Your Chat ID: {chat_id}\n\n"
        f"Add this to your .env and Render:\n"
        f"TELEGRAM_ADMIN_CHAT_ID={chat_id}\n\n"
        f"Use /help to see all commands."
    )
    logger.info(f"[TG BOT] /start from chat_id={chat_id}")


async def cmd_ping(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    await update.message.reply_text("Pong! Bot is alive and responding.")


async def cmd_help(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    make_status = "CONFIGURED" if os.getenv("MAKE_WEBHOOK_URL") else "NOT SET"
    await update.message.reply_text(
        "Animanoizing Bot - All Commands\n\n"
        "--- INFO ---\n"
        "/status         - Bot status and uptime\n"
        "/organic        - 80/20 Organic Trust Ratio & anti-spam status\n"
        "/doctor         - System health report (Auto: runs every 3 days)\n"
        "/repairlinks    - Audit & repair dead Amazon links (Auto: 1st of month)\n"
        "/stats          - Pins count and queue\n"
        "/digest         - Daily performance & health digest (Auto: 9 PM IST)\n"
        "/summary        - Today's multi-platform report (Auto: 10 PM IST)\n"
        "/dailyreport    - Detailed pin & cross-post report\n"
        "/clicks         - Affiliate clicks & estimated earnings\n"
        "/revenue        - Estimated affiliate revenue dashboard\n"
        "/analytics      - 7-day pins & revenue report\n"
        "/weekly         - Force-send weekly performance report\n"
        "/ab             - A/B title test results & leaderboard\n"
        "/revive         - Re-queue ghost pins (add --dry for preview)\n"
        "/stockstats     - Stock photo upload counts per platform\n"
        "/uptime         - Platform health & circuit breaker status\n"
        "/preview        - Last pin with image\n"
        "/seo [anime]    - Preview Multi-Intent SEO tags & title\n"
        "/topseo         - Top anime keywords driving search traffic\n"
        "/logs           - Recent log output\n"
        "/queue          - Pending queue breakdown\n"
        "/schedule       - Today's posting schedule (Auto: 8 AM IST)\n"
        "/channels       - Monitored channels\n"
        "/ping           - Check bot is alive\n\n"
        "--- CROSS-POSTING & SEO ---\n"
        "/crosspost      - Multi-platform status (All 11 connected platforms)\n"
        "/backlinks      - Backlink SEO Waterfall counts across 11 platforms\n"
        "/radar          - Viral Pin Surge Radar & live trending pins\n"
        "/spinoff <code> - Generate 2 Express Spinoff pins for a viral artwork\n"
        "/arena          - Are.na channel stats & block count\n"
        "/arena_test     - Post test block to Are.na channel\n"
        "/bluesky        - Bluesky profile stats & follower count\n"
        "/bluesky_test   - Post test image to Bluesky feed\n"
        "/raindrop       - Raindrop collection stats & public link\n"
        "/raindrop_test  - Post test bookmark to Raindrop collection\n"
        "/mastodon       - Mastodon account stats & profile link\n"
        "/mastodon_test  - Post test anime to Mastodon feed\n"
        "/pixelfed       - Pixelfed account stats & profile link\n"
        "/pixelfed_test  - Post test photo to Pixelfed feed\n"
        "/freeimage      - Freeimage.host API stats & link\n"
        "/freeimage_test - Post test photo to Freeimage.host\n"
        "/imghippo       - Imghippo API stats & link\n"
        "/imghippo_test  - Post test photo to Imghippo\n"
        "/wallhaven      - Wallhaven.cc stats, profile & collections\n"
        "/wallhaven_search - Search 4K anime wallpapers on Wallhaven\n\n"
        "--- POSTING ---\n"
        "/post_now       - Force-post next pin immediately\n"
        "/scrape         - Scrape channels for new pins immediately\n"
        f"/autopilot      - Toggle auto-post vs approval mode (Webhook: {make_status})\n"
        "/testpost       - Send a test pin right now via webhook\n\n"
        "--- CONTROL ---\n"
        "/pause          - Pause posting\n"
        "/resume         - Resume posting\n"
        "/dryrun         - Toggle dry-run on/off\n"
        "/golive         - Enable real Pinterest posting\n"
        "/clearqueue     - Clear pending queue\n"
        "/fixqueue       - Re-upload stale CDN URLs to permanent host\n"
        "/deadqueue      - View pins that failed all retries (dead-letter)\n"
        "/cleardead      - Clear the dead-letter queue\n\n"
        "--- SETTINGS ---\n"
        "/addchannel @ch - Add source channel\n"
        "/removechannel @ch - Remove channel\n"
        "/setdelay [min] - Set posting delay\n"
        "/setmax [num]   - Set max pins/day\n\n"
        "--- MOBILE UPLOAD ---\n"
        "Send any photo/image directly to this bot to queue it as a Pinterest pin! \U0001f4f2\n"
        "The bot will auto-detect the anime, generate captions, find Amazon link, and queue it.\n"
    )


async def cmd_status(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    msg = update.effective_message
    uptime = datetime.datetime.now() - _state["start_time"]
    h, rem = divmod(int(uptime.total_seconds()), 3600)
    m = rem // 60
    mode   = "DRY RUN" if _state["dry_run"] else "LIVE (posting to Pinterest!)"
    paused = "PAUSED" if _state["is_paused"] else "RUNNING"
    make_url = os.getenv("MAKE_WEBHOOK_URL", "")
    post_method = "Make.com Webhook" if make_url else "Pinterest API"
    auto_label  = "AUTO-PILOT" if _state.get("auto_post", True) else "APPROVAL MODE (tap button)"
    try:
        from database import get_organic_trust_stats
        t_stats = get_organic_trust_stats()
        trust_line = f"Trust    : {t_stats['ratio_pct']}% organic (Streak: {t_stats['monetized_streak']}/4)\n"
    except Exception:
        trust_line = ""

    await msg.reply_text(
        f"Bot Status\n"
        f"{'='*25}\n"
        f"Status   : {paused}\n"
        f"Mode     : {mode}\n"
        f"Method   : {post_method}\n"
        f"Posting  : {auto_label}\n"
        f"Uptime   : {h}h {m}m\n"
        f"Channels : {len(_state['channels'])} monitored\n"
        f"Delay    : {_state['post_delay']} min between pins\n"
        f"Max/day  : {_state['max_per_day']} pins\n"
        f"Today    : {_state['posts_today']} pins posted\n"
        f"{trust_line}"
        f"Queue    : {_state['queue_size']} pending",
        reply_markup=get_queue_keyboard()
    )


async def cmd_stats(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        from database import get_all_time_stats
        db_stats = get_all_time_stats()
        total_db  = db_stats["total"]
        today_db  = db_stats["today"]
        top_anime = db_stats["top_anime"]
        top_str = ""
        for name, cnt in top_anime:
            top_str += f"  • {name or 'Unknown'}: {cnt} pins\n"
    except Exception:
        total_db = today_db = 0
        top_str = "  (not available)"

    try:
        from database import get_organic_trust_stats
        org_stats = get_organic_trust_stats()
        org_line = f"Organic  : {org_stats['organic_posts']} pins ({org_stats['ratio_pct']}% of total)\n"
    except Exception:
        org_line = ""

    pin = _state.get("last_pin")
    last_time = pin["time"] if pin else "None yet"
    await msg.reply_text(
        f"Pin Statistics\n"
        f"{'='*25}\n"
        f"Today    : {today_db} pins\n"
        f"Total    : {total_db} pins\n"
        f"{org_line}"
        f"Queue    : {_state['queue_size']} pending\n"
        f"Last pin : {last_time}\n"
        f"Mode     : {'DRY RUN' if _state['dry_run'] else 'LIVE'}\n\n"
        f"Top Anime:\n{top_str or '  (none yet)'}",
        reply_markup=get_queue_keyboard()
    )


async def cmd_organic(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Display 80/20 Organic Trust Ratio, domain protection telemetry and next pin type."""
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        import config
        from database import get_organic_trust_stats, should_next_pin_be_organic

        stats = get_organic_trust_stats()
        enabled = getattr(config, "ORGANIC_TRUST_ENABLED", True)
        target = int(getattr(config, "ORGANIC_TRUST_RATIO", 0.20) * 100)
        profile_link = getattr(config, "ORGANIC_PIN_LINK", "Pinterest Profile")

        next_organic = should_next_pin_be_organic(getattr(config, "ORGANIC_TRUST_RATIO", 0.20))
        next_tag = "🌿 Organic Pin (Safe Mode)" if next_organic else "💰 Monetized Pin (Affiliate Link)"

        streak = stats["monetized_streak"]
        streak_bar = "🔴" * min(streak, 4) + "⚪" * max(0, 4 - streak)
        status_label = "✅ ACTIVE" if enabled else "⚠️ DISABLED"

        text = (
            f"🌿 80/20 Organic Trust Ratio Engine\n"
            f"{'='*32}\n"
            f"Status       : {status_label}\n"
            f"Target Ratio : {target}% (1 organic pin per 4 affiliate)\n"
            f"Actual Ratio : {stats['ratio_pct']}% organic\n\n"
            f"📊 Lifetime Post Breakdown:\n"
            f"  • Total Pins  : {stats['total_posts']}\n"
            f"  • 🌿 Organic   : {stats['organic_posts']}\n"
            f"  • 💰 Monetized : {stats['monetized_posts']}\n\n"
            f"🛡️ Anti-Spam Streak Guard:\n"
            f"  • Affiliate Streak   : {streak}/4 {streak_bar}\n"
            f"  • Next Auto-Pin Type : {next_tag}\n\n"
            f"📦 Queue Health:\n"
            f"  • Pending Organic Pins : {stats['queue_organic']} in queue\n"
            f"  • Destination Link     : {profile_link}\n\n"
            f"💡 Purpose: Interleaving clean organic pins builds domain trust and authority on Pinterest, "
            f"preventing commercial shadowbans ('ghost pins') and algorithmic downranking."
        )
    except Exception as e:
        text = f"❌ Error loading Organic Trust metrics: {e}"

    await msg.reply_text(text)


async def cmd_rebalance(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Rebalance pin queue to enforce 80/20 organic trust ratio and wrap links with tracker."""
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        from database import rebalance_queue_organic, get_organic_trust_stats
        res = rebalance_queue_organic()
        stats = get_organic_trust_stats()
        text = (
            f"🌿 *Queue Rebalancing Complete*\n\n"
            f"• Rebalanced Pins: `{res['rebalanced']}`\n"
            f"• 🌿 Organic Pins in Queue: `{res['organic_in_queue']}`\n"
            f"• 💰 Monetized Pins in Queue: `{res['monetized_in_queue']}`\n\n"
            f"🛡️ Current Affiliate Streak: `{stats['monetized_streak']}/4`\n"
            f"💡 The queue now strictly interleaves organic profile pins to prevent Pinterest shadowbans."
        )
    except Exception as e:
        text = f"❌ Error rebalancing queue: {e}"
    await msg.reply_text(text, parse_mode="Markdown")


async def cmd_dailyreport(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Send today's detailed multi-platform report on demand."""
    if not _is_admin(update): return
    await _send_daily_report(update.effective_chat.id)


async def cmd_summary(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Send today's multi-platform summary on demand (alias for /dailyreport)."""
    if not _is_admin(update): return
    await _send_daily_report(update.effective_chat.id)


async def cmd_digest(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Generate and send the daily performance & health digest on demand."""
    if not _is_admin(update): return
    msg = update.effective_message
    status_msg = await msg.reply_text("📊 Compiling daily performance digest...")
    try:
        from daily_digest import build_daily_digest_message
        report = build_daily_digest_message()
        await status_msg.edit_text(report, parse_mode="Markdown", reply_markup=get_queue_keyboard())
    except Exception as e:
        logger.error(f"[TG BOT] cmd_digest error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ Failed to generate digest: {e}")


async def cmd_doctor(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Run full system diagnostics and reply with health report."""
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        from doctor import run_full_system_diagnostic, format_health_report
        diag = run_full_system_diagnostic()
        report = format_health_report(diag, is_scheduled=False)
        await msg.reply_text(report, reply_markup=get_queue_keyboard())
        logger.info("[TG BOT] /doctor diagnostic report sent.")
    except Exception as e:
        await msg.reply_text(f"Doctor diagnostic error: {e}")



async def cmd_repairlinks(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Audit all tracked Amazon affiliate links and repair dead ones."""
    if not _is_admin(update): return
    await update.message.reply_text(
        "🔍 Scanning all tracked affiliate links for dead pages...\n"
        "Testing Amazon URLs & checking for 404s. Please wait..."
    )
    try:
        from link_healer import run_link_healing_audit, format_repair_report
        audit = run_link_healing_audit(max_links=50)
        report = format_repair_report(audit, is_monthly=False)
        await update.message.reply_text(report)
        logger.info("[TG BOT] /repairlinks audit completed and report sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error during link repair audit: {e}")


async def cmd_healnow(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Autonomous link redirection & stock healer — audit priority links & heal dead items now."""
    if not _is_admin(update): return
    await update.message.reply_text(
        "🔄 *Autonomous Stock Healer Triggered*\n"
        "Auditing high-traffic affiliate links for dead pages and out-of-stock listings...\n"
        "Testing live Amazon availability. Please wait...",
        parse_mode="Markdown"
    )
    try:
        from link_healer import run_autonomous_stock_healer
        results = run_autonomous_stock_healer(batch_size=25)
        scanned = results.get("total_scanned", 0)
        repaired = results.get("repaired_count", 0)
        healthy = results.get("healthy_count", 0)
        details = results.get("repaired_details", [])

        if repaired > 0:
            lines = [f"  • *{d['anime']}*: {d['title'][:32]}...\n    ↳ _{d['reason']}_ ➜ Healed & Swapped ✅" for d in details[:5]]
            repaired_block = "\n" + "\n".join(lines) + "\n"
        else:
            repaired_block = "\n✨ All scanned priority links are active & in-stock! Zero broken listings found.\n"

        msg = (
            f"🔄 *Autonomous Stock Healer Results*\n"
            f"{'═' * 32}\n"
            f"🗓️ Time       : {results.get('timestamp_ist')} IST\n"
            f"📊 Links Audited : {scanned}\n"
            f"🟢 Active/In-Stock: {healthy}\n"
            f"🔧 Healed/Swapped : *{repaired}*{repaired_block}\n"
            f"💡 All user clicks on `/r/<code>` and `/p/<code>` are automatically hot-swapped in real-time (<15ms) to live products."
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        logger.info(f"[TG BOT] /healnow completed: {repaired}/{scanned} links healed.")
    except Exception as e:
        logger.error(f"[TG BOT] /healnow failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error during stock healing: {e}")


async def cmd_linkstats(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Autonomous Stock Healer & link redirection metrics."""
    if not _is_admin(update): return
    try:
        from database import get_link_healing_stats
        stats = get_link_healing_stats()

        total = stats.get("total_links", 0)
        healed = stats.get("healed_links", 0)
        total_heals = stats.get("total_heals", 0)
        checked = stats.get("checked_links", 0)
        top = stats.get("top_healed", [])

        top_lines = []
        if top:
            for i, item in enumerate(top[:5], 1):
                top_lines.append(f"  {i}. *{item['anime']}* (`{item['code']}`): {item['heals']} heals")
            top_block = "\n🔥 *Top Healed Links*:\n" + "\n".join(top_lines) + "\n"
        else:
            top_block = "\n✨ No healed links recorded yet.\n"

        msg = (
            f"🔗 *Autonomous Link Redirection & Stock Healer Stats*\n"
            f"{'═' * 36}\n"
            f"📦 Total Tracked Links : {total}\n"
            f"🔍 Audited for Health   : {checked}\n"
            f"🩹 Healed Links         : {healed}\n"
            f"🔄 Total Heal Operations: {total_heals}\n"
            f"{top_block}\n"
            f"⚡ *Latency SLA*: Sub-15ms cached hot-swap\n"
            f"⏱️ *Auto-Cycle*: Every 6 hours in background"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[TG BOT] /linkstats failed: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Error loading link stats: {e}")



def country_to_flag(code: str) -> str:
    """Converts 2-letter ISO country code to emoji flag."""
    if not code or len(code) != 2:
        return "🌍"
    try:
        code = code.upper()
        return chr(ord(code[0]) + 127397) + chr(ord(code[1]) + 127397)
    except Exception:
        return "🌍"


async def cmd_clicks(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show detailed affiliate link clicks and estimated revenue metrics."""
    if not _is_admin(update): return
    try:
        from database import get_click_stats
        cstats = get_click_stats()
    except Exception as e:
        await update.message.reply_text(f"Could not load click analytics: {e}")
        return

    top_anime_lines = []
    for i, (name, cnt) in enumerate(cstats["top_anime"], 1):
        top_anime_lines.append(f"  {i}. {name or 'Unknown'}: {cnt} click(s)")
    top_anime_str = "\n".join(top_anime_lines) if top_anime_lines else "  No link clicks recorded yet."

    top_pins_lines = []
    for i, (title, anime, cnt) in enumerate(cstats["top_pins"], 1):
        top_pins_lines.append(f"  • [{anime}] {title[:40]} ({cnt} clicks)")
    top_pins_str = "\n".join(top_pins_lines) if top_pins_lines else "  No pin click data yet."

    top_images_lines = []
    for i, (img_fn, anime, title, cnt) in enumerate(cstats.get("top_images", []), 1):
        display = img_fn[:35] if img_fn else (title[:35] if title else 'Unknown')
        top_images_lines.append(f"  {i}. {display} ({cnt} clicks)")
    top_images_str = "\n".join(top_images_lines) if top_images_lines else "  No image click data yet."

    # Top Visitor Countries breakdown
    top_countries_lines = []
    for i, (cc, cnt) in enumerate(cstats.get("top_countries", []), 1):
        flag = country_to_flag(cc)
        name = "India" if cc == "IN" else ("United States" if cc == "US" else ("Brazil" if cc == "BR" else ("Philippines" if cc == "PH" else cc)))
        top_countries_lines.append(f"  {i}. {flag} {name} ({cc}): {cnt} click(s)")
    top_countries_str = "\n".join(top_countries_lines) if top_countries_lines else "  No country data yet."

    # Estimated revenue (USD from US/Global + INR from India)
    usd_min = cstats.get('est_revenue_usd_min', round(cstats['est_revenue_min'] / 84, 2))
    usd_max = cstats.get('est_revenue_usd_max', round(cstats['est_revenue_max'] / 84, 2))
    est_rev = f"${usd_min:.2f} - ${usd_max:.2f} USD (~₹{cstats['est_revenue_min']} - ₹{cstats['est_revenue_max']} INR)"
    est_orders = f"{cstats['est_orders_min']} - {cstats['est_orders_max']} items"

    tbm = cstats.get("today_by_merchant", {})
    tbm_str = ""
    if tbm:
        tbm_parts = [f"{m.capitalize()}: {cnt}" for m, cnt in tbm.items()]
        tbm_str = f" ({', '.join(tbm_parts)})"

    by_m = cstats.get("by_merchant", {})
    by_m_str = ""
    if by_m:
        by_m_parts = [f"  • {m.capitalize()}: {cnt} click(s)" for m, cnt in by_m.items()]
        by_m_str = f"\n\n🏪 Clicks by Merchant (All-Time):\n" + "\n".join(by_m_parts)

    msg = (
        f"📊 Affiliate Clicks & Earnings Report\n"
        f"{'═' * 30}\n"
        f"🖱️ Clicks Today    : {cstats['today']}{tbm_str}\n"
        f"📈 Clicks This Week : {cstats['week']}\n"
        f"🌐 All-Time Clicks  : {cstats['total']}"
        f"{by_m_str}\n\n"
        f"💰 Estimated Performance (7 Days):\n"
        f"  • Estimated Orders  : {est_orders}\n"
        f"  • Estimated Revenue : {est_rev}\n\n"
        f"🌍 Top Visitor Countries:\n{top_countries_str}\n\n"
        f"🎌 Top Clicked Anime:\n{top_anime_str}\n\n"
        f"🔥 Top Clicked Pins:\n{top_pins_str}\n\n"
        f"🖼️ Top Clicked Images:\n{top_images_str}\n\n"
        f"💡 Dual-Routing Active: Indian visitors earn in ₹ (Amazon.in); US/Global earn in $ (Amazon.com)."
    )
    await update.message.reply_text(msg)
    logger.info("[TG BOT] /clicks report sent.")


async def cmd_verify_public(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🔍 Ghost Pin Inspector — check if a Pinterest pin is visible to the public.
    Usage: /verify_public <pin_id>
    Example: /verify_public 1234567890123456
    The pin_id is the long number in the Pinterest pin URL:
      https://www.pinterest.com/pin/<pin_id>/
    """
    if not _is_admin(update): return
    args = context.args if context.args else []
    if not args:
        await update.message.reply_text(
            "🔍 *Ghost Pin Inspector*\n\n"
            "Checks if a Pinterest pin is actually visible to the public "
            "(other accounts, unauthenticated visitors).\n\n"
            "*Usage:* `/verify_public <pin_id>`\n"
            "*Example:* `/verify_public 1234567890123456`\n\n"
            "Find your pin ID in the URL:\n"
            "`https://pinterest.com/pin/PIN_ID_HERE/`",
            parse_mode="Markdown",
        )
        return

    pin_id = args[0].strip().rstrip("/")
    # Accept full URL too
    if "pinterest.com/pin/" in pin_id:
        import re as _re
        m = _re.search(r'/pin/(\d+)', pin_id)
        if m:
            pin_id = m.group(1)

    if not pin_id.isdigit():
        await update.message.reply_text(
            "\u274c Invalid pin ID. Please provide the numeric ID from the Pinterest URL.\n"
            "Example: `/verify_public 1234567890123456`",
            parse_mode="Markdown",
        )
        return

    pin_url = f"https://www.pinterest.com/pin/{pin_id}/"
    wait_msg = await update.message.reply_text(
        f"🔍 Checking public visibility of pin `{pin_id}`...\n"
        f"This takes ~30 seconds (letting Pinterest index first).",
        parse_mode="Markdown",
    )
    try:
        import asyncio
        from ghost_pin_checker import check_pin_by_id
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: check_pin_by_id(pin_id, expected_title="")
        )

        status_icons = {
            "public":     "✅",
            "ghosted":    "👻",
            "login_wall": "🔒",
            "not_found":  "❓",
            "error":      "⚠️",
        }
        icon = status_icons.get(result["status"], "⚠️")

        reply = (
            f"{icon} *Ghost Pin Check Result*\n\n"
            f"🔗 [View Pin]({pin_url})\n"
            f"📊 Status: `{result['status']}`\n"
            f"💬 {result['message']}\n\n"
        )

        if not result["visible"]:
            reply += (
                f"🛠️ *Fix Options:*\n"
                f"1. Set board to *Public* (not Secret)\n"
                f"2. Use bridge link `/p/<code>` instead of direct Amazon URL\n"
                f"3. Re-pin manually from browser to reset the filter\n"
                f"4. Wait 24\u201372h for new account indexing\n"
                f"5. Check that Make.com scenario ran successfully"
            )
        else:
            reply += "🎉 Pin is visible to everyone \u2014 no action needed!"

        await wait_msg.edit_text(reply, parse_mode="Markdown")
        logger.info(f"[TG BOT] /verify_public: pin {pin_id} status={result['status']}")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ghost check error: {e}")
        logger.error(f"[TG BOT] verify_public error: {e}")


async def cmd_bridge_url(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🌉 Generate a bridge landing page URL for an affiliate code.
    Usage: /bridge_url <code>
    Shows what URL to put in a Pinterest pin instead of the raw Amazon link.
    """
    if not _is_admin(update): return
    from config import APP_BASE_URL
    if not APP_BASE_URL:
        await update.message.reply_text(
            "⚠️ APP_BASE_URL is not set in .env.\n"
            "Set it to your Render URL (e.g. https://your-bot.onrender.com) to use bridge pages."
        )
        return

    args = context.args if context.args else []
    if args:
        code = args[0].strip()
        bridge = f"{APP_BASE_URL}/p/{code}"
        await update.message.reply_text(
            f"🌉 *Bridge Landing Page*\n\n"
            f"Code: `{code}`\n"
            f"URL: {bridge}\n\n"
            f"Put this URL in your Pinterest pin instead of the direct Amazon link.\n"
            f"Pinterest sees your own domain \u2014 no affiliate flag!",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            f"🌉 *Bridge Landing Page Info*\n\n"
            f"Your base URL: `{APP_BASE_URL}`\n"
            f"Bridge pattern: `{APP_BASE_URL}/p/<code>`\n\n"
            f"Pins now automatically use bridge URLs when APP_BASE_URL is set.\n"
            f"Example: `{APP_BASE_URL}/p/dp_B0CHR8R1L3`\n\n"
            f"💡 Tip: Pinterest\'s algorithm treats your Render domain as a legit website,"
            f" completely bypassing affiliate link detection.",
            parse_mode="Markdown",
        )


async def cmd_pinanalytics(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    📈 Pinterest Analytics Feedback Loop
    Shows real impressions, saves, and click-through performance from Pinterest API.
    Usage: /pinanalytics [days] (default: 7 days)
    """
    if not _is_admin(update): return
    args = context.args if context.args else []
    days = 7
    try:
        if args:
            days = max(1, min(30, int(args[0])))
    except (ValueError, IndexError):
        pass

    wait_msg = await update.message.reply_text(
        f"📁 Fetching {days}-day funnel analytics...", parse_mode="Markdown"
    )
    try:
        from pinterest_analytics import get_analytics_summary, get_top_pins, get_hourly_pattern
        summary  = get_analytics_summary(days=days)
        top_pins = get_top_pins(days=days, metric="visits", limit=3)
        peak_hrs = get_hourly_pattern(days=days)

        SEP = "─" * 32
        lines = [
            f"📊 *Pinterest Funnel — Last {days} Days*",
            SEP,
            f"👁️  Bridge visits:   {summary['bridge_visits']:,}",
            f"🌐 From Pinterest:  {summary['pinterest_visits']:,}  ({summary['pint_rate']}% of visits)",
            f"🛍️ Amazon clicks:   {summary['amazon_clicks']:,}",
            f"📈 Click-through:   {summary['ctr']}% CTR",
            SEP,
            f"📅 Today — Visits: {summary['today_visits']} | Clicks: {summary['today_clicks']}",
            SEP,
        ]

        if summary["top_anime"]:
            lines.append("🏆 *Top Anime by Bridge Visits:*")
            for i, (anime, visits, pint_v) in enumerate(summary["top_anime"], 1):
                lines.append(f"  {i}. {anime}: {visits} visits ({pint_v} from Pinterest)")

        if top_pins:
            lines.append("\n🔖 *Top Pins (Visits → Clicks):*")
            for i, pin in enumerate(top_pins, 1):
                lines.append(
                    f"  {i}. [{pin['anime']}] {pin['title'][:35]}…"
                    f" | {pin['visits']} visits → {pin['clicks']} clicks ({pin['ctr']}%)"
                )

        if peak_hrs:
            peak_str = ", ".join(f"{h:02d}:00 IST ({c})" for h, c in peak_hrs[:3])
            lines.append(f"\n⏰ *Peak Hours:* {peak_str}")

        if summary["bridge_visits"] == 0:
            lines.append(
                "\n⚠️ No visits yet.\n"
                "Bridge pages /p/<code> collect analytics automatically.\n"
                "Make sure APP_BASE_URL is set in .env so pins use bridge links."
            )

        await wait_msg.edit_text("\n".join(lines), parse_mode="Markdown")
        logger.info(f"[TG BOT] /pinanalytics report sent ({days} days).")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Analytics error: {e}")
        logger.error(f"[TG BOT] pinanalytics error: {e}")


async def cmd_topseo(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🔍 Pinterest SEO Intelligence Report
    Shows which anime keywords are driving clicks + their trending search terms.
    Usage: /topseo
    """
    if not _is_admin(update): return
    try:
        from keyword_seo import get_top_performing_keywords, get_keyword_cache_stats
        top_kws = get_top_performing_keywords(limit=8)
        cache   = get_keyword_cache_stats()

        SEP = "─" * 32
        lines = [
            f"🔍 *Pinterest SEO Intelligence*",
            SEP,
            f"📊 Keyword cache: {cache['fresh']} fresh | {cache['stale']} stale | {cache['total_cached']} total",
            SEP,
        ]

        if top_kws:
            lines.append(f"🏆 *Top Anime by Affiliate Clicks + SEO Keywords:*")
            for i, item in enumerate(top_kws, 1):
                kw_str = " | ".join(item["keywords"][:2]) if item["keywords"] else "(no keywords cached)"
                lines.append(
                    f"  {i}. *{item['anime']}* ({item['clicks']} clicks)\n"
                    f"     Keywords: {kw_str}"
                )
        else:
            lines.append(
                "⚠️ No data yet \u2014 keywords populate as pins are posted.\n"
                "Affiliate clicks tracked via /clicks command."
            )

        lines += [
            SEP,
            f"💡 *How it works:*",
            f"Each pin title is automatically enriched with trending Pinterest search terms.",
            f"Keywords are fetched live from Pinterest's autocomplete API and cached 24h.",
        ]

        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        logger.info("[TG BOT] /topseo report sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ SEO report error: {e}")
        logger.error(f"[TG BOT] topseo error: {e}")


async def cmd_seo(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🔍 Multi-Intent SEO Matrix Inspector & Generator
    Tests and previews the balanced Pinterest search tags, title, and description hook for any anime.
    Usage:
      /seo
      /seo Demon Slayer
      /seo Jujutsu Kaisen - Gojo
    """
    if not _is_admin(update): return
    try:
        from seo_matrix import (
            generate_multi_intent_tags,
            build_multi_intent_title,
            build_multi_intent_description,
        )

        args = context.args or []
        query = " ".join(args).strip()
        anime_name = "Demon Slayer"
        character_name = ""

        if query:
            if " - " in query:
                parts = query.split(" - ", 1)
                anime_name = parts[0].strip()
                character_name = parts[1].strip()
            elif "," in query:
                parts = query.split(",", 1)
                anime_name = parts[0].strip()
                character_name = parts[1].strip()
            else:
                anime_name = query
        else:
            last_pin = _state.get("last_pin")
            if last_pin and last_pin.get("anime_name"):
                anime_name = last_pin["anime_name"]

        seo_title = build_multi_intent_title("Masterpiece Artwork", anime_name, character_name)
        tags = generate_multi_intent_tags(anime_name, character_name, target_count=13, include_disclosure=True)
        sample_desc = build_multi_intent_description(
            base_description="Incredible hand-crafted anime illustration featuring legendary character artwork.",
            anime_name=anime_name,
            character_name=character_name,
            link_url="https://your-bot.onrender.com/p/dp_SAMPLE",
        )

        SEP = "─" * 32
        subject_display = f"{anime_name} ({character_name})" if character_name else anime_name
        msg = (
            f"🔍 *Pinterest Multi-Intent SEO Matrix*\n"
            f"{SEP}\n"
            f"🎌 *Target Anime:* {subject_display}\n\n"
            f"📌 *SEO Title (First 30 Chars Weighted):*\n"
            f"`{seo_title}` ({len(seo_title)}/100 chars)\n\n"
            f"📝 *Description Hook (First 150 Chars):*\n"
            f"{sample_desc.split(chr(10))[0]}\n\n"
            f"🏷️ *Balanced Multi-Intent Tag Matrix (13 Tags):*\n"
            f"• 🖼️ *Wallpaper/4K:* `#AnimeWallpaper4K #PhoneWallpaper4K`\n"
            f"• 🖼️ *Decor/Displate:* `#AnimeRoomDecor #MetalPosterArt`\n"
            f"• 🛍️ *Merch/Amazon:* `#AnimeFigure #AnimeMerch`\n"
            f"• ⚔️ *Fandom/Character:* `#{anime_name.replace(' ', '')}`\n\n"
            f"✨ *Full Generated Cluster:*\n"
            f"`{tags}`\n\n"
            f"{SEP}\n"
            f"💡 *Tip:* Test any anime using `/seo <Anime> - <Character>` (e.g. `/seo Naruto - Sasuke`)."
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        logger.info(f"[TG BOT] /seo preview sent for '{subject_display}'")
    except Exception as e:
        await update.message.reply_text(f"❌ SEO preview error: {e}")
        logger.error(f"[TG BOT] cmd_seo error: {e}")


async def cmd_backlinks(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🌐 Backlink SEO Waterfall Report
    Displays total external backlinks generated across Bluesky, Mastodon, Raindrop,
    Are.na, Pixelfed, Freeimage, and Imghippo pointing to Pinterest and the Bridge Page.
    Usage: /backlinks
    """
    if not _is_admin(update): return
    try:
        from database import get_backlink_stats
        import config
        stats = get_backlink_stats()
        total = stats.get("total", 0)
        today = stats.get("today", 0)
        by_platform = stats.get("by_platform", {})

        profile_url = getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
        SEP = "─" * 32

        platform_names = {
            "bluesky":   ("Bluesky", "DA 90+"),
            "mastodon":  ("Mastodon", "DA 85+"),
            "raindrop":  ("Raindrop.io", "DA 80+"),
            "pixelfed":  ("Pixelfed", "DA 75+"),
            "arena":     ("Are.na", "DA 75+"),
            "freeimage": ("Freeimage", "DA 65+"),
            "imghippo":  ("Imghippo", "DA 60+"),
        }

        breakdown_lines = []
        for key, (name, da) in platform_names.items():
            cnt = by_platform.get(key, 0)
            breakdown_lines.append(f"  • {name} ({da}): {cnt} backlinks")

        breakdown_str = "\n".join(breakdown_lines)

        msg = (
            f"🌐 *Backlink SEO Waterfall Report*\n"
            f"{SEP}\n"
            f"📊 *Total Backlinks Created:* {total}\n"
            f"📅 *Created Today (IST):* {today}\n\n"
            f"🚀 *Syndication Platform Breakdown:*\n"
            f"{breakdown_str}\n\n"
            f"🎯 *Target Assets Boosted:*\n"
            f"  • 📌 Pinterest: `{profile_url}`\n"
            f"  • 🖼️ Multi-Merchant Bridge: Active on all cross-posts\n\n"
            f"{SEP}\n"
            f"💡 *Google SEO Benefit:* External links from Bluesky, Raindrop, and Mastodon "
            f"pass domain citation authority, helping Google index and rank your Pinterest pins."
        )

        await update.message.reply_text(msg, parse_mode="Markdown")
        logger.info("[TG BOT] /backlinks report sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Backlinks report error: {e}")
        logger.error(f"[TG BOT] cmd_backlinks error: {e}")


async def cmd_radar(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🚨 Check real-time viral surges across all Pinterest bridge pins.
    Usage: /radar or /viral
    """
    if not _is_admin(update): return
    try:
        from viral_radar import format_radar_leaderboard, detect_viral_surges
        msg = format_radar_leaderboard(limit=5)
        candidates = detect_viral_surges(window_hours=24, min_visits=1, check_cooldown=False)
        buttons = []
        if candidates:
            for c in candidates[:3]:
                code = c["code"]
                anime = c["anime_name"]
                buttons.append([
                    InlineKeyboardButton(f"🔥 Spinoff {anime[:14]}", callback_data=f"spinoff:{code}"),
                    InlineKeyboardButton(f"⚡ Express #1", callback_data=f"boost:{code}"),
                ])
        markup = InlineKeyboardMarkup(buttons) if buttons else None
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=markup)
        logger.info("[TG BOT] /radar leaderboard sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Radar error: {e}")
        logger.error(f"[TG BOT] cmd_radar error: {e}")


async def cmd_spinoff(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🔥 Create Express Spinoff pins for a viral artwork code.
    Usage: /spinoff [code]
    Example: /spinoff dp_a1b2c3
    """
    if not _is_admin(update): return
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "ℹ️ *Usage:* `/spinoff <code>`\n\n"
            "Example: `/spinoff dp_test`\n"
            "Use `/radar` to see active trending codes.",
            parse_mode="Markdown"
        )
        return
    code = args[0].strip()
    try:
        from viral_radar import generate_spinoff_pins
        res = generate_spinoff_pins(code, count=2)
        await update.message.reply_text(
            f"🔥 *Spinoff Pins Activated!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎨 Anime: *{res['anime_name']}*\n"
            f"⚡ Boosted from Queue: *{res['boosted']}* (Priority 2 Express)\n"
            f"✨ Generated Fresh: *{res['generated']}* (Priority 2 Express)\n"
            f"🚀 Total Active Spinoffs: *{res['total']}*\n\n"
            f"Scheduled at Express Priority (#1) to ride the active Pinterest surge!",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Spinoff error: {e}")
        logger.error(f"[TG BOT] cmd_spinoff error: {e}")


async def cmd_refreshanalytics(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    🔄 Manually trigger Pinterest analytics refresh from API.
    Usage: /refreshanalytics
    Fetches latest impressions/saves/clicks for all tracked pins.
    """
    if not _is_admin(update): return
    wait_msg = await update.message.reply_text(
        "🔄 Pulling Pinterest analytics from API... (may take 30s)"
    )
    try:
        import asyncio
        from pinterest_analytics import run_analytics_pull
        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_analytics_pull)

        await wait_msg.edit_text(
            f"✅ *Analytics Refresh Complete*\n\n"
            f"📍 Pins checked: {result['pins_checked']}\n"
            f"📅 Days stored:  {result['days_stored']}\n"
            f"❌ Errors:       {result['errors']}\n\n"
            f"Use /pinanalytics to see the full report.",
            parse_mode="Markdown",
        )
        logger.info(f"[TG BOT] /refreshanalytics complete: {result}")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Analytics refresh error: {e}")
        logger.error(f"[TG BOT] refreshanalytics error: {e}")


async def cmd_stockstats(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show stock photography upload stats — today and all-time totals per platform."""

    if not _is_admin(update): return
    try:
        from database import get_stock_stats_today, get_stock_stats_alltime
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        today  = get_stock_stats_today(today_str)
        alltime = get_stock_stats_alltime()

        # Platform display config: name → (emoji, full label)
        platforms = {
            "shutterstock":  ("📸", "Shutterstock"),
            "adobe":         ("🎨", "Adobe Stock"),
            "freepik":       ("🖼️", "Freepik"),
            "depositphotos": ("💼", "Depositphotos"),
            "dreamstime":    ("💭", "Dreamstime"),
            "123rf":         ("🔢", "123RF"),
        }

        lines = [
            f"📊 *Stock Photography Stats*",
            f"{'─' * 30}",
            f"{'Platform':<16} {'Today':>6}  {'All-Time':>9}",
            f"{'─' * 33}",
        ]
        for key, (emoji, label) in platforms.items():
            t = today.get(key, 0)
            a = alltime.get(key, 0)
            today_str_val  = f"{t}" if t > 0 else "—"
            alltime_str = f"{a:,}"
            lines.append(f"{emoji} {label:<14} {today_str_val:>6}  {alltime_str:>9}")

        lines += [
            f"{'─' * 33}",
            f"{'📦 Total':<16} {sum(today.values()):>6}  {alltime.get('total', 0):>9,}",
            f"{'─' * 30}",
            f"💡 Uploads run automatically after each Pinterest post.",
        ]

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown"
        )
        logger.info("[TG BOT] /stockstats report sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error loading stock stats: {e}")
        logger.error(f"[TG BOT] stockstats error: {e}")


async def cmd_uptime(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show platform health: which platforms are live vs cooling down (circuit breaker status)."""
    if not _is_admin(update): return
    try:
        from circuit_breaker import get_all_cooldowns, is_cooling_down

        all_platforms = [
            ("pinterest",  "🖼️", "Pinterest"),
            ("arena",      "🔲", "Are.na"),
            ("bluesky",    "🦋", "Bluesky"),
            ("mastodon",   "🐘", "Mastodon"),
            ("pixelfed",   "📷", "Pixelfed"),
            ("raindrop",   "💧", "Raindrop"),
            ("freeimage",  "🆓", "Freeimage"),
            ("imghippo",   "🦛", "Imghippo"),
        ]

        healthy = []
        cooling = []
        for key, emoji, label in all_platforms:
            is_cool, reason, rem = is_cooling_down(key)
            if is_cool:
                cooling.append(f"  {emoji} *{label}* — ❄️ Cooldown: {rem}h left\n    _{reason[:70]}_")
            else:
                healthy.append(f"  {emoji} {label} ✅")

        lines = [f"🛡️ *Platform Health Report*", f"{'─' * 30}"]

        if healthy:
            lines.append(f"\n🟢 *Healthy ({len(healthy)})*")
            lines.extend(healthy)

        if cooling:
            lines.append(f"\n🔴 *Circuit Breaker Active ({len(cooling)})*")
            lines.extend(cooling)
            lines.append(f"\n💡 Use /resetbreaker to manually clear a cooldown.")
        else:
            lines.append(f"\n_All platforms are posting normally._")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown"
        )
        logger.info("[TG BOT] /uptime report sent.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error loading platform status: {e}")
        logger.error(f"[TG BOT] uptime error: {e}")


async def cmd_analytics(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show 7-day performance analytics: daily counts, affiliate clicks, revenue, top anime."""
    if not _is_admin(update): return
    try:
        from database import get_weekly_stats, get_click_stats
        stats = get_weekly_stats()
        cstats = get_click_stats()
    except Exception as e:
        await update.message.reply_text(f"Could not load analytics: {e}")
        return

    # Build daily chart (simple ASCII bar)
    daily = stats["daily_counts"]
    max_count = max((d["count"] for d in daily), default=1)
    chart_lines = []
    for d in daily:
        bar_len = int((d["count"] / max_count) * 10) if max_count else 0
        bar = "[" + "#" * bar_len + "." * (10 - bar_len) + "]"
        chart_lines.append(f"  {d['date']}: {bar} {d['count']}")
    chart = "\n".join(chart_lines) if chart_lines else "  No pins posted this week yet."

    # Top anime
    top_str = ""
    for i, (name, cnt) in enumerate(stats["top_anime"], 1):
        top_str += f"  {i}. {name or 'Unknown'}: {cnt} pins\n"
    top_str = top_str or "  No data yet."

    # Failed retries warning
    retry_warning = ""
    if stats["failed_retries"] > 0:
        retry_warning = f"\n[!] {stats['failed_retries']} pin(s) have failed uploads in queue.\n"

    est_rev = f"₹{cstats['est_revenue_min']} - ₹{cstats['est_revenue_max']}"

    await update.message.reply_text(
        f"7-Day Analytics & Revenue Report\n"
        f"{'=' * 32}\n"
        f"📌 Pins Posted (Week) : {stats['total_week']}\n"
        f"📌 All-Time Pins      : {stats['total_all_time']}\n\n"
        f"🖱️ Affiliate Clicks (Week): {cstats['week']} (Today: {cstats['today']})\n"
        f"💰 Est. Commission (Week) : {est_rev}\n\n"
        f"Daily Pin Activity:\n{chart}\n\n"
        f"Top Anime Posted This Week:\n{top_str}"
        f"{retry_warning}\n"
        f"Use /clicks for full affiliate link breakdown."
    )
    logger.info("[TG BOT] /analytics report sent.")


async def _send_daily_report(chat_id):
    """Build and send a comprehensive multi-platform daily summary to the given chat_id."""
    if not _app_ref:
        return
    try:
        from database import (
            get_today_uploads, get_all_time_stats,
            get_arena_stats, get_bluesky_stats,
            get_raindrop_stats, get_mastodon_stats, get_pixelfed_stats,
            get_freeimage_stats, get_imghippo_stats, get_deviantart_stats,
            get_imgbb_stats, get_imagekit_stats, get_imgpile_stats, get_wallhaven_stats,
            get_click_stats
        )

        import config
        from config import (
            ARENA_ENABLED, ARENA_CHANNEL_SLUG,
            BLUESKY_ENABLED, BLUESKY_HANDLE,
            RAINDROP_ENABLED, RAINDROP_COLLECTION_ID,
            MASTODON_ENABLED, MASTODON_INSTANCE_URL,
            PIXELFED_ENABLED, PIXELFED_INSTANCE_URL,
            FREEIMAGE_ENABLED)
        # Use IST date for consistent timezone-aware reporting
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_ist_str = now_ist.strftime("%Y-%m-%d")
        today_display = now_ist.strftime("%d %b %Y")

        pins   = get_today_uploads(today_ist_str)
        stats  = get_all_time_stats()
        count  = len(pins)

        # Cross-platform stats
        arena_st       = get_arena_stats(today_ist_str)
        bluesky_st     = get_bluesky_stats(today_ist_str)
        raindrop_st    = get_raindrop_stats(today_ist_str)
        mastodon_st    = get_mastodon_stats(today_ist_str)
        pixelfed_st    = get_pixelfed_stats(today_ist_str)
        freeimage_st   = get_freeimage_stats(today_ist_str)
        imghippo_st    = get_imghippo_stats(today_ist_str)
        deviantart_st  = get_deviantart_stats(today_ist_str)
        imgbb_st       = get_imgbb_stats(today_ist_str)
        imagekit_st    = get_imagekit_stats(today_ist_str)
        imgpile_st     = get_imgpile_stats(today_ist_str)
        wallhaven_st   = get_wallhaven_stats(today_ist_str)


        # Click & earnings stats
        try:
            clicks = get_click_stats(days=1)
            clicks_today = clicks.get("total_clicks", 0)
            earnings_today = clicks.get("estimated_revenue_inr", 0.0)
        except Exception:
            clicks_today = 0
            earnings_today = 0.0

        header = (
            f"🌙 Good Night! Daily Report — {today_display}\n"
            f"⏰ 10:00 PM IST Auto-Report\n"
            f"{'═' * 38}\n\n"
        )

        arena_badge     = "🟢 ON" if ARENA_ENABLED else "⚪ OFF"
        bluesky_badge   = "🟢 ON" if BLUESKY_ENABLED else "⚪ OFF"
        raindrop_badge  = "🟢 ON" if RAINDROP_ENABLED else "⚪ OFF"
        masto_badge     = "🟢 ON" if MASTODON_ENABLED else "⚪ OFF"
        pixelfed_badge  = "🟢 ON" if PIXELFED_ENABLED else "⚪ OFF"
        freeimage_badge = "🟢 ON" if FREEIMAGE_ENABLED else "⚪ OFF"
        imghippo_badge  = "🟢 ON" if getattr(config, "IMGHIPPO_ENABLED", False) else "⚪ OFF"
        deviantart_badge = "🟢 ON" if getattr(config, "DEVIANTART_ENABLED", False) else "⚪ OFF"
        imgbb_badge      = "🟢 ON" if getattr(config, "IMGBB_ENABLED", False) else "⚪ OFF"
        imagekit_badge   = "🟢 ON" if getattr(config, "IMAGEKIT_ENABLED", False) else "⚪ OFF"
        imgpile_badge    = "🟢 ON" if getattr(config, "IMGPILE_ENABLED", False) else "⚪ OFF"
        wallhaven_badge  = "🟢 ON" if getattr(config, "WALLHAVEN_ENABLED", False) else "⚪ OFF" 

        summary_section = (
            f"📊 Platform Posting Summary:\n"
            f"{'─' * 34}\n"
            f"  📌 Pinterest : {count} today  |  {stats['total']} all-time\n"
            f"  🔮 Are.na    : {arena_st['today']} today  |  {arena_st['total']} all-time  ({arena_badge})\n"
            f"  🦋 Bluesky   : {bluesky_st['today']} today  |  {bluesky_st['total']} all-time  ({bluesky_badge})\n"
            f"  💧 Raindrop  : {raindrop_st['today']} today  |  {raindrop_st['total']} all-time  ({raindrop_badge})\n"
            f"  🐘 Mastodon  : {mastodon_st['today']} today  |  {mastodon_st['total']} all-time  ({masto_badge})\n"
            f"  📷 Pixelfed  : {pixelfed_st['today']} today  |  {pixelfed_st['total']} all-time  ({pixelfed_badge})\n"
            f"  🖼️ Freeimage : {freeimage_st['today']} today  |  {freeimage_st['total']} all-time  ({freeimage_badge})\n"
            f"  🦛 Imghippo  : {imghippo_st['today']} today  |  {imghippo_st['total']} all-time  ({imghippo_badge})\n"
            f"  🎨 DeviantArt: {deviantart_st['today']} today  |  {deviantart_st['total']} all-time  ({deviantart_badge})\n"
            f"  🖼️ ImgBB     : {imgbb_st['today']} today  |  {imgbb_st['total']} all-time  ({imgbb_badge})\n"
            f"  🖼️ ImageKit  : {imagekit_st['today']} today  |  {imagekit_st['total']} all-time  ({imagekit_badge})\n"
            f"  🗄️ ImgPile   : {imgpile_st['today']} today  |  {imgpile_st['total']} all-time  ({imgpile_badge})\n"
            f"  🌌 Wallhaven : {wallhaven_st['today']} today  |  {wallhaven_st['total']} all-time  ({wallhaven_badge})\n\n"
        )


        revenue_section = ""
        if clicks_today > 0 or earnings_today > 0:
            revenue_section = (
                f"💰 Affiliate Performance (Today):\n"
                f"  • Clicks: {clicks_today}  |  Est. Revenue: ₹{earnings_today:,.2f}\n\n"
            )

        if count == 0:
            pin_section = "📌 Pinterest Activity:\n  (No pins posted today — queue may be empty)\n\n"
        else:
            from collections import Counter
            anime_counts = Counter(p.get('anime') or 'Unknown' for p in pins)
            top_today = "\n".join(
                f"  • {a}: {c} pin(s)" for a, c in anime_counts.most_common(5)
            )
            lines = [f"  {i}. [{p.get('anime') or 'Unknown'}] {p.get('title') or 'Untitled'}" for i, p in enumerate(pins[:10], 1)]
            more_pins = f"\n  ... and {count - 10} more" if count > 10 else ""
            pin_section = (
                f"📌 Pinterest Activity:\n"
                f"Top Anime:\n{top_today}\n\n"
                f"Recent Pins:\n" + "\n".join(lines) + more_pins + "\n\n"
            )

        crosspost_section = "🌐 Connected Platforms:\n"
        if ARENA_ENABLED:
            crosspost_section += f"  • Are.na: are.na/manoj-muthelyrics/{ARENA_CHANNEL_SLUG}\n"
        if BLUESKY_ENABLED:
            crosspost_section += f"  • Bluesky: https://bsky.app/profile/{BLUESKY_HANDLE}\n"
        if RAINDROP_ENABLED:
            crosspost_section += f"  • Raindrop: https://raindrop.io/muthelyrics/anime-posters-{RAINDROP_COLLECTION_ID}\n"
        if MASTODON_ENABLED:
            crosspost_section += f"  • Mastodon: {MASTODON_INSTANCE_URL}/@muthelyrics\n"
        if PIXELFED_ENABLED:
            crosspost_section += f"  • Pixelfed: {PIXELFED_INSTANCE_URL}\n"
        if FREEIMAGE_ENABLED:
            crosspost_section += "  • Freeimage: https://freeimage.host/muthelyrics\n"
        if getattr(config, "IMGHIPPO_ENABLED", False):
            crosspost_section += "  • Imghippo: https://www.imghippo.com/dashboard\n"
        if getattr(config, "DEVIANTART_ENABLED", False):
            crosspost_section += "  • DeviantArt: https://www.deviantart.com/muthelyrics\n"
        if getattr(config, "IMGBB_ENABLED", False):
            crosspost_section += "  • ImgBB: https://muthelyrics.imgbb.com/\n"
        if getattr(config, "IMAGEKIT_ENABLED", False):
            crosspost_section += "  • ImageKit: https://ik.imagekit.io/zno3dgy8z\n"
        if getattr(config, "IMGPILE_ENABLED", False):
            crosspost_section += "  • ImgPile: https://imgpile.com\n"

        if getattr(config, "WALLHAVEN_ENABLED", False):
            crosspost_section += "  • Wallhaven: https://wallhaven.cc/user/animizing\n"
        crosspost_section += "\n👉 Use /crosspost for live platform diagnostics & testing."

        msg = header + summary_section + revenue_section + pin_section + crosspost_section

        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await _app_ref.bot.send_message(chat_id=chat_id, text=chunk)
        logger.info(f"[TG BOT] Multi-platform daily report sent to {chat_id}")

    except Exception as e:
        logger.error(f"[TG BOT] Daily report error: {e}", exc_info=True)



async def _send_daily_morning_schedule(chat_id: str):
    """
    Sends today's scheduled posting plan and upcoming pins to the admin at 8:00 AM IST.
    """
    try:
        from database import get_queue_counts, get_queue_detail, get_upcoming_queued_pins
        from scheduler import get_upcoming_slot_times

        counts = get_queue_counts()
        total = counts["total"]
        details = get_queue_detail()
        upcoming = get_upcoming_queued_pins(limit=5)

        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%d %b %Y")

        slot_times = []
        try:
            slot_times = get_upcoming_slot_times(count=max(len(upcoming), 1))
        except Exception:
            pass

        upcoming_lines = []
        for i, p in enumerate(upcoming):
            p_type = "NEW" if p["priority"] == 1 else "BACKLOG"
            time_label = slot_times[i] if i < len(slot_times) else f"{p['scheduled_date']} ({p_type})"
            upcoming_lines.append(
                f"{i+1}. 🌸 {p['title']}\n"
                f"   🎌 Anime: {p['anime_name']}\n"
                f"   ⏰ Scheduled: {time_label}"
            )
        upcoming_text = "\n\n".join(upcoming_lines) if upcoming_lines else "None queued yet."

        lines = []
        for d in details:
            parts = []
            if d["new"] > 0:
                parts.append(f"{d['new']} NEW")
            if d["backlog"] > 0:
                parts.append(f"{d['backlog']} BACKLOG")
            lines.append(f"  • {d['date']}: {', '.join(parts)}")
        breakdown = "\n".join(lines) if lines else "  • No dates scheduled"

        msg = (
            f"☀️ Good Morning! Daily Pinterest Posting Schedule\n"
            f"📅 {today_str} • 08:00 AM IST\n"
            f"{'═' * 32}\n\n"
            f"📊 Queue Status: {total} pin(s) ready\n"
            f"  {counts['new']} NEW  |  {counts['backlog']} BACKLOG\n\n"
            f"📌 Today's Scheduled Pins & Times:\n"
            f"{upcoming_text}\n\n"
            f"📅 Daily Distribution:\n{breakdown}\n\n"
            f"👉 Use /post_now to publish pin #1 immediately\n"
            f"👉 Use /scrape to check channels for fresh posts\n"
            f"👉 Use /queue to inspect full queue anytime"
        )

        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await _app_ref.bot.send_message(
                chat_id=chat_id, text=chunk, reply_markup=get_queue_keyboard()
            )
        logger.info(f"[TG BOT] 8:00 AM Morning schedule sent to {chat_id}")
    except Exception as e:
        logger.error(f"[TG BOT] 8:00 AM schedule report error: {e}", exc_info=True)


def _start_daily_summary_thread(token: str, admin_chat_id: str):
    """
    Background thread that sends:
      • 8:00 AM IST (02:30 UTC): Today's Schedule & upcoming pins
      • 10:00 AM IST (04:30 UTC): Automated 3-Day Health Check
      • 10:30 AM IST (05:00 UTC): Monthly Self-Healing Link Audit
      • 10:00 PM IST (16:30 UTC): Nightly daily summary (all 5 platforms)
    Dynamically reads admin_chat_id from _state so it always finds the correct chat.
    """
    def _get_chat_id():
        """Resolve the admin chat ID at send time — never uses a stale value."""
        return (
            _state.get("admin_chat_id")          # set when user sends /start
            or admin_chat_id                      # passed at startup from config
            or os.getenv("TELEGRAM_ADMIN_CHAT_ID")  # final env var fallback
        )

    def _loop():
        logger.info("[TG BOT] Daily summary & 8 AM Schedule scheduler started.")
        sent_today = None
        sent_schedule_today = None
        sent_health_today = None    # BUG-FIX: prevent repeated health-check firing
        sent_repair_today = None    # BUG-FIX: prevent repeated link-repair firing
        # BUG-FIX: Track if the 8 AM window was missed while _loop_ref was None,
        # so we can send it as soon as the bot is ready (within the same UTC day).
        missed_schedule_utc_date = None
        while True:
            now_utc = datetime.datetime.utcnow()

            # 08:00 AM IST = 02:30 UTC -> Daily Morning Schedule
            # BUG-FIX: Widened from ±2 min (28-32) to ±10 min (20-40) so that the
            # 20-second _loop_ref initialization delay cannot silently skip the window.
            # Root cause of missing morning messages on restart days.
            if now_utc.hour == 2 and 20 <= now_utc.minute <= 40 and now_utc.date() != sent_schedule_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    sent_schedule_today = now_utc.date()
                    missed_schedule_utc_date = None  # cleared — no longer missed
                    logger.info(f"[TG BOT] Sending 8:00 AM daily schedule to {chat_id}...")
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_morning_schedule(chat_id), _loop_ref
                    )
                else:
                    # BUG-FIX: Mark as missed so catch-up fires when bot becomes ready.
                    missed_schedule_utc_date = now_utc.date()
                    logger.warning(
                        "[TG BOT] 8 AM schedule window hit but bot not ready yet "
                        "(_app_ref/_loop_ref not set) — will send as soon as ready."
                    )

            # BUG-FIX: Missed-window catch-up: if the 8 AM window passed while the
            # bot was still initializing, send the morning message as soon as possible
            # (within the same UTC day, before 10:00 UTC = 3:30 PM IST).
            if (
                missed_schedule_utc_date == now_utc.date()
                and now_utc.date() != sent_schedule_today
                and now_utc.hour < 10  # only catch up before 3:30 PM IST
                and _app_ref and _loop_ref
            ):
                chat_id = _get_chat_id()
                if chat_id:
                    sent_schedule_today = now_utc.date()
                    missed_schedule_utc_date = None
                    logger.info(
                        f"[TG BOT] Sending missed 8:00 AM morning schedule (catch-up) to {chat_id}..."
                    )
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_morning_schedule(chat_id), _loop_ref
                    )

            # 10:00 PM IST = 16:30 UTC -> Daily Nightly Summary Report
            # At 10 PM IST all 5 posting slots (9AM/1PM/4PM/6PM/8PM IST) are fully done.
            # BUG-FIX: Use ±2 min window + add missing _loop_ref guard (was crashing if loop not ready)
            if now_utc.hour == 16 and 28 <= now_utc.minute <= 32 and now_utc.date() != sent_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:  # BUG-FIX: was missing _loop_ref check
                    sent_today = now_utc.date()
                    logger.info(f"[TG BOT] Sending nightly daily report to {chat_id} (10 PM IST)...")
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_report(chat_id), _loop_ref
                    )
                else:
                    logger.warning("[TG BOT] Nightly report skipped — admin chat ID not set yet.")

            # 10:00 AM IST = 04:30 UTC -> Automated 3-Day Health Check
            # BUG-FIX: Use ±2 min window + sent_health_today guard to prevent repeated firing
            if now_utc.hour == 4 and 28 <= now_utc.minute <= 32 and now_utc.date() != sent_health_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    sent_health_today = now_utc.date()
                    try:
                        from doctor import check_and_run_scheduled_health_check
                        check_and_run_scheduled_health_check(_app_ref, _loop_ref, chat_id)
                    except Exception as e:
                        logger.error(f"[TG BOT] Scheduled health check error: {e}")

            # 10:30 AM IST = 05:00 UTC -> Automated Monthly Self-Healing Link Audit (1st of month)
            # BUG-FIX: Use ±2 min window + sent_repair_today guard to prevent repeated firing
            if now_utc.hour == 5 and 0 <= now_utc.minute <= 4 and now_utc.date() != sent_repair_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    sent_repair_today = now_utc.date()
                    try:
                        from link_healer import check_and_run_monthly_repair
                        check_and_run_monthly_repair(_app_ref, _loop_ref, chat_id)
                    except Exception as e:
                        logger.error(f"[TG BOT] Scheduled monthly link repair error: {e}")

            time.sleep(45)  # BUG-FIX: reduced from 55s to 45s for tighter timing within the ±2 min window

    t = threading.Thread(target=_loop, daemon=True, name="DailySummary")
    t.start()

    # Startup health check (runs 45s after bot boots up if 3 days have passed)
    def _startup_health_check():
        time.sleep(45)
        chat_id = _get_chat_id()
        if _app_ref and chat_id and _loop_ref:
            try:
                from doctor import check_and_run_scheduled_health_check
                check_and_run_scheduled_health_check(_app_ref, _loop_ref, chat_id)
            except Exception as e:
                logger.debug(f"[TG BOT] Startup health check note: {e}")

    threading.Thread(target=_startup_health_check, daemon=True, name="StartupHealth").start()


async def cmd_preview(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    pin = _state.get("last_pin")
    if not pin:
        await update.message.reply_text(
            "No pins generated yet.\n"
            "Waiting for next scrape cycle (~10 min)."
        )
        return
    caption = (
        f"Last Generated Pin\n"
        f"{'='*25}\n"
        f"Anime  : {pin['anime']}\n"
        f"Title  : {pin['title']}\n"
        f"Time   : {pin['time']}\n\n"
        f"{pin['description'][:350]}\n\n"
        f"Link: {pin['link']}"
    )
    try:
        img = pin.get("image_path")
        if img and os.path.exists(img):
            with open(img, "rb") as f:
                await update.message.reply_photo(photo=f, caption=caption[:1024])
            return
    except Exception:
        pass
    await update.message.reply_text(caption)


async def cmd_logs(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    try:
        log_file = os.path.join("logs", "bot.log")
        if not os.path.exists(log_file):
            await update.message.reply_text("No log file found yet.")
            return
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        last_lines = lines[-15:]
        log_text = "".join(last_lines).strip()
        # Truncate for Telegram's 4096 char limit
        if len(log_text) > 3800:
            log_text = "..." + log_text[-3800:]
        await update.message.reply_text(f"Recent Logs:\n\n{log_text}")
    except Exception as e:
        await update.message.reply_text(f"Could not read logs: {e}")


async def cmd_channels(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    channels = _state.get("channels", [])
    if not channels:
        await update.message.reply_text(
            "No channels configured.\n"
            "Use /addchannel @channel_name to add one."
        )
        return
    ch_list = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(channels))
    await update.message.reply_text(
        f"Monitored Channels ({len(channels)}):\n{ch_list}\n\n"
        f"Add: /addchannel @name\n"
        f"Remove: /removechannel @name"
    )


async def cmd_addchannel(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /addchannel @channel_name\n"
            "Example: /addchannel @Anime_Naruto_Art_AOT"
        )
        return
    channel = args[0]
    if not channel.startswith("@"):
        channel = "@" + channel
    channels = _state.get("channels", [])
    if channel in channels:
        await update.message.reply_text(f"{channel} is already being monitored.")
        return
    channels.append(channel)
    _state["channels"] = channels
    os.environ["TELEGRAM_CHANNELS"] = ",".join(channels)
    await update.message.reply_text(
        f"Added {channel} to monitoring!\n"
        f"Now watching {len(channels)} channel(s).\n\n"
        f"Note: Add TELEGRAM_CHANNELS={','.join(channels)} to Render env vars to persist after restart."
    )
    logger.info(f"[TG BOT] Channel added: {channel}")


async def cmd_removechannel(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /removechannel @channel_name")
        return
    channel = args[0]
    if not channel.startswith("@"):
        channel = "@" + channel
    channels = _state.get("channels", [])
    if channel not in channels:
        await update.message.reply_text(f"{channel} is not in the monitoring list.")
        return
    channels.remove(channel)
    _state["channels"] = channels
    await update.message.reply_text(
        f"Removed {channel}.\n"
        f"Now watching {len(channels)} channel(s)."
    )
    logger.info(f"[TG BOT] Channel removed: {channel}")


async def cmd_setdelay(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    args = context.args
    if not args or not args[0].isdigit():
        current = _state["post_delay"]
        await update.message.reply_text(
            f"Current delay: {current} minutes between pins.\n\n"
            f"Usage: /setdelay [minutes]\n"
            f"Example: /setdelay 10\n"
            f"Min: 5 minutes | Max: 120 minutes"
        )
        return
    minutes = int(args[0])
    if minutes < 5:
        await update.message.reply_text("Minimum delay is 5 minutes.")
        return
    if minutes > 120:
        await update.message.reply_text("Maximum delay is 120 minutes.")
        return
    _state["post_delay"] = minutes
    os.environ["POST_DELAY_MINUTES"] = str(minutes)
    await update.message.reply_text(
        f"Posting delay set to {minutes} minutes.\n"
        f"New pins will be spaced {minutes} min apart.\n\n"
        f"Add POST_DELAY_MINUTES={minutes} to Render env to persist."
    )
    logger.info(f"[TG BOT] Delay changed to {minutes} min.")


async def cmd_setmax(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    args = context.args
    if not args or not args[0].isdigit():
        current = _state["max_per_day"]
        await update.message.reply_text(
            f"Current max: {current} pins per day.\n\n"
            f"Usage: /setmax [number]\n"
            f"Example: /setmax 20\n"
            f"Range: 1 to 100 pins/day"
        )
        return
    n = int(args[0])
    if n < 1 or n > 100:
        await update.message.reply_text("Please enter a number between 1 and 100.")
        return
    _state["max_per_day"] = n
    os.environ["MAX_POSTS_PER_DAY"] = str(n)
    try:
        import config
        config.MAX_POSTS_PER_DAY = n
    except Exception:
        pass
    try:
        from database import set_metadata
        set_metadata("max_posts_per_day", str(n))
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ Max pins per day set to {n}.\n\n"
        f"Persisted in database & config. (Render env MAX_POSTS_PER_DAY={n})"
    )
    logger.info(f"[TG BOT] Max per day changed to {n}.")


async def cmd_dryrun(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    _state["dry_run"] = not _state["dry_run"]
    current = _state["dry_run"]
    os.environ["DRY_RUN"] = "true" if current else "false"
    if current:
        await update.message.reply_text(
            "DRY RUN mode ON.\n"
            "Pins are logged but NOT posted to Pinterest."
        )
    else:
        await update.message.reply_text(
            "LIVE mode ON!\n"
            "Pins will now be posted to Pinterest.\n"
            "Make sure your API access is approved!"
        )
    logger.info(f"[TG BOT] DRY_RUN toggled to {current}.")


async def cmd_golive(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    _state["dry_run"] = False
    os.environ["DRY_RUN"] = "false"
    await update.message.reply_text(
        "LIVE MODE ACTIVATED!\n\n"
        "Bot will now upload real pins to Pinterest.\n"
        "Make sure your Pinterest API access is approved!\n\n"
        "Use /dryrun to go back to test mode."
    )
    logger.info("[TG BOT] Admin switched to LIVE mode.")


async def cmd_pause(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    _state["is_paused"] = True
    await update.message.reply_text(
        "Bot PAUSED.\n"
        "Scraping continues but no pins will be posted.\n"
        "Use /resume to restart."
    )


async def cmd_resume(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    _state["is_paused"] = False
    await update.message.reply_text("Bot RESUMED. Posting is active again.")


async def cmd_resetbreaker(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    Reset the circuit breaker cooldown for a specific platform or all platforms.
    Usage: /resetbreaker [platform|all] or /unbreak [platform|all]
    """
    if not _is_admin(update): return
    args = context.args if context and context.args else []
    target = (args[0].lower().strip() if args else "all")
    from circuit_breaker import clear_breaker, get_all_cooldowns
    if target in ("all", "*"):
        cooldowns = get_all_cooldowns()
        for p in list(cooldowns.keys()):
            clear_breaker(p)
        try:
            from jsonbin_sync import save_cloud_state
            save_cloud_state()
        except Exception:
            pass
        await update.message.reply_text("✅ All platform circuit breakers cleared. Immediate posting restored.")
    else:
        clear_breaker(target)
        try:
            from jsonbin_sync import save_cloud_state
            save_cloud_state()
        except Exception:
            pass
        await update.message.reply_text(f"✅ Circuit breaker for `{target.upper()}` cleared. Immediate posting restored.", parse_mode="Markdown")



async def cmd_queue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show the real pending queue from SQLite with upcoming pin titles, schedule & countdown."""
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        from database import get_queue_counts, get_queue_detail, get_upcoming_queued_pins
        counts   = get_queue_counts()
        details  = get_queue_detail()
        upcoming = get_upcoming_queued_pins(limit=5)
    except Exception as e:
        await msg.reply_text(f"Could not read queue: {e}")
        return

    total = counts["total"]
    if total == 0:
        await msg.reply_text(
            "Queue is empty.\n\n"
            "👉 Run /scrape to fetch new pins now, or send any photo directly to this bot!",
            reply_markup=get_queue_keyboard()
        )
        return

    # Calculate upcoming slot times with exact clock time
    slot_times = []
    next_post_label = "soon"
    try:
        from scheduler import get_upcoming_slot_times
        slot_times = get_upcoming_slot_times(count=max(len(upcoming), 1))
        if slot_times:
            next_post_label = slot_times[0]
    except Exception:
        pass

    # Build upcoming pins section (titles, anime & exact posting time)
    upcoming_lines = []
    for i, p in enumerate(upcoming):
        p_type = "NEW" if p["priority"] == 1 else "BACKLOG"
        time_label = slot_times[i] if i < len(slot_times) else f"{p['scheduled_date']} ({p_type})"
        upcoming_lines.append(
            f"{i+1}. 🌸 {p['title']}\n"
            f"   🎌 Anime: {p['anime_name']}\n"
            f"   ⏰ Time: {time_label}"
        )
    upcoming_text = "\n\n".join(upcoming_lines) if upcoming_lines else "None"

    # Build per-date breakdown
    lines = []
    for d in details:
        parts = []
        if d["new"] > 0:
            parts.append(f"{d['new']} NEW")
        if d["backlog"] > 0:
            parts.append(f"{d['backlog']} BACKLOG")
        lines.append(f"  • {d['date']}: {', '.join(parts)}")
    breakdown = "\n".join(lines) if lines else "  • No dates scheduled"

    await msg.reply_text(
        f"📋 Pending Queue: {total} pin(s) total\n"
        f"  ({counts['new']} NEW | {counts['backlog']} BACKLOG)\n"
        f"⏰ Next Auto-Post: {next_post_label}\n\n"
        f"📌 Upcoming Pins & Scheduled Times:\n"
        f"{upcoming_text}\n\n"
        f"📅 Daily Distribution:\n{breakdown}\n\n"
        f"👉 Use /post_now to publish pin #1 immediately.\n"
        f"👉 Use /scrape to fetch more pins from channels.",
        reply_markup=get_queue_keyboard()
    )


async def cmd_schedule(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show today's daily posting schedule with exact times on demand."""
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        from database import get_queue_counts, get_queue_detail, get_upcoming_queued_pins
        from scheduler import get_upcoming_slot_times, _get_jittered_times_utc, _ist_now

        counts = get_queue_counts()
        total = counts["total"]
        details = get_queue_detail()
        upcoming = get_upcoming_queued_pins(limit=5)

        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%d %b %Y")

        # ── Always show today's 5 actual slot times ────────────────────────
        today_slot_lines = []
        try:
            jittered_utc = _get_jittered_times_utc()
            now_utc = datetime.datetime.utcnow()
            for i, (h, m) in enumerate(jittered_utc):
                slot_utc = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
                slot_ist = slot_utc + datetime.timedelta(hours=5, minutes=30)
                is_past = slot_ist < now_ist
                status = "✅ Done" if is_past else "⏳ Upcoming"
                if not is_past:
                    mins_away = int((slot_ist - now_ist).total_seconds() / 60)
                    h_diff, m_diff = divmod(mins_away, 60)
                    countdown = f"in ~{h_diff}h {m_diff}m" if h_diff > 0 else f"in ~{m_diff}m"
                    status = f"⏳ {countdown}"
                today_slot_lines.append(
                    f"  Slot {i+1}: {slot_ist.strftime('%I:%M %p')} IST — {status}"
                )
        except Exception:
            today_slot_lines = ["  (Could not calculate slot times)"]
        today_slots_text = "\n".join(today_slot_lines)

        # ── Upcoming pinned posts ──────────────────────────────────────────
        slot_times = []
        try:
            slot_times = get_upcoming_slot_times(count=max(len(upcoming), 1))
        except Exception:
            pass

        if total == 0:
            upcoming_text = (
                "⚠️ Queue is empty — no pins scheduled!\n\n"
                "👉 Tap 🔄 Scrape Channels below to fetch new pins from your\n"
                "   Telegram channel and fill the queue automatically."
            )
        else:
            upcoming_lines = []
            for i, p in enumerate(upcoming):
                p_type = "NEW" if p["priority"] == 1 else "BACKLOG"
                time_label = slot_times[i] if i < len(slot_times) else f"{p['scheduled_date']} ({p_type})"
                upcoming_lines.append(
                    f"{i+1}. 🌸 {p['title']}\n"
                    f"   🎌 Anime: {p['anime_name']}\n"
                    f"   ⏰ Time: {time_label}"
                )
            upcoming_text = "\n\n".join(upcoming_lines)

        # ── Per-date breakdown ─────────────────────────────────────────────
        lines = []
        for d in details:
            parts = []
            if d["new"] > 0:
                parts.append(f"{d['new']} NEW")
            if d["backlog"] > 0:
                parts.append(f"{d['backlog']} BACKLOG")
            lines.append(f"  • {d['date']}: {', '.join(parts)}")
        breakdown = "\n".join(lines) if lines else "  • No dates scheduled"

        text = (
            f"☀️ Daily Pinterest Posting Schedule\n"
            f"📅 {today_str} • {now_ist.strftime('%I:%M %p')} IST\n"
            f"{'═' * 30}\n\n"
            f"🕐 Today's Posting Slots:\n"
            f"{today_slots_text}\n\n"
            f"📊 Queue Status: {total} pin(s) ready\n"
            f"  ({counts['new']} NEW | {counts['backlog']} BACKLOG)\n\n"
            f"📌 Scheduled Pins & Exact Clock Times:\n"
            f"{upcoming_text}\n\n"
            f"📅 Daily Distribution:\n{breakdown}\n\n"
            f"👉 Use /post_now to publish pin #1 immediately\n"
            f"👉 Use /scrape to check channels for fresh posts"
        )
        await msg.reply_text(text, reply_markup=get_queue_keyboard())
    except Exception as e:
        logger.error(f"[TG BOT] /schedule error: {e}", exc_info=True)
        await msg.reply_text(f"❌ Error displaying schedule: {e}")






async def cmd_clearqueue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Purge all pending pins from the SQLite queue."""
    if not _is_admin(update): return
    try:
        from database import clear_pin_queue, get_queue_counts
        # Show what we're about to clear
        before = get_queue_counts()
        if before["total"] == 0:
            await update.message.reply_text(
                "Queue is already empty! Nothing to clear."
            )
            return
        cleared = clear_pin_queue()
        await update.message.reply_text(
            f"Queue cleared!\n"
            f"Removed {cleared} pin(s) ({before['new']} NEW, {before['backlog']} BACKLOG).\n\n"
            f"New pins will be added on next scrape cycle."
        )
        logger.info(f"[TG BOT] Queue cleared by admin: {cleared} pins removed.")
    except Exception as e:
        await update.message.reply_text(f"Error clearing queue: {e}")
        logger.error(f"[TG BOT] clearqueue error: {e}")


async def cmd_deadqueue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show pins that exhausted all retries and landed in the dead-letter queue."""
    if not _is_admin(update): return
    try:
        from database import get_dead_letter_pins, count_dead_letter_pins, clear_dead_letter_queue
        total = count_dead_letter_pins()
        if total == 0:
            await update.message.reply_text(
                "✅ Dead-letter queue is empty — no failed pins.\n"
                "Pins only appear here after failing 3 upload attempts."
            )
            return

        pins = get_dead_letter_pins(limit=10)
        lines = [f"☠️ *Dead-Letter Queue* — {total} failed pin(s)\n{'─' * 30}"]
        for i, p in enumerate(pins, 1):
            failed_at = p["failed_at"][:16] if p["failed_at"] else "unknown"
            reason = p["fail_reason"] or "unknown"
            lines.append(
                f"\n*{i}.* {p['title']}\n"
                f"   🎌 Anime: {p['anime_name']}\n"
                f"   ❌ Reason: {reason[:80]}\n"
                f"   🔄 Retries: {p['retry_count']}  |  🕐 {failed_at}"
            )
        if total > 10:
            lines.append(f"\n_...and {total - 10} more. Showing latest 10._")
        lines.append(f"\n{'─' * 30}\n💡 Use /cleardead to flush this list.")

        await update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown"
        )
        logger.info(f"[TG BOT] Dead-letter queue viewed by admin ({total} entries).")
    except Exception as e:
        await update.message.reply_text(f"Error reading dead-letter queue: {e}")
        logger.error(f"[TG BOT] deadqueue error: {e}")


async def cmd_cleardead(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Clear all entries from the dead-letter queue."""
    if not _is_admin(update): return
    try:
        from database import clear_dead_letter_queue, count_dead_letter_pins
        total = count_dead_letter_pins()
        if total == 0:
            await update.message.reply_text("Dead-letter queue is already empty.")
            return
        cleared = clear_dead_letter_queue()
        await update.message.reply_text(
            f"🗑️ Dead-letter queue cleared.\n"
            f"Removed {cleared} failed pin record(s)."
        )
        logger.info(f"[TG BOT] Dead-letter queue cleared by admin: {cleared} entries.")
    except Exception as e:
        await update.message.reply_text(f"Error clearing dead-letter queue: {e}")
        logger.error(f"[TG BOT] cleardead error: {e}")


async def cmd_fixqueue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    Re-upload all stale queue entries that still have expiring Telegram CDN
    URLs (telesco.pe) to a permanent host (Cloudinary/Catbox) and update the
    DB. Unrecoverable entries (file missing AND CDN expired) are deleted from
    the queue immediately so they don't waste future posting slots.
    """
    if not _is_admin(update): return
    msg = update.effective_message
    await msg.reply_text(
        "🔧 Scanning queue for stale Telegram CDN URLs... This may take a moment."
    )
    try:
        import sqlite3
        from database import DB_PATH
        from image_host import upload_image_to_host
        import asyncio

        def _fix_stale_entries():
            results = {"fixed": 0, "failed": 0, "deleted": 0, "details": []}
            from database import _get_conn
            with _get_conn() as conn:
                rows = conn.execute(
                    "SELECT id, title, image_path, image_url FROM pin_queue "
                    "WHERE image_url LIKE '%telesco.pe%' OR image_url LIKE '%/t.me/%'"
                ).fetchall()

            if not rows:
                return results

            for row_id, title, image_path, old_url in rows:
                # If local file still exists, re-upload it to a permanent host
                if image_path and os.path.exists(image_path):
                    try:
                        new_url = upload_image_to_host(image_path)
                        if new_url:
                            with _get_conn() as conn:
                                conn.execute(
                                    "UPDATE pin_queue SET image_url=? WHERE id=?",
                                    (new_url, row_id)
                                )
                                conn.commit()
                            results["fixed"] += 1
                            results["details"].append(
                                f"✅ '{title[:30]}' → permanent URL stored"
                            )
                            logger.info(
                                f"[fixqueue] Re-uploaded '{title}' to permanent host: {new_url[:60]}"
                            )
                        else:
                            results["failed"] += 1
                            results["details"].append(
                                f"❌ '{title[:30]}' — upload failed (host down?)"
                            )
                    except Exception as e:
                        results["failed"] += 1
                        results["details"].append(
                            f"❌ '{title[:30]}' — error: {str(e)[:40]}"
                        )
                else:
                    # Local file gone + CDN expired = unrecoverable.
                    # DELETE immediately so the scheduler doesn't waste
                    # 3 retry slots trying to re-download a dead URL.
                    with _get_conn() as conn:
                        conn.execute("DELETE FROM pin_queue WHERE id=?", (row_id,))
                        conn.commit()
                    results["deleted"] += 1
                    results["details"].append(
                        f"🗑️ '{title[:30]}' — deleted (file missing & CDN expired)"
                    )
                    logger.warning(
                        f"[fixqueue] Deleted unrecoverable pin '{title}' "
                        f"(file missing + CDN expired)."
                    )
            return results

        results = await asyncio.get_event_loop().run_in_executor(None, _fix_stale_entries)

        if results["fixed"] == 0 and results["failed"] == 0 and results["deleted"] == 0:
            await msg.reply_text(
                "✅ No stale Telegram CDN URLs found in queue!\n"
                "All entries already have permanent URLs."
            )
            return

        detail_lines = "\n".join(results["details"][:15])  # cap at 15 to avoid long messages
        if len(results["details"]) > 15:
            detail_lines += f"\n... and {len(results['details']) - 15} more."

        summary = (
            f"🔧 Fix Queue Results:\n"
            f"✅ Fixed: {results['fixed']} (permanent URL stored)\n"
            f"❌ Failed: {results['failed']} (host upload failed)\n"
            f"🗑️ Deleted: {results['deleted']} (unrecoverable — file missing + CDN expired)\n\n"
            f"{detail_lines}\n\n"
        )
        if results["deleted"] > 0:
            summary += (
                f"The {results['deleted']} deleted pin(s) were removed from the queue to avoid "
                f"wasting posting slots. Re-send the original images to the Telegram channel "
                f"so they get re-queued with a permanent URL."
            )
        await msg.reply_text(summary)
        logger.info(
            f"[TG BOT] /fixqueue complete: {results['fixed']} fixed, "
            f"{results['failed']} failed, {results['deleted']} deleted (unrecoverable)."
        )

        # ── CRITICAL: immediately sync clean state to JSONBin cloud ──────────
        # Without this, the next Render restart would restore the OLD cloud
        # snapshot (which still has the deleted CDN entries) via
        # restore_db_from_cloud(), bringing zombie entries back to life.
        if results["fixed"] > 0 or results["deleted"] > 0:
            try:
                from jsonbin_sync import save_cloud_state
                synced = await asyncio.get_event_loop().run_in_executor(
                    None, save_cloud_state
                )
                if synced:
                    await msg.reply_text(
                        "☁️ Cloud backup updated with cleaned queue.\n"
                        "Deleted/fixed entries will NOT come back after a restart."
                    )
                    logger.info("[fixqueue] Cloud sync completed after cleanup.")
                else:
                    await msg.reply_text(
                        "⚠️ Cloud sync failed — deleted entries may reappear after restart.\n"
                        "Check JSONBIN_API_KEY / JSONBIN_BIN_ID in env vars."
                    )
            except Exception as sync_err:
                logger.warning(f"[fixqueue] Cloud sync after cleanup failed: {sync_err}")
        # ─────────────────────────────────────────────────────────────────────

    except Exception as e:
        await msg.reply_text(f"Error running fixqueue: {e}")
        logger.error(f"[TG BOT] fixqueue error: {e}", exc_info=True)

async def cmd_scrape(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Manually trigger channel scraping & backlog fetch immediately."""
    if not _is_admin(update): return
    msg = update.effective_message
    await msg.reply_text("🔄 Scraping channels and fetching fresh images... Please wait ~15-30s.")
    try:
        from telegram_listener import scrape_all_channels
        import asyncio
        loop = asyncio.get_running_loop()
        new_found, queue_total = await loop.run_in_executor(None, lambda: scrape_all_channels(max_backlog=5))
        await msg.reply_text(
            f"✅ Scrape completed!\n"
            f"• New posts found: {new_found}\n"
            f"• Current queue: {queue_total} pin(s)\n\n"
            f"Use /post_now to publish immediately!",
            reply_markup=get_queue_keyboard()
        )
    except Exception as e:
        await msg.reply_text(f"❌ Scrape error: {e}")
        logger.error(f"[TG BOT] scrape error: {e}", exc_info=True)

async def cmd_postnow(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    Force-post the next queued pin immediately, bypassing the scheduled time slot.
    Loops silently through stale (expired CDN) pins and sends exactly ONE result
    message — no per-pin spam.
    """
    if not _is_admin(update): return
    msg = update.effective_message
    try:
        import requests as _req
        from telegram_listener import download_image
        from image_processor import process_image
        from pinterest_uploader import upload_to_pinterest
        from database import (pop_next_pin_for_immediate_post, get_queue_counts,
                               enqueue_pin, remove_queued_pin)

        # ── Quick check: is there anything in the queue? ─────────────────────
        counts = get_queue_counts()
        if counts["total"] == 0:
            await msg.reply_text(
                "Queue is empty!\n"
                "Wait for the scraper to pick up new images from your Telegram channel."
            )
            return

        await msg.reply_text(
            f"🔍 Scanning queue ({counts['total']} pin(s))… please wait."
        )


        stale_dropped = 0
        MAX_TRIES = counts["total"]  # never try more pins than exist

        for attempt in range(MAX_TRIES + 1):
            # ── Pull next pin ─────────────────────────────────────────────────
            pin = pop_next_pin_for_immediate_post()
            if not pin:
                if stale_dropped > 0:
                    await msg.reply_text(
                        f"⚠️ All {stale_dropped} pin(s) in the queue had expired CDN links (older than 48h from past restarts).\n"
                        f"They were safely cleared from the queue.\n\n"
                        f"👉 Run /scrape to fetch fresh images now, or send any photo directly to this bot!"
                    )
                else:
                    await msg.reply_text(
                        "Queue is empty!\n\n"
                        "👉 Run /scrape to fetch fresh images now, or send any photo directly to this bot!"
                    )
                return

            image_path = pin["image_path"]

            # ── File exists — upload immediately ─────────────────────────────
            if os.path.exists(image_path):
                break  # fall through to upload block below

            # ── File missing — try CDN re-download ───────────────────────────
            cdn_url = pin.get("image_url", "")
            if cdn_url and cdn_url.startswith("http"):
                # Fast HEAD check — avoid downloading a dead URL
                try:
                    head = _req.head(cdn_url, timeout=5, allow_redirects=True)
                    cdn_alive = head.status_code < 400
                except Exception:
                    cdn_alive = False

                if cdn_alive:
                    try:
                        safe_name = os.path.splitext(os.path.basename(image_path))[0]
                        dl_path = download_image(cdn_url, safe_name)
                        if dl_path:
                            image_path = process_image(dl_path)
                            logger.info(f"[TG BOT] /post_now: Re-download OK: {image_path}")
                            break  # got a good image — upload it
                    except Exception as dl_err:
                        logger.warning(f"[TG BOT] /post_now: Re-download failed: {dl_err}")

            # ── CDN dead or no URL — silently drop this pin ───────────────────
            logger.warning(
                f"[TG BOT] /post_now: Dropping stale pin (CDN expired): '{pin['title']}'"
            )
            stale_dropped += 1
            # pin already popped — just continue loop
        else:
            # Exhausted all pins without finding a good one
            await msg.reply_text(
                f"⚠️ All {stale_dropped} pin(s) in the queue had expired CDN URLs.\n"
                f"They have been automatically cleared.\n\n"
                f"📥 The scraper will pick up fresh images next cycle (~10 min).\n"
                f"Or send images directly to the bot to queue them now."
            )
            return

        # ── Upload the good pin ───────────────────────────────────────────────
        from database import should_next_pin_be_organic
        import re
        is_org = pin.get("is_organic", 0)

        if getattr(config, "ORGANIC_TRUST_ENABLED", True) and should_next_pin_be_organic(getattr(config, "ORGANIC_TRUST_RATIO", 0.20)):
            is_org = 1
            pin["is_organic"] = 1
            pin["link"] = (
                getattr(config, "ORGANIC_PIN_LINK", "")
                or getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
            )
            pin["description"] = re.sub(
                r'#ad\b|#affiliate\b|#sponsored\b', '', pin.get("description", "")
            ).strip()
        elif not is_org and getattr(config, "APP_BASE_URL", ""):
            if "amazon." in pin.get("link", "") and "/r/" not in pin.get("link", ""):
                try:
                    from amazon_search import wrap_with_tracker
                    pin["link"] = wrap_with_tracker(
                        pin["link"],
                        anime_name=pin.get("anime_name", ""),
                        title=pin.get("title", ""),
                        image_url=pin.get("image_url", ""),
                    )
                except Exception:
                    pass

        if is_org:
            pin_type = "ORGANIC 🌿"
        else:
            pin_type = "NEW" if pin["priority"] == 1 else "BACKLOG"

        stale_note = f"\n🗑 Skipped {stale_dropped} stale pin(s) with expired CDN URLs." if stale_dropped else ""

        # Pre-upload watermark check: ensure 100% clean image
        try:
            from watermark_inpainter import verify_and_clean
            image_path = verify_and_clean(image_path)
        except Exception:
            pass

        pin_status = upload_to_pinterest(
            image_path=image_path,
            title=pin["title"],
            description=pin["description"],
            link=pin["link"],
            anime_name=pin["anime_name"],
            board_id=pin.get("board_id", ""),
        )
        success = bool(pin_status)  # tri-state: "live" / "queued" / False

        if success:
            _state["posts_today"] = _state.get("posts_today", 0) + 1
            _state["posts_total"] = _state.get("posts_total", 0) + 1
            remaining = get_queue_counts()["total"]
            from database import get_tracked_target_url, mark_file_uploaded
            mark_file_uploaded(
                filename=os.path.basename(image_path),
                title=pin["title"],
                anime_name=pin["anime_name"],
                image_url=pin.get("image_url", ""),
                is_organic=is_org,
            )

            # ── Dispatch cross-posts across all 11 platforms ───────────────────
            from crosspost_dispatcher import dispatch_all_crossposts
            cp_res = dispatch_all_crossposts(pin, image_path, pin_url=pin.get("pin_url", ""))
            cross_lines = format_crosspost_status_block(cp_res)

            target_url = get_tracked_target_url(pin["link"])
            if pin_status == "live":
                _hdr = f"📌 {pin_type} pin posted!{stale_note}"
            else:
                _hdr = (
                    f"📤 {pin_type} pin sent to Pinterest queue (via Make.com){stale_note}\n"
                    f"⚠️ Make.com accepted it — check Created tab to confirm it's live."
                )
            confirm_text = (
                f"{_hdr}\n"
                f"{'─' * 26}\n"
                f"📝 {pin['title']}\n"
                f"🎌 {pin['anime_name']} • {remaining} left in queue"
                f"{cross_lines}"
            )

            confirm_markup = get_post_confirmation_keyboard(
                amazon_url=target_url or pin["link"],
                bluesky_url=cp_res.get("bluesky_url", ""),
                mastodon_url=cp_res.get("mastodon_url", ""),
                arena_url=cp_res.get("arena_url", ""),
                raindrop_url=cp_res.get("raindrop_url", ""),
                pixelfed_url=cp_res.get("pixelfed_url", ""),
                freeimage_url=cp_res.get("freeimage_url", ""),
                imghippo_url=cp_res.get("imghippo_url", ""),
                wallhaven_url=cp_res.get("wallhaven_url", ""),
                deviantart_url=cp_res.get("deviantart_url", ""),
                imgbb_url=cp_res.get("imgbb_url", ""),
                imagekit_url=cp_res.get("imagekit_url", ""),
                imgpile_url=cp_res.get("imgpile_url", ""),
            )

            sent_photo = False
            if image_path and os.path.exists(image_path):
                try:
                    with open(image_path, "rb") as img_file:
                        await msg.reply_photo(photo=img_file, caption=confirm_text[:1024], reply_markup=confirm_markup)
                    sent_photo = True
                except Exception as _e:
                    logger.warning(f"[TG BOT] /post_now file photo failed: {_e}")

            if not sent_photo and pin.get("image_url") and pin["image_url"].startswith(("http://", "https://")):
                try:
                    await msg.reply_photo(photo=pin["image_url"], caption=confirm_text[:1024], reply_markup=confirm_markup)
                    sent_photo = True
                except Exception as _e:
                    logger.warning(f"[TG BOT] /post_now CDN photo failed: {_e}")

            if not sent_photo:
                try:
                    await msg.reply_text(confirm_text, reply_markup=confirm_markup)
                except Exception:
                    await msg.reply_text(confirm_text)
            logger.info(f"[TG BOT] /post_now: posted '{pin['title']}' (skipped {stale_dropped} stale)")
        else:
            # Upload failed — re-queue this pin
            enqueue_pin(
                post_id=pin["post_id"], image_path=image_path,
                title=pin["title"], description=pin["description"],
                link=pin["link"], anime_name=pin["anime_name"],
                image_url=pin.get("image_url", ""),
                board_id=pin.get("board_id", ""),
                priority=pin["priority"], scheduled_date="",
            )
            await msg.reply_text(
                f"❌ Upload failed — pin re-queued.{stale_note}\n"
                f"Check /logs for details."
            )
            logger.warning(f"[TG BOT] /post_now: upload failed for '{pin['title']}' — re-queued.")

    except Exception as e:
        await msg.reply_text(f"Error during /post_now: {e}")
        logger.error(f"[TG BOT] post_now error: {e}", exc_info=True)



# -- Make.com Webhook Commands ------------------------------------------------


async def cmd_autopilot(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Toggle between fully automatic posting and Telegram approval mode."""
    if not _is_admin(update): return
    make_url = os.getenv("MAKE_WEBHOOK_URL", "")
    if not make_url:
        await update.message.reply_text(
            "Make.com Webhook is not configured yet.\n\n"
            "Steps to set it up:\n"
            "1. Sign up free at make.com\n"
            "2. Create scenario: Webhook trigger → Pinterest: Create a Pin\n"
            "3. Copy the webhook URL\n"
            "4. Add MAKE_WEBHOOK_URL=<url> to your .env file\n"
            "5. Set DRY_RUN=false"
        )
        return
    _state["auto_post"] = not _state.get("auto_post", True)
    is_auto = _state["auto_post"]
    os.environ["AUTO_POST_MODE"] = "true" if is_auto else "false"
    if is_auto:
        await update.message.reply_text(
            "AUTO-PILOT ON\n"
            "Pins will be posted automatically to Pinterest via Make.com webhook.\n"
            "No approval needed — just sit back!"
        )
    else:
        await update.message.reply_text(
            "APPROVAL MODE ON\n"
            "Each new pin will be sent to you with [Post to Pinterest] and [Discard] buttons.\n"
            "You control what gets posted, right from Telegram!"
        )
    logger.info(f"[TG BOT] auto_post toggled to {is_auto}")


async def cmd_testpost(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Immediately send a test pin via Make.com webhook using the last processed image."""
    if not _is_admin(update): return
    make_url = os.getenv("MAKE_WEBHOOK_URL", "")
    if not make_url:
        await update.message.reply_text(
            "Make.com webhook URL is not set.\n"
            "Add MAKE_WEBHOOK_URL to your .env first."
        )
        return
    pin = _state.get("last_pin")
    if not pin:
        await update.message.reply_text(
            "No pin ready yet. Wait for the scraper to pick up an image first."
        )
        return
    await update.message.reply_text("Sending test pin to Pinterest via Make.com...")
    try:
        from pinterest_uploader import upload_via_make_webhook
        image_path = pin.get("image_path", "")
        success = upload_via_make_webhook(
            image_path, pin["title"], pin["description"], pin["link"]
        )
        if success:
            await update.message.reply_text(
                "✅ Test pin accepted by Make.com!\n"
                "Webhook returned OK — the Pinterest module runs next.\n"
                "Check Make History + your Created tab to confirm it went live."
            )
            _state["posts_today"] += 1
            _state["posts_total"] += 1
        else:
            await update.message.reply_text(
                "Test post failed. Check /logs for details."
            )
    except Exception as e:
        await update.message.reply_text(f"Error during test post: {e}")
        logger.error(f"[TG BOT] testpost error: {e}")


async def handle_approval_callback(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Handle inline button taps: [Post to Pinterest] or [Discard]."""
    query = update.callback_query
    if not _is_admin(update):
        await query.answer()
        logger.warning("[TG BOT] Ignored callback from non-admin chat")
        return
    await query.answer()

    data = query.data or ""
    if data.startswith("post:"):
        key = data[5:]
        pin = _pending_approvals.pop(key, None)
        if not pin:
            await query.edit_message_caption(caption="This pin has already been handled.")
            return
        await query.edit_message_caption(
            caption=f"Posting to Pinterest...\n\n{pin['title']}"
        )
        try:
            from pinterest_uploader import upload_via_make_webhook
            from database import mark_file_uploaded
            import os as _os
            success = upload_via_make_webhook(
                pin["image_path"], pin["title"], pin["description"], pin["link"]
            )
            if success:
                mark_file_uploaded(_os.path.basename(pin["image_path"]), pin["title"])
                _state["posts_today"] += 1
                _state["posts_total"] += 1
                await query.edit_message_caption(
                    caption=(
                        f"📤 Sent to Pinterest queue via Make.com!\n"
                        f"(Webhook accepted — verify in Created tab before assuming live)\n\n"
                        f"Title: {pin['title']}\n"
                        f"Link: {pin['link']}"
                    )
                )
            else:
                await query.edit_message_caption(
                    caption=f"Failed to post. Check /logs for details."
                )
        except Exception as e:
            logger.error(f"[TG BOT] Approval post error: {e}")
            await query.edit_message_caption(caption=f"Error: {e}")

    elif data.startswith("discard:"):
        key = data[8:]
        _pending_approvals.pop(key, None)
        await query.edit_message_caption(caption="Pin discarded.")
        logger.info("[TG BOT] Pin discarded by admin.")

    elif data.startswith("drop_postnow:"):
        drop_id = data[13:]
        draft = _pending_drops.pop(drop_id, None)
        if not draft:
            await query.answer("Draft has already been processed or expired.")
            return
        await query.answer("🚀 Publishing immediately...")
        await query.edit_message_caption(
            caption=f"🚀 *Publishing to Pinterest & Syndicating in Parallel...*\n\n📝 *{draft['title']}*\n🎌 *{draft['anime_name']}*",
            parse_mode="Markdown"
        )
        try:
            from pinterest_uploader import upload_to_pinterest
            from database import mark_file_uploaded
            from crosspost_dispatcher import dispatch_all_crossposts
            import os

            uploaded_status = upload_to_pinterest(
                image_path=draft["image_path"],
                title=draft["title"],
                description=draft["description"],
                link=draft["link"],
                anime_name=draft["anime_name"],
                board_id=draft.get("board_id", ""),
            )
            success = bool(uploaded_status)
            if success:
                fn = os.path.basename(draft["image_path"])
                mark_file_uploaded(
                    filename=fn,
                    title=draft["title"],
                    anime_name=draft["anime_name"],
                    image_url=draft.get("image_url", ""),
                    sha256=draft.get("sha256", ""),
                    phash=draft.get("phash", ""),
                    is_organic=0,
                )
                _state["posts_today"] = _state.get("posts_today", 0) + 1
                _state["posts_total"] = _state.get("posts_total", 0) + 1

                try:
                    pin_dict = {
                        "id": f"manual_{drop_id}",
                        "title": draft["title"],
                        "description": draft["description"],
                        "link": draft["link"],
                        "anime_name": draft["anime_name"],
                        "image_url": draft.get("image_url", ""),
                    }
                    cp_res = dispatch_all_crossposts(pin_dict, draft["image_path"], pin_url="")
                except Exception as cp_err:
                    logger.error(f"[TG BOT] Quick-post crosspost error: {cp_err}", exc_info=True)
                    cp_res = {}

                confirm_kb = get_post_confirmation_keyboard(
                    pinterest_url=draft.get("pin_url", ""),
                    amazon_url=draft["link"],
                    arena_url=cp_res.get("arena_url", ""),
                    bluesky_url=cp_res.get("bluesky_url", ""),
                    raindrop_url=cp_res.get("raindrop_url", ""),
                    mastodon_url=cp_res.get("mastodon_url", ""),
                    pixelfed_url=cp_res.get("pixelfed_url", ""),
                    freeimage_url=cp_res.get("freeimage_url", ""),
                    imghippo_url=cp_res.get("imghippo_url", ""),
                    wallhaven_url=cp_res.get("wallhaven_url", ""),
                )
                hdr = "📌 *Live on Pinterest!* ✅" if uploaded_status == "live" else "📤 *Sent to Pinterest Queue!* (Make.com)"
                conf_text = (
                    f"{hdr}\n"
                    f"{'─' * 28}\n"
                    f"📝 *Title:* {draft['title']}\n"
                    f"🎌 *Anime:* {draft['anime_name']}\n"
                    f"🔗 *Affiliate:* Amazon & Displate Bridge\n\n"
                    f"⚡ Syndicated across all active channels in parallel."
                )
                await query.edit_message_caption(caption=conf_text, reply_markup=confirm_kb, parse_mode="Markdown")
            else:
                from database import enqueue_pin
                enqueue_pin(
                    post_id=f"manual_{drop_id}",
                    image_path=draft["image_path"],
                    title=draft["title"],
                    description=draft["description"],
                    link=draft["link"],
                    anime_name=draft["anime_name"],
                    image_url=draft.get("image_url", ""),
                    board_id=draft.get("board_id", ""),
                    priority=1,
                    is_organic=0,
                )
                await query.edit_message_caption(
                    caption=f"⚠️ *Direct upload had an issue — pin queued as Priority #1 instead!*\n\n📝 *Title:* {draft['title']}",
                    reply_markup=get_queue_keyboard(),
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"[TG BOT] drop_postnow error: {e}", exc_info=True)
            await query.edit_message_caption(caption=f"❌ Error during posting: {e}")

    elif data.startswith("drop_organic:"):
        drop_id = data[13:]
        draft = _pending_drops.pop(drop_id, None)
        if not draft:
            await query.answer("Draft has already been processed or expired.")
            return
        await query.answer("🌿 Publishing as Organic Trust Pin...")
        await query.edit_message_caption(
            caption=f"🌿 *Publishing as Organic Trust Pin (Zero Affiliate Links)...*\n\n📝 *{draft['title']}*\n🎌 *{draft['anime_name']}*",
            parse_mode="Markdown"
        )
        try:
            from pinterest_uploader import upload_to_pinterest
            from database import mark_file_uploaded
            from crosspost_dispatcher import dispatch_all_crossposts
            import config, os

            organic_link = getattr(config, "ORGANIC_PIN_LINK", "") or getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
            clean_desc = draft["description"].replace("#ad", "").replace("#affiliate", "").replace("#AD", "").strip()

            uploaded_status = upload_to_pinterest(
                image_path=draft["image_path"],
                title=draft["title"],
                description=clean_desc,
                link=organic_link,
                anime_name=draft["anime_name"],
                board_id=draft.get("board_id", ""),
            )
            success = bool(uploaded_status)
            if success:
                fn = os.path.basename(draft["image_path"])
                mark_file_uploaded(
                    filename=fn,
                    title=draft["title"],
                    anime_name=draft["anime_name"],
                    image_url=draft.get("image_url", ""),
                    sha256=draft.get("sha256", ""),
                    phash=draft.get("phash", ""),
                    is_organic=1,
                )
                _state["posts_today"] = _state.get("posts_today", 0) + 1
                _state["posts_total"] = _state.get("posts_total", 0) + 1

                try:
                    pin_dict = {
                        "id": f"manual_org_{drop_id}",
                        "title": draft["title"],
                        "description": clean_desc,
                        "link": organic_link,
                        "anime_name": draft["anime_name"],
                        "image_url": draft.get("image_url", ""),
                        "is_organic": 1,
                    }
                    cp_res = dispatch_all_crossposts(pin_dict, draft["image_path"], pin_url="")
                except Exception as cp_err:
                    logger.error(f"[TG BOT] Organic crosspost error: {cp_err}", exc_info=True)
                    cp_res = {}

                hdr = "🌿 *Organic Pin Live on Pinterest!* ✅" if uploaded_status == "live" else "🌿 *Organic Pin Queued (Make.com)*"
                conf_text = (
                    f"{hdr}\n"
                    f"{'─' * 28}\n"
                    f"📝 *Title:* {draft['title']}\n"
                    f"🎌 *Anime:* {draft['anime_name']}\n"
                    f"🔗 *Link:* {organic_link}\n\n"
                    f"🛡️ *Zero Affiliate Disclosures* — reinforces Pinterest domain authority & organic trust score."
                )
                confirm_kb = get_post_confirmation_keyboard(
                    pinterest_url=draft.get("pin_url", ""),
                    amazon_url="",
                    arena_url=cp_res.get("arena_url", ""),
                    bluesky_url=cp_res.get("bluesky_url", ""),
                    raindrop_url=cp_res.get("raindrop_url", ""),
                    mastodon_url=cp_res.get("mastodon_url", ""),
                    pixelfed_url=cp_res.get("pixelfed_url", ""),
                    freeimage_url=cp_res.get("freeimage_url", ""),
                    imghippo_url=cp_res.get("imghippo_url", ""),
                    wallhaven_url=cp_res.get("wallhaven_url", ""),
                )
                await query.edit_message_caption(caption=conf_text, reply_markup=confirm_kb, parse_mode="Markdown")
            else:
                from database import enqueue_pin
                enqueue_pin(
                    post_id=f"manual_org_{drop_id}",
                    image_path=draft["image_path"],
                    title=draft["title"],
                    description=clean_desc,
                    link=organic_link,
                    anime_name=draft["anime_name"],
                    image_url=draft.get("image_url", ""),
                    board_id=draft.get("board_id", ""),
                    priority=1,
                    is_organic=1,
                )
                await query.edit_message_caption(
                    caption=f"⚠️ *Direct upload had an issue — pin enqueued as Priority #1 Organic Pin!*\n\n📝 *Title:* {draft['title']}",
                    reply_markup=get_queue_keyboard(),
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"[TG BOT] drop_organic error: {e}", exc_info=True)
            await query.edit_message_caption(caption=f"❌ Error during organic posting: {e}")

    elif data.startswith("drop_queue:"):
        drop_id = data[11:]
        draft = _pending_drops.pop(drop_id, None)
        if not draft:
            await query.answer("Draft has already been processed or expired.")
            return
        await query.answer("📅 Adding to Queue...")
        try:
            from scheduler import scheduler
            scheduler.enqueue_new_image(
                post_id=f"manual_{drop_id}",
                image_path=draft["image_path"],
                title=draft["title"],
                description=draft["description"],
                link=draft["link"],
                anime_name=draft["anime_name"],
                board_id=draft.get("board_id", ""),
                image_url=draft.get("image_url", ""),
                sha256=draft.get("sha256", ""),
                phash=draft.get("phash", ""),
                is_organic=0,
            )
            await query.edit_message_caption(
                caption=(
                    f"📥 *Added to Queue (Priority #1)!* ✅\n"
                    f"{'─' * 28}\n"
                    f"📝 *Title:* {draft['title']}\n"
                    f"🎌 *Anime:* {draft['anime_name']}\n\n"
                    f"⏰ Scheduled for today/upcoming optimal posting window.\n"
                    f"Use /queue to inspect the active schedule."
                ),
                reply_markup=get_queue_keyboard(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"[TG BOT] drop_queue error: {e}", exc_info=True)
            await query.edit_message_caption(caption=f"❌ Error adding to queue: {e}")

    elif data.startswith("drop_reroll:"):
        drop_id = data[12:]
        draft = _pending_drops.get(drop_id)
        if not draft:
            await query.answer("Draft not found or expired.")
            return
        await query.answer("🔄 Re-rolling AI Caption...")
        try:
            from ai_caption import generate_pin_content
            from amazon_search import clean_character_name, generate_amazon_link
            from board_router import get_board_for_anime
            from seo_matrix import build_multi_intent_title, build_multi_intent_description, generate_multi_intent_tags
            import config

            reroll_hint = f"{draft.get('caption_hint', '')} fresh aesthetic angle wallpaper anime art".strip()
            anime_name, title, desc_template = generate_pin_content(
                reroll_hint, "studio_drop", image_path=draft["image_path"],
                openrouter_key=config.OPENROUTER_API_KEY
            )
            raw_char = title.split(" - ")[0].split()[0] if title else ""
            character_name = clean_character_name(raw_char, anime_name=anime_name)
            genre, board_id = get_board_for_anime(anime_name)
            try:
                title = build_multi_intent_title(
                    original_title=title, anime_name=anime_name,
                    character_name=character_name, genre=genre
                )
            except Exception:
                pass
            amazon_link = generate_amazon_link(
                anime_name, character_name=character_name,
                title=title, image_url=draft.get("image_url", "")
            )
            try:
                description = build_multi_intent_description(
                    base_description=desc_template, anime_name=anime_name,
                    character_name=character_name, link_url=amazon_link
                )
                color_tags = []
                try:
                    from image_processor import extract_image_color_tags
                    color_tags = extract_image_color_tags(draft["image_path"])
                except Exception:
                    pass
                optimized_tags = generate_multi_intent_tags(
                    anime_name=anime_name, character_name=character_name,
                    genre=genre, target_count=13, include_disclosure=True,
                    color_tags=color_tags,
                )
                description = description.rstrip() + "\n\n" + optimized_tags
                if "#ad" not in description.lower() and "#affiliate" not in description.lower():
                    description = description.rstrip() + "\n#ad #affiliate"
            except Exception:
                description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link) + "\n#ad #affiliate"

            draft.update({
                "title": title,
                "description": description,
                "link": amazon_link,
                "anime_name": anime_name,
                "character_name": character_name,
                "board_id": board_id,
                "genre": genre,
            })

            char_label = f" ({character_name})" if character_name and character_name.lower() not in anime_name.lower() else ""
            new_caption = (
                f"✨ *Studio Quick-Post Draft (Re-Rolled)*\n"
                f"{'─' * 28}\n"
                f"📝 *Title:* {title}\n"
                f"🎌 *Anime:* {anime_name}{char_label} • *Board:* {genre.title()}\n"
                f"🔗 *Affiliate:* Amazon & Displate Bridge\n\n"
                f"{description[:220]}..."
            )
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("🚀 Post Now (All Platforms)", callback_data=f"drop_postnow:{drop_id}"),
                    InlineKeyboardButton("📅 Add to Queue (Priority #1)", callback_data=f"drop_queue:{drop_id}"),
                ],
                [
                    InlineKeyboardButton("🌿 Post as Organic (No Link)", callback_data=f"drop_organic:{drop_id}"),
                    InlineKeyboardButton("🔄 Re-roll Again", callback_data=f"drop_reroll:{drop_id}"),
                ],
                [
                    InlineKeyboardButton("❌ Discard", callback_data=f"drop_discard:{drop_id}"),
                ]
            ])
            await query.edit_message_caption(caption=new_caption, reply_markup=keyboard, parse_mode="Markdown")
        except Exception as e:
            logger.error(f"[TG BOT] drop_reroll error: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Error during re-roll: {e}")

    elif data.startswith("drop_discard:"):
        drop_id = data[13:]
        draft = _pending_drops.pop(drop_id, None)
        await query.answer("Draft discarded.")
        if draft and draft.get("image_path"):
            try:
                import os
                if os.path.exists(draft["image_path"]) and "studio_" in draft["image_path"]:
                    os.remove(draft["image_path"])
            except Exception:
                pass
        await query.edit_message_caption(caption="🗑️ *Draft discarded by admin.*", parse_mode="Markdown")

    elif data == "btn_postnow":
        await query.message.reply_text("🚀 Triggering immediate post...")
        await cmd_postnow(update, context)
    elif data == "btn_scrape":
        await cmd_scrape(update, context)
    elif data == "btn_queue":
        await cmd_queue(update, context)
    elif data == "btn_schedule":
        await cmd_schedule(update, context)
    elif data == "btn_stats":
        await cmd_stats(update, context)
    elif data == "btn_doctor":
        await cmd_doctor(update, context)
    elif data == "btn_digest":
        await cmd_digest(update, context)

    elif data.startswith("spinoff:"):
        code = data[8:]
        await query.answer("🔥 Generating Express Spinoff Pins...")
        try:
            from viral_radar import generate_spinoff_pins
            res = generate_spinoff_pins(code, count=2)
            await query.message.reply_text(
                f"🔥 *Spinoff Pins Activated!*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🎨 Anime: *{res['anime_name']}*\n"
                f"⚡ Boosted from Queue: *{res['boosted']}* (Priority 2)\n"
                f"✨ Generated Fresh: *{res['generated']}* (Priority 2)\n"
                f"🚀 Total Active Spinoffs: *{res['total']}*\n\n"
                f"Scheduled at Express Priority (#1) to ride the active Pinterest surge!"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Spinoff error: {e}")

    elif data.startswith("boost:"):
        code = data[6:]
        await query.answer("⚡ Boosting Priority to #1...")
        try:
            from database import get_tracked_link_data, boost_queue_priority_by_anime
            link_data = get_tracked_link_data(code) or {}
            anime = link_data.get("anime_name", "")
            boosted = boost_queue_priority_by_anime(anime, new_priority=2, limit=3)
            if boosted > 0:
                await query.message.reply_text(
                    f"⚡ *Express Priority Granted!*\n"
                    f"Boosted *{boosted}* queued pin(s) for *{anime}* to Priority #1."
                )
            else:
                await query.message.reply_text(
                    f"ℹ️ No unposted pins for *{anime}* found in queue to boost.\n"
                    f"Tap *Spinoff 2 Pins Now* to auto-generate fresh express variations!"
                )
        except Exception as e:
            await query.message.reply_text(f"❌ Boost error: {e}")

    elif data.startswith("analytics:"):
        code = data[10:]
        await query.answer()
        try:
            from database import _get_conn
            with _get_conn() as conn:
                v = conn.execute(
                    "SELECT COUNT(*), COUNT(CASE WHEN is_pinterest=1 THEN 1 END) FROM bridge_visits WHERE code = ?",
                    (code,),
                ).fetchone()
                c = conn.execute(
                    "SELECT COUNT(*) FROM link_clicks WHERE code = ?",
                    (code,),
                ).fetchone()
                tot_v = v[0] if v else 0
                pint_v = v[1] if v else 0
                clicks = c[0] if c else 0
                ctr = round((clicks / max(1, tot_v)) * 100, 1)
            await query.message.reply_text(
                f"📊 *Deep Pin Analytics for `{code}`*:\n"
                f"  • Total Bridge Visits: *{tot_v}*\n"
                f"  • From Pinterest Search: *{pint_v}* ({round(pint_v/max(1,tot_v)*100, 1)}%)\n"
                f"  • External Visits: *{max(0, tot_v - pint_v)}*\n"
                f"  • Affiliate Clicks: *{clicks}*\n"
                f"  • Conversion Rate: *{ctr}%* CTR"
            )
        except Exception as e:
            await query.message.reply_text(f"❌ Analytics lookup error: {e}")

    elif data.startswith("dismiss_surge:"):
        await query.answer("Alert dismissed.")
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass


# -- Notify admin helper ------------------------------------------------------
_app_ref  = None
_loop_ref = None
_anti_flood_timestamps: list[float] = []
_FLOOD_LOCK = threading.Lock()
MAX_NOTIFICATIONS_PER_MINUTE = 5


def _check_flood_protection() -> bool:
    """Return True if notification is allowed, False if throttling active."""
    now = time.time()
    with _FLOOD_LOCK:
        while _anti_flood_timestamps and (now - _anti_flood_timestamps[0]) > 60.0:
            _anti_flood_timestamps.pop(0)
        if len(_anti_flood_timestamps) >= MAX_NOTIFICATIONS_PER_MINUTE:
            logger.warning(
                f"[TG BOT] Anti-flood protection triggered: {len(_anti_flood_timestamps)} messages in last 60s. Throttling outbound notification."
            )
            return False
        _anti_flood_timestamps.append(now)
        return True


def notify_admin(message: str, parse_mode: str = "Markdown"):
    """Send a Markdown-formatted notification to the admin from any thread."""
    if not _check_flood_protection():
        return
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _app_ref.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            ),
            _loop_ref
        )
    except Exception as e:
        logger.warning(f"[TG BOT] Could not notify admin: {e}")


def notify_admin_with_keyboard(message: str, reply_markup=None, parse_mode: str = "Markdown"):
    """Send a Markdown-formatted notification with an InlineKeyboardMarkup to the admin from any thread."""
    if not _check_flood_protection():
        return
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _app_ref.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            ),
            _loop_ref
        )
    except Exception as e:
        logger.warning(f"[TG BOT] Could not notify admin with keyboard: {e}")


_last_click_alerts: dict[str, float] = {}

def notify_link_clicked(anime_name: str, title: str, today_count: int, source: str = "Pinterest", country: str = ""):
    """
    Sends a notification to Telegram when a visitor clicks an affiliate link.
    Features:
      1. Dynamic source attribution (Pinterest, Bluesky, Telegram, Direct).
      2. Milestone alerts (1st, 5th, 10th, 20th, 25th, 50th...).
      3. 5-minute per-pin cooldown to prevent notification bursts from spamming admin.
    """
    import config
    import time
    if not config.CLICK_NOTIFICATION:
        return
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return

    # Anti-spam throttling & milestone gating:
    # Always notify on milestone counts (1, 5, 10, 20, 25, 50, 75, 100...)
    # Otherwise, require at least 300 seconds (5 min) cooldown since last alert for this pin
    pin_key = f"{anime_name}:{title[:30]}"
    now = time.time()
    last_time = _last_click_alerts.get(pin_key, 0)
    is_milestone = today_count in (1, 5, 10, 20, 25, 50, 75, 100) or (today_count > 0 and today_count % 50 == 0)

    if not is_milestone and (now - last_time < 300):
        # Throttle rapid clicks to avoid buzzing the user's phone
        logger.debug(f"[TG BOT] Suppressed duplicate click alert for '{pin_key}' (cooldown: {int(now - last_time)}s)")
        return

    _last_click_alerts[pin_key] = now

    # Clean old entries from _last_click_alerts if size grows > 1000
    if len(_last_click_alerts) > 1000:
        _last_click_alerts.clear()

    # Dynamic headline based on source and milestone
    if is_milestone and today_count > 1:
        headline = f"🎉 Milestone: {today_count} Clicks Today!"
    elif source and source != "Direct":
        headline = f"🔔 Affiliate Link Clicked via {source}!"
    else:
        headline = f"🔔 Affiliate Link Clicked!"

    loc_line = f"🌍 Location: {country_to_flag(country)} {country}\n" if country else ""
    text = (
        f"{headline}\n"
        f"{'─' * 28}\n"
        f"🎌 Anime : {anime_name or 'Anime'}\n"
        f"{loc_line}"
        f"📝 Pin   : {title[:60]}\n"
        f"🖱️ Clicks Today : {today_count}\n"
        f"Use /clicks to see all stats."
    )

    async def _send():
        try:
            await _app_ref.bot.send_message(chat_id=admin_id, text=text)
        except Exception as e:
            logger.warning(f"[TG BOT] Link click notification failed: {e}")

    try:
        asyncio.run_coroutine_threadsafe(_send(), _loop_ref)
    except Exception:
        pass


async def handle_admin_photo_upload(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """
    Direct Telegram Image Drop & Interactive Quick-Post Studio.
    Admin drops or forwards any anime artwork:
      1. Downloads image & processes through Lanczos downscale (zero crop).
      2. Inpaints and removes corner watermarks or channel text.
      3. Performs cryptographic SHA-256 and visual pHash deduplication check.
      4. Uploads to permanent image host (Catbox/Freeimage/Cloudinary).
      5. Runs Vision AI to identify anime, character, and multi-intent metadata.
      6. Builds multi-intent SEO title, description, and affiliate bridge link.
      7. Sends back the processed photo with interactive 1-tap action buttons:
         [🚀 Post Now (All Platforms)]  [📅 Add to Queue (Priority #1)]
         [🌿 Post as Organic (No Link)] [🔄 Re-roll AI Caption]
         [❌ Discard]
    """
    if not _is_admin(update): return
    msg = update.effective_message
    status_msg = await msg.reply_text("📥 *Artwork received!* Processing image & running Vision AI analysis...", parse_mode="Markdown")
    try:
        import time as _time
        import uuid
        os.makedirs("downloads", exist_ok=True)
        ts = int(_time.time())
        drop_id = f"drop_{ts}_{uuid.uuid4().hex[:6]}"
        save_path = os.path.join("downloads", f"studio_{ts}.jpg")

        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.document:
            file_id = update.message.document.file_id
        else:
            await status_msg.edit_text("❌ No supported image found in upload.")
            return

        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(save_path)

        # 1. Process image
        from image_processor import process_image
        processed_path = process_image(save_path, "studio_drop")

        # 2. Watermark cleaner
        try:
            from watermark_inpainter import verify_and_clean
            processed_path = verify_and_clean(processed_path)
        except Exception as _wm_err:
            logger.warning(f"[Studio Drop] Watermark check error: {_wm_err}")

        # 3. Deduplication check
        dup_warning = ""
        img_sha, img_phash = "", ""
        try:
            from image_dedup import get_image_fingerprints
            from database import is_image_duplicate
            img_sha, img_phash = get_image_fingerprints(processed_path)
            is_dup, dup_reason = is_image_duplicate(filepath=processed_path, sha256=img_sha, phash=img_phash)
            if is_dup:
                dup_warning = f"\n⚠️ *Warning:* Visual duplicate detected: {dup_reason}\n"
        except Exception:
            pass

        # 4. Upload to permanent host immediately
        permanent_url = ""
        try:
            from image_host import upload_image_to_host
            permanent_url = upload_image_to_host(processed_path)
            if permanent_url:
                logger.info(f"[Studio Drop] Permanent host upload success: {permanent_url[:60]}")
        except Exception as _host_err:
            logger.warning(f"[Studio Drop] Host upload error: {_host_err}")

        # 5. Vision AI content generation
        from ai_caption import generate_pin_content
        import config
        caption_hint = update.message.caption or ""
        anime_name, title, desc_template = generate_pin_content(
            caption_hint, "studio_drop", image_path=processed_path,
            openrouter_key=config.OPENROUTER_API_KEY
        )

        # 6. Character name & board routing
        from amazon_search import clean_character_name, generate_amazon_link
        from board_router import get_board_for_anime
        raw_char = title.split(" - ")[0].split()[0] if title else ""
        character_name = clean_character_name(raw_char, anime_name=anime_name)
        genre, board_id = get_board_for_anime(anime_name)

        # 7. Multi-intent SEO Title
        from seo_matrix import build_multi_intent_title, build_multi_intent_description, generate_multi_intent_tags
        try:
            title = build_multi_intent_title(
                original_title=title, anime_name=anime_name,
                character_name=character_name, genre=genre
            )
        except Exception:
            pass

        # 8. Affiliate bridge link
        amazon_link = generate_amazon_link(
            anime_name, character_name=character_name,
            title=title, image_url=permanent_url
        )

        # 9. Multi-intent description & hashtags
        try:
            description = build_multi_intent_description(
                base_description=desc_template, anime_name=anime_name,
                character_name=character_name, link_url=amazon_link
            )
            color_tags = []
            try:
                from image_processor import extract_image_color_tags
                color_tags = extract_image_color_tags(processed_path)
            except Exception:
                pass
            optimized_tags = generate_multi_intent_tags(
                anime_name=anime_name, character_name=character_name,
                genre=genre, target_count=13, include_disclosure=True,
                color_tags=color_tags,
            )
            description = description.rstrip() + "\n\n" + optimized_tags
            if "#ad" not in description.lower() and "#affiliate" not in description.lower():
                description = description.rstrip() + "\n#ad #affiliate"
        except Exception:
            description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link) + "\n#ad #affiliate"

        # 10. Cache in _pending_drops
        _pending_drops[drop_id] = {
            "drop_id": drop_id,
            "image_path": processed_path,
            "image_url": permanent_url,
            "title": title,
            "description": description,
            "desc_template": desc_template,
            "link": amazon_link,
            "anime_name": anime_name,
            "character_name": character_name,
            "board_id": board_id,
            "genre": genre,
            "sha256": img_sha,
            "phash": img_phash,
            "caption_hint": caption_hint,
        }

        char_label = f" ({character_name})" if character_name and character_name.lower() not in anime_name.lower() else ""
        draft_caption = (
            f"✨ *Studio Quick-Post Draft Ready!*\n"
            f"{'─' * 28}\n"
            f"📝 *Title:* {title}\n"
            f"🎌 *Anime:* {anime_name}{char_label} • *Board:* {genre.title()}\n"
            f"🔗 *Affiliate:* Amazon & Displate Bridge{dup_warning}\n"
            f"{description[:220]}..."
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚀 Post Now (All Platforms)", callback_data=f"drop_postnow:{drop_id}"),
                InlineKeyboardButton("📅 Add to Queue (Priority #1)", callback_data=f"drop_queue:{drop_id}"),
            ],
            [
                InlineKeyboardButton("🌿 Post as Organic (No Link)", callback_data=f"drop_organic:{drop_id}"),
                InlineKeyboardButton("🔄 Re-roll AI Caption", callback_data=f"drop_reroll:{drop_id}"),
            ],
            [
                InlineKeyboardButton("❌ Discard", callback_data=f"drop_discard:{drop_id}"),
            ]
        ])

        try:
            await status_msg.delete()
        except Exception:
            pass

        if processed_path and os.path.exists(processed_path):
            with open(processed_path, "rb") as img:
                await msg.reply_photo(photo=img, caption=draft_caption[:1024], reply_markup=keyboard, parse_mode="Markdown")
        elif permanent_url:
            await msg.reply_photo(photo=permanent_url, caption=draft_caption[:1024], reply_markup=keyboard, parse_mode="Markdown")
        else:
            await msg.reply_text(draft_caption, reply_markup=keyboard, parse_mode="Markdown")

    except Exception as e:
        logger.error(f"[TG BOT] Studio Quick-Drop upload error: {e}", exc_info=True)
        await msg.reply_text(f"❌ Error processing upload in Studio: {e}")



def notify_admin_pin_posted(title: str, anime_name: str, link: str,
                             image_path: str, pin_type: str,
                             posted_today: int, max_today: int,
                             time_ist: str,
                             arena_ok=None, arena_url: str = "",
                             bluesky_ok=None, bluesky_url: str = "",
                             raindrop_ok=None, raindrop_url: str = "",
                             mastodon_ok=None, mastodon_url: str = "",
                             pixelfed_ok=None, pixelfed_url: str = "",
                             freeimage_ok=None, freeimage_url: str = "",
                             imghippo_ok=None, imghippo_url: str = "",
                             wallhaven_ok=None, wallhaven_url: str = "",
                             deviantart_ok=None, deviantart_url: str = "",
                             imgbb_ok=None, imgbb_url: str = "",
                             imagekit_ok=None, imagekit_url: str = "",
                             imgpile_ok=None, imgpile_url: str = "",
                             pin_live: bool = True,

                             image_url: str = ""):
    """
    Send a rich Telegram notification after every successful Pinterest post.
    pin_live=False → Make.com accepted the webhook but the pin is NOT yet
    confirmed on Pinterest; header says "queue" instead of "Posted".
    Sends the actual image + details with 1-tap buttons for all 11 social & image hosting platforms.
    """
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return

    # Use config MAX_POSTS_PER_DAY so bar is always correct size (5, matching 5 daily slots)
    from config import MAX_POSTS_PER_DAY as _max
    actual_max = max(1, _max)  # always read from config, ignore stale state

    from database import get_tracked_target_url
    target_url = get_tracked_target_url(link)

    # Clean, normalized 5-block progress bar that never wraps
    filled = min(posted_today, actual_max)
    pct = int((filled / actual_max) * 100)
    if actual_max == 5:
        filled_blocks = min(5, max(0, filled))
    else:
        filled_blocks = min(5, max(0, round((filled / actual_max) * 5)))
        if filled > 0 and filled_blocks == 0:
            filled_blocks = 1
        elif filled < actual_max and filled_blocks == 5:
            filled_blocks = 4
    prog_bar = "🟩" * filled_blocks + "⬜" * (5 - filled_blocks)

    results_map = {
        "bluesky_ok": bluesky_ok,
        "raindrop_ok": raindrop_ok,
        "mastodon_ok": mastodon_ok,
        "freeimage_ok": freeimage_ok,
        "wallhaven_ok": wallhaven_ok,
        "arena_ok": arena_ok,
        "pixelfed_ok": pixelfed_ok,
        "imghippo_ok": imghippo_ok,
        "deviantart_ok": deviantart_ok,
        "imgbb_ok": imgbb_ok,
        "imagekit_ok": imagekit_ok,
        "imgpile_ok": imgpile_ok,
    }

    cross_lines = format_crosspost_status_block(results_map)

    _hdr = "📌 Pin Confirmed Live!" if pin_live else "📤 Sent to Make.com (unverified)"
    _live_note = (
        ""
        if pin_live else
        "\n⏳ Make.com accepted it — NOT yet seen on your Pinterest profile.\n"
        "Bot is checking your profile and will alert you if it never appears."
    )
    caption = (
        f"{_hdr} ({prog_bar} {posted_today}/{actual_max})\n"
        f"{'─' * 26}\n"
        f"📝 {title}\n"
        f"🎌 {anime_name} • {time_ist} IST"
        f"{_live_note}"
        f"{cross_lines}"
    )

    # Auto-resolve Cloudinary CDN URL from database if not explicitly passed
    if not image_url or not image_url.startswith(("http://", "https://")):
        try:
            from database import get_uploaded_file_image_url
            fn = os.path.basename(image_path) if image_path else ""
            if fn:
                found_url = get_uploaded_file_image_url(fn)
                if found_url:
                    image_url = found_url
        except Exception:
            pass

    reply_markup = get_post_confirmation_keyboard(
        amazon_url=target_url or link,
        bluesky_url=bluesky_url,
        mastodon_url=mastodon_url,
        arena_url=arena_url,
        raindrop_url=raindrop_url,
        pixelfed_url=pixelfed_url,
        freeimage_url=freeimage_url,
        imghippo_url=imghippo_url,
        wallhaven_url=wallhaven_url,
        deviantart_url=deviantart_url,
        imgbb_url=imgbb_url,
        imagekit_url=imagekit_url,
        imgpile_url=imgpile_url,
    )


    async def _send():
        sent = False
        # Tier 1: Try local image file
        if image_path and os.path.exists(image_path):
            try:
                with open(image_path, "rb") as img:
                    await _app_ref.bot.send_photo(
                        chat_id=admin_id,
                        photo=img,
                        caption=caption[:1024],
                        reply_markup=reply_markup,
                    )
                sent = True
                logger.info(f"[TG BOT] Pin photo sent successfully via local file to {admin_id}")
            except Exception as e:
                logger.warning(f"[TG BOT] Local photo notification with buttons failed ({e}) — retrying photo without buttons")
                if reply_markup is not None:
                    try:
                        with open(image_path, "rb") as img:
                            await _app_ref.bot.send_photo(
                                chat_id=admin_id,
                                photo=img,
                                caption=caption[:1024],
                            )
                        sent = True
                        logger.info(f"[TG BOT] Pin photo sent successfully via local file (without buttons) to {admin_id}")
                    except Exception as e2:
                        logger.warning(f"[TG BOT] Local photo notification failed ({e2}) — falling back to CDN bytes")

        # Tier 1.5: Download from CDN to temp file and send as file bytes (most reliable for Telegram)
        if not sent and image_url and image_url.startswith(("http://", "https://")):
            temp_file = None
            try:
                import tempfile
                import requests as _requests
                r = _requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if r.status_code == 200 and len(r.content) > 1000:
                    tf = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    tf.write(r.content)
                    tf.close()
                    temp_file = tf.name
                    with open(temp_file, "rb") as img:
                        await _app_ref.bot.send_photo(
                            chat_id=admin_id,
                            photo=img,
                            caption=caption[:1024],
                            reply_markup=reply_markup,
                        )
                    sent = True
                    logger.info(f"[TG BOT] Pin photo sent successfully via downloaded CDN bytes to {admin_id}")
            except Exception as e:
                logger.warning(f"[TG BOT] Downloaded CDN photo notification failed ({e}) — retrying without buttons")
                if reply_markup is not None and temp_file and os.path.exists(temp_file):
                    try:
                        with open(temp_file, "rb") as img:
                            await _app_ref.bot.send_photo(
                                chat_id=admin_id,
                                photo=img,
                                caption=caption[:1024],
                            )
                        sent = True
                        logger.info(f"[TG BOT] Pin photo sent successfully via downloaded CDN bytes (without buttons) to {admin_id}")
                    except Exception as e2:
                        logger.warning(f"[TG BOT] Downloaded CDN photo retry failed ({e2}) — falling back to direct URL")
            finally:
                if temp_file and os.path.exists(temp_file):
                    try:
                        os.remove(temp_file)
                    except Exception:
                        pass

        # Tier 2: Try public CDN URL (Cloudinary / Catbox)
        if not sent and image_url and image_url.startswith(("http://", "https://")):
            try:
                await _app_ref.bot.send_photo(
                    chat_id=admin_id,
                    photo=image_url,
                    caption=caption[:1024],
                    reply_markup=reply_markup,
                )
                sent = True
                logger.info(f"[TG BOT] Pin photo sent successfully via CDN URL to {admin_id}")
            except Exception as e:
                logger.warning(f"[TG BOT] CDN photo notification failed ({e}) — retrying without buttons")
                if reply_markup is not None:
                    try:
                        await _app_ref.bot.send_photo(
                            chat_id=admin_id,
                            photo=image_url,
                            caption=caption[:1024],
                        )
                        sent = True
                        logger.info(f"[TG BOT] Pin photo sent successfully via CDN URL (without buttons) to {admin_id}")
                    except Exception as e2:
                        logger.warning(f"[TG BOT] CDN photo retry failed ({e2}) — falling back to text")

        # Tier 3: Send text message with interactive buttons
        if not sent:
            try:
                await _app_ref.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=reply_markup,
                )
                sent = True
                logger.info(f"[TG BOT] Pin notification sent as text with buttons to {admin_id}")
            except Exception as e:
                logger.warning(f"[TG BOT] Text notification with buttons failed ({e}) — trying stripped markup")

        # Tier 4: Send pure text message without buttons (emergency fallback)
        if not sent:
            try:
                await _app_ref.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                )
                sent = True
                logger.info(f"[TG BOT] Pin notification sent as plain text fallback to {admin_id}")
            except Exception as e:
                logger.error(f"[TG BOT] Critical: all notification attempts failed for '{title}': {e}")

    try:
        fut = asyncio.run_coroutine_threadsafe(_send(), _loop_ref)
        def _on_done(f):
            try:
                exc = f.exception()
                if exc:
                    logger.error(f"[TG BOT] Pin notification async task failed: {exc}")
            except Exception:
                pass
        fut.add_done_callback(_on_done)
    except Exception as e:
        logger.warning(f"[TG BOT] Could not schedule pin notification: {e}")





def send_pin_approval_request(image_path: str, title: str, description: str, link: str):
    """
    Sends a photo message to the admin with [Post to Pinterest] and [Discard] buttons.
    Called from the main pipeline when AUTO_POST_MODE=false.
    """
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        logger.warning("[TG BOT] Cannot send approval request: bot not ready.")
        return

    # Create a unique key for this pending pin
    import hashlib
    key = hashlib.md5(f"{image_path}{title}".encode()).hexdigest()[:8]
    _pending_approvals[key] = {
        "image_path": image_path,
        "title":      title,
        "description": description,
        "link":        link,
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚀 Post to Pinterest", callback_data=f"post:{key}"),
            InlineKeyboardButton("❌ Discard",           callback_data=f"discard:{key}"),
        ]
    ])

    caption = (
        f"New Pin Ready!\n\n"
        f"Title: {title}\n"
        f"Link: {link}\n\n"
        f"{description[:200]}{'...' if len(description) > 200 else ''}"
    )

    async def _send():
        try:
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as f:
                    await _app_ref.bot.send_photo(
                        chat_id=admin_id,
                        photo=f,
                        caption=caption[:1024],
                        reply_markup=keyboard,
                    )
            else:
                await _app_ref.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=keyboard,
                )
        except Exception as e:
            logger.warning(f"[TG BOT] Could not send approval request: {e}")

    asyncio.run_coroutine_threadsafe(_send(), _loop_ref)
    logger.info(f"[TG BOT] Approval request sent for: {title}")


# -- Are.na Cross-Post Commands -----------------------------------------------

async def cmd_arena(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Are.na channel stats: block count, remaining quota, channel URL."""
    if not _is_admin(update):
        return
    try:
        from arena_uploader import get_arena_channel_info, verify_arena_token
        from config import ARENA_ENABLED, ARENA_CHANNEL_SLUG

        enabled_str = "ENABLED" if ARENA_ENABLED else "DISABLED (set ARENA_ENABLED=true)"
        token_ok    = verify_arena_token()
        info        = get_arena_channel_info() if token_ok else None

        if info:
            used      = info["length"]
            remaining = max(0, 200 - used)  # Guest plan: 200 blocks
            bar_filled = min(20, int(used / 200 * 20))
            bar = "[" + "+" * bar_filled + "-" * (20 - bar_filled) + "]"
            msg = (
                f"Are.na Channel Status\n"
                f"{'='*30}\n"
                f"Channel   : {info['title']}\n"
                f"Status    : {info['status'].capitalize()}\n"
                f"Blocks    : {used}/200 used (Guest plan)\n"
                f"Remaining : {remaining} blocks free\n"
                f"{bar} {used/200*100:.0f}%\n"
                f"URL       : {info['url']}\n"
                f"Token     : Valid\n"
                f"Auto-post : {enabled_str}"
            )
        elif token_ok:
            msg = (
                f"Are.na connected but channel not found.\n"
                f"Check ARENA_CHANNEL_SLUG={ARENA_CHANNEL_SLUG} in .env"
            )
        else:
            msg = (
                f"Are.na token is INVALID or not set.\n"
                f"Check ARENA_ACCESS_TOKEN in .env"
            )
    except Exception as e:
        msg = f"Are.na error: {e}"

    await update.message.reply_text(msg)


async def cmd_arena_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a test block to Are.na channel to verify the token and channel work."""
    if not _is_admin(update):
        return
    await update.message.reply_text("Posting test block to Are.na...")
    try:
        from arena_uploader import post_to_arena
        ok = post_to_arena(
            image_url   = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3f/Biwa_catfish.jpg/640px-Biwa_catfish.jpg",
            title       = "Test Block - Pinterest Bot",
            description = "Automated test block from your Pinterest Bot's Are.na integration. If you see this, it's working!",
            link        = "https://amazon.in",
        )
        if ok:
            await update.message.reply_text(
                "Test block posted to Are.na successfully!\n"
                "Check your channel: https://www.are.na/manoj-muthelyrics/aesthetic-inspiration"
            )
        else:
            await update.message.reply_text(
                "Test block FAILED. Check:\n"
                "1. ARENA_ACCESS_TOKEN is correct in .env\n"
                "2. ARENA_CHANNEL_SLUG matches your channel URL\n"
                "3. ARENA_ENABLED=true in .env\n"
                "Run /logs for details."
            )
    except Exception as e:
        await update.message.reply_text(f"Are.na test error: {e}")




async def cmd_crosspost(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show real-time multi-platform status across all 11 platforms (Pinterest, Are.na, Bluesky, Raindrop, Mastodon, Pixelfed, Freeimage, Imghippo, Wallhaven, DeviantArt, ImgBB, ImageKit) with quick links."""
    if not _is_admin(update):
        return
    try:
        from config import (
            ARENA_ENABLED, ARENA_CHANNEL_SLUG,
            BLUESKY_ENABLED, BLUESKY_HANDLE,
            RAINDROP_ENABLED, RAINDROP_COLLECTION_ID,
            MASTODON_ENABLED, MASTODON_INSTANCE_URL,
            PIXELFED_ENABLED, PIXELFED_INSTANCE_URL,
            FREEIMAGE_ENABLED,
            DRY_RUN
        )
        from database import (
            get_arena_stats, get_bluesky_stats,
            get_raindrop_stats, get_mastodon_stats, get_pixelfed_stats,
            get_freeimage_stats, get_imghippo_stats,
            get_today_uploads, get_all_time_stats
        )
        from arena_uploader import verify_arena_token, get_arena_channel_info
        from raindrop_uploader import verify_raindrop_token, get_raindrop_collection_info
        from mastodon_uploader import verify_mastodon_token, get_mastodon_profile_info

        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%Y-%m-%d")

        # Pinterest status
        pins_today = len(get_today_uploads(today_str))
        all_time = get_all_time_stats().get("total", 0)
        pin_mode = "LIVE (Make.com Webhook)" if not DRY_RUN else "DRY-RUN (Simulated)"

        # Are.na status
        arena_status = "DISABLED (ARENA_ENABLED=false)"
        arena_blocks_str = ""
        if ARENA_ENABLED:
            arena_token_ok = verify_arena_token()
            if arena_token_ok:
                ch = get_arena_channel_info()
                blocks = ch.get("length", 0) if ch else "?"
                arena_status = "🟢 ACTIVE"
                arena_blocks_str = f" ({blocks}/200 blocks used)"
            else:
                arena_status = "🔴 AUTH ERROR (Check token)"
        arena_st = get_arena_stats(today_str)

        # Bluesky status
        bluesky_status = "DISABLED (BLUESKY_ENABLED=false)"
        bluesky_posts_str = ""
        if BLUESKY_ENABLED:
            from bluesky_uploader import verify_bluesky_credentials, get_bluesky_profile_info
            bsky_token_ok = verify_bluesky_credentials()
            if bsky_token_ok:
                bp = get_bluesky_profile_info()
                posts_cnt = bp.get("posts", 0) if bp else "?"
                bluesky_status = "🟢 ACTIVE"
                bluesky_posts_str = f" ({posts_cnt} posts)"
            else:
                bluesky_status = "🔴 AUTH ERROR (Check credentials)"
        bluesky_st = get_bluesky_stats(today_str)

        # Raindrop.io status
        raindrop_status = "DISABLED (RAINDROP_ENABLED=false)"
        raindrop_drops_str = ""
        raindrop_link = "https://app.raindrop.io"
        if RAINDROP_ENABLED:
            drop_token_ok = verify_raindrop_token()
            if drop_token_ok:
                rc = get_raindrop_collection_info()
                cnt = rc.get("count", 0) if rc else "?"
                raindrop_status = "🟢 ACTIVE"
                raindrop_drops_str = f" ({cnt} bookmarks)"
                if rc and rc.get("url"):
                    raindrop_link = rc["url"]
            else:
                raindrop_status = "🔴 AUTH ERROR (Check token)"
        raindrop_st = get_raindrop_stats(today_str)

        # Mastodon status
        mastodon_status = "DISABLED (MASTODON_ENABLED=false)"
        mastodon_posts_str = ""
        mastodon_link = f"{MASTODON_INSTANCE_URL}/@muthelyrics"
        if MASTODON_ENABLED:
            masto_token_ok = verify_mastodon_token()
            if masto_token_ok:
                mp = get_mastodon_profile_info()
                cnt = mp.get("statuses_count", 0) if mp else "?"
                mastodon_status = "🟢 ACTIVE"
                mastodon_posts_str = f" ({cnt} toots/posts)"
                if mp and mp.get("url"):
                    mastodon_link = mp["url"]
            else:
                mastodon_status = "🔴 AUTH ERROR (Check token)"
        mastodon_st = get_mastodon_stats(today_str)

        # Pixelfed status (omitted completely if disabled)
        pixelfed_block = ""
        if PIXELFED_ENABLED:
            from pixelfed_uploader import get_pixelfed_status, get_pixelfed_profile_info
            p_status = get_pixelfed_status()
            pixelfed_posts_str = ""
            pixelfed_link = PIXELFED_INSTANCE_URL
            if p_status.get("authenticated"):
                pp = get_pixelfed_profile_info()
                cnt = pp.get("statuses_count", 0) if pp else "?"
                pixelfed_status = "🟢 ACTIVE"
                pixelfed_posts_str = f" ({cnt} posts)"
                if pp and pp.get("url"):
                    pixelfed_link = pp["url"]
            elif p_status.get("status_code") == 401 or p_status.get("state") == "unauthenticated":
                pixelfed_status = "🔴 AUTH ERROR (HTTP 401: Token invalid or expired)"
            elif p_status.get("state") == "missing_token":
                pixelfed_status = "🔴 TOKEN MISSING (Check PIXELFED_ACCESS_TOKEN)"
            else:
                pixelfed_status = f"🔴 ERROR ({p_status.get('message', 'Check configuration')})"
            pixelfed_st = get_pixelfed_stats(today_str)
            pixelfed_block = (
                f"📷 Pixelfed:\n"
                f"  • Status: {pixelfed_status}{pixelfed_posts_str}\n"
                f"  • Posts: {pixelfed_st['today']} today  |  {pixelfed_st['total']} all-time\n"
                f"  • Link: {pixelfed_link}\n\n"
            )

        # Freeimage status
        freeimage_status = "DISABLED (FREEIMAGE_ENABLED=false)"
        if FREEIMAGE_ENABLED:
            from freeimage_uploader import verify_freeimage_token
            freeimage_status = "🟢 ACTIVE (API Key Verified)" if verify_freeimage_token() else "🟡 REACHABLE"
        freeimage_st = get_freeimage_stats(today_str)

        # Imghippo status
        imghippo_status = "DISABLED (IMGHIPPO_ENABLED=false)"
        from config import IMGHIPPO_ENABLED
        if IMGHIPPO_ENABLED:
            from imghippo_uploader import verify_imghippo_token
            imghippo_status = "🟢 ACTIVE (API Key Verified)" if verify_imghippo_token() else "🟡 REACHABLE"
        imghippo_st = get_imghippo_stats(today_str)

        # Wallhaven status
        wallhaven_status = "DISABLED (WALLHAVEN_ENABLED=false)"
        from config import WALLHAVEN_ENABLED, WALLHAVEN_USERNAME
        if WALLHAVEN_ENABLED:
            from wallhaven_uploader import verify_wallhaven_token
            wallhaven_status = "🟢 ACTIVE (Account & API Reachable)" if verify_wallhaven_token() else "🟡 REACHABLE"
        from database import get_wallhaven_stats
        wallhaven_st = get_wallhaven_stats(today_str)

        # DeviantArt status
        deviantart_status = "DISABLED (DEVIANTART_ENABLED=false)"
        from config import DEVIANTART_ENABLED
        if DEVIANTART_ENABLED:
            from deviantart_uploader import verify_deviantart_token
            deviantart_status = "🟢 ACTIVE (@muthelyrics)" if verify_deviantart_token() else "🔴 AUTH ERROR"
        from database import get_deviantart_stats
        deviantart_st = get_deviantart_stats(today_str)

        # ImgBB status
        imgbb_status = "DISABLED (IMGBB_ENABLED=false)"
        from config import IMGBB_ENABLED
        if IMGBB_ENABLED:
            from imgbb_uploader import verify_imgbb_token, is_imgbb_maintenance
            if verify_imgbb_token():
                imgbb_status = "🟢 ACTIVE (API Key Verified)"
            elif is_imgbb_maintenance():
                imgbb_status = "🟡 MAINTENANCE (Down for maintenance)"
            else:
                imgbb_status = "🔴 KEY ERROR"
        from database import get_imgbb_stats
        imgbb_st = get_imgbb_stats(today_str)

        # ImageKit status
        imagekit_status = "DISABLED (IMAGEKIT_ENABLED=false)"
        from config import IMAGEKIT_ENABLED
        if IMAGEKIT_ENABLED:
            from imagekit_uploader import verify_imagekit_token
            imagekit_status = "🟢 ACTIVE (Mumbai CDN Connected)" if verify_imagekit_token() else "🔴 KEY ERROR"
        from database import get_imagekit_stats
        imagekit_st = get_imagekit_stats(today_str)

        # ImgPile status
        imgpile_status = "DISABLED (IMGPILE_ENABLED=false)"
        from config import IMGPILE_ENABLED
        if IMGPILE_ENABLED:
            from imgpile_uploader import verify_imgpile_token
            imgpile_status = "🟢 ACTIVE (Bearer Token Connected)" if verify_imgpile_token() else "🔴 KEY ERROR"
        from database import get_imgpile_stats
        imgpile_st = get_imgpile_stats(today_str)


        msg = (
            f"🌐 Multi-Platform Cross-Post Hub\n"
            f"{'═' * 38}\n\n"
            f"📌 Pinterest:\n"
            f"  • Mode: {pin_mode}\n"
            f"  • Posts Today: {pins_today}  |  All-time: {all_time}\n\n"
            f"🔮 Are.na:\n"
            f"  • Status: {arena_status}{arena_blocks_str}\n"
            f"  • Channel: {ARENA_CHANNEL_SLUG}\n"
            f"  • Posts: {arena_st['today']} today  |  {arena_st['total']} all-time\n"
            f"  • Link: https://www.are.na/manoj-muthelyrics/{ARENA_CHANNEL_SLUG}\n\n"
            f"🦋 Bluesky:\n"
            f"  • Status: {bluesky_status}{bluesky_posts_str}\n"
            f"  • Handle: @{BLUESKY_HANDLE or 'NOT SET'}\n"
            f"  • Posts: {bluesky_st['today']} today  |  {bluesky_st['total']} all-time\n"
            f"  • Link: https://bsky.app/profile/{BLUESKY_HANDLE}\n\n"
            f"💧 Raindrop.io:\n"
            f"  • Status: {raindrop_status}{raindrop_drops_str}\n"
            f"  • Collection ID: {RAINDROP_COLLECTION_ID}\n"
            f"  • Posts: {raindrop_st['today']} today  |  {raindrop_st['total']} all-time\n"
            f"  • Link: {raindrop_link}\n\n"
            f"🐘 Mastodon:\n"
            f"  • Status: {mastodon_status}{mastodon_posts_str}\n"
            f"  • Posts: {mastodon_st['today']} today  |  {mastodon_st['total']} all-time\n"
            f"  • Link: {mastodon_link}\n\n"
            f"{pixelfed_block}"
            f"🖼️ Freeimage.host:\n"
            f"  • Status: {freeimage_status}\n"
            f"  • Posts: {freeimage_st['today']} today  |  {freeimage_st['total']} all-time\n"
            f"  • Link: https://freeimage.host/muthelyrics\n\n"
            f"🦛 Imghippo:\n"
            f"  • Status: {imghippo_status}\n"
            f"  • Posts: {imghippo_st['today']} today  |  {imghippo_st['total']} all-time\n"
            f"  • Link: https://www.imghippo.com/dashboard\n\n"
            f"🌌 Wallhaven.cc:\n"
            f"  • Status: {wallhaven_status}\n"
            f"  • Username: @{WALLHAVEN_USERNAME}\n"
            f"  • Posts: {wallhaven_st['today']} today  |  {wallhaven_st['total']} all-time\n"
            f"  • Link: https://wallhaven.cc/user/{WALLHAVEN_USERNAME}\n\n"
            f"🎨 DeviantArt:\n"
            f"  • Status: {deviantart_status}\n"
            f"  • Posts: {deviantart_st['today']} today  |  {deviantart_st['total']} all-time\n"
            f"  • Link: https://www.deviantart.com/muthelyrics\n\n"
            f"🖼️ ImgBB:\n"
            f"  • Status: {imgbb_status}\n"
            f"  • Posts: {imgbb_st['today']} today  |  {imgbb_st['total']} all-time\n"
            f"  • Link: https://muthelyrics.imgbb.com/\n\n"
            f"🖼️ ImageKit.io:\n"
            f"  • Status: {imagekit_status}\n"
            f"  • Posts: {imagekit_st['today']} today  |  {imagekit_st['total']} all-time\n"
            f"  • CDN Endpoint: https://ik.imagekit.io/zno3dgy8z\n\n"
            f"🗄️ ImgPile:\n"
            f"  • Status: {imgpile_status}\n"
            f"  • Posts: {imgpile_st['today']} today  |  {imgpile_st['total']} all-time\n"
            f"  • Link: https://imgpile.com\n\n"

            f"🚀 Quick Commands:\n"
            f"  /summary — Today's multi-platform report\n"
            f"  /wallhaven — Wallhaven account stats & link\n"
            f"  /wallhaven_search — Search 4K anime wallpapers\n"
            f"  /arena_test — Test Are.na block upload\n"
            f"  /bluesky_test — Test Bluesky image post\n"
            f"  /raindrop_test — Test Raindrop bookmark upload\n"
            f"  /mastodon_test — Test Mastodon status & image post\n"
            f"  /pixelfed_test — Test Pixelfed photo & caption post\n"
            f"  /freeimage_test — Test Freeimage.host upload\n"
            f"  /imghippo_test — Test Imghippo upload\n"
            f"  /testpost — Test Pinterest webhook"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_crosspost error: {e}", exc_info=True)
        await update.message.reply_text(f"Cross-post status error: {e}")


async def cmd_deviantart(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show DeviantArt gallery and token status."""
    if not _is_admin(update): return
    try:
        from config import DEVIANTART_ENABLED, DEVIANTART_CLIENT_ID
        from deviantart_uploader import verify_deviantart_token
        from database import get_deviantart_stats
        
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%Y-%m-%d")
        
        enabled_str = "🟢 ENABLED" if DEVIANTART_ENABLED else "🔴 DISABLED"
        valid = verify_deviantart_token()
        status_str = "🟢 Token Active (Auth OK)" if valid else "🔴 Token Expired / Auth Error"
        
        stats = get_deviantart_stats(today_str)
        today_cnt = stats.get("today", 0)
        total_cnt = stats.get("total", 0)
        
        msg = (
            f"🎨 DeviantArt Status: {enabled_str}\n"
            f"{'═' * 30}\n"
            f"• Auth Status : {status_str}\n"
            f"• Client ID   : {DEVIANTART_CLIENT_ID}\n"
            f"• Today Posts : {today_cnt}\n"
            f"• Total Posts : {total_cnt}\n"
            f"• Gallery URL : https://www.deviantart.com/muthelyrics\n"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_deviantart error: {e}", exc_info=True)
        await update.message.reply_text(f"DeviantArt error: {e}")




async def cmd_imgbb(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show ImgBB gallery and API key status."""
    if not _is_admin(update): return
    try:
        from config import IMGBB_ENABLED
        from imgbb_uploader import verify_imgbb_token
        from database import get_imgbb_stats
        
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%Y-%m-%d")
        
        enabled_str = "🟢 ENABLED" if IMGBB_ENABLED else "🔴 DISABLED"
        valid = verify_imgbb_token()
        status_str = "🟢 API Key Active (Auth OK)" if valid else "🔴 API Key Error"
        
        stats = get_imgbb_stats(today_str)
        today_cnt = stats.get("today", 0)
        total_cnt = stats.get("total", 0)
        
        msg = (
            f"🖼️ ImgBB Status: {enabled_str}\n"
            f"{'═' * 30}\n"
            f"• Auth Status : {status_str}\n"
            f"• Today Posts : {today_cnt}\n"
            f"• Total Posts : {total_cnt}\n"
            f"• Profile URL : https://muthelyrics.imgbb.com/\n"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_imgbb error: {e}", exc_info=True)
        await update.message.reply_text(f"ImgBB error: {e}")


async def cmd_imagekit(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show ImageKit CDN status and stats."""
    if not _is_admin(update): return
    try:
        from config import IMAGEKIT_ENABLED, IMAGEKIT_URL_ENDPOINT
        from imagekit_uploader import verify_imagekit_token, get_imagekit_info
        from database import get_imagekit_stats
        
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%Y-%m-%d")
        
        enabled_str = "🟢 ENABLED" if IMAGEKIT_ENABLED else "🔴 DISABLED"
        valid = verify_imagekit_token()
        status_str = "🟢 Active (Mumbai CDN Connected)" if valid else "🔴 API Key Error"
        
        stats = get_imagekit_stats(today_str)
        today_cnt = stats.get("today", 0)
        total_cnt = stats.get("total", 0)
        info = get_imagekit_info()
        
        msg = (
            f"🖼️ ImageKit.io Status: {enabled_str}\n"
            f"{'═' * 30}\n"
            f"• Auth Status : {status_str}\n"
            f"• Today Posts : {today_cnt}\n"
            f"• Total Posts : {total_cnt}\n"
            f"• Public Key  : {info.get('public_key_masked')}\n"
            f"• Private Key : {info.get('private_key_masked')}\n"
            f"• CDN Endpoint: {info.get('url_endpoint')}\n"
            f"• Region      : Mumbai (India) 🇮🇳\n"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_imagekit error: {e}", exc_info=True)
        await update.message.reply_text(f"ImageKit error: {e}")


async def cmd_imgpile(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show ImgPile hosting status and stats."""
    if not _is_admin(update): return
    try:
        from config import IMGPILE_ENABLED
        from imgpile_uploader import verify_imgpile_token, get_imgpile_info
        from database import get_imgpile_stats
        
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_str = now_ist.strftime("%Y-%m-%d")
        
        enabled_str = "🟢 ENABLED" if IMGPILE_ENABLED else "🔴 DISABLED"
        valid = verify_imgpile_token()
        status_str = "🟢 Active (Bearer Key Connected)" if valid else "🔴 API Key Error"
        
        stats = get_imgpile_stats(today_str)
        today_cnt = stats.get("today", 0)
        total_cnt = stats.get("total", 0)
        info = get_imgpile_info()
        
        msg = (
            f"🗄️ ImgPile Status: {enabled_str}\n"
            f"{'═' * 30}\n"
            f"• Auth Status : {status_str}\n"
            f"• Today Posts : {today_cnt}\n"
            f"• Total Posts : {total_cnt}\n"
            f"• API Key     : {info.get('api_key_masked')}\n"
            f"• Gallery URL : https://imgpile.com\n"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_imgpile error: {e}", exc_info=True)
        await update.message.reply_text(f"ImgPile error: {e}")



async def cmd_bluesky(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Bluesky profile stats: handle, followers, post count."""
    if not _is_admin(update): return
    try:
        from bluesky_uploader import get_bluesky_profile_info, verify_bluesky_credentials
        from config import BLUESKY_ENABLED, BLUESKY_HANDLE
        enabled_str = "ENABLED" if BLUESKY_ENABLED else "DISABLED (set BLUESKY_ENABLED=true)"
        valid = verify_bluesky_credentials()
        info = get_bluesky_profile_info() if valid else None
        if info:
            msg = (
                f"🦋 Bluesky Status: {enabled_str}\n"
                f"{'═' * 30}\n"
                f"Handle    : @{info['handle']}\n"
                f"Name      : {info['display_name']}\n"
                f"Followers : {info['followers']}\n"
                f"Posts     : {info['posts']}\n"
                f"Profile   : {info['url']}"
            )
        elif valid:
            msg = (
                f"🦋 Bluesky: {enabled_str}\n"
                f"Handle: @{BLUESKY_HANDLE}\n"
                f"Credentials valid, but profile info could not be fetched."
            )
        else:
            msg = (
                f"🦋 Bluesky: {enabled_str}\n"
                f"Handle: @{BLUESKY_HANDLE or 'NOT SET'}\n"
                f"Status: 🔴 Authentication failed.\n"
                f"Check BLUESKY_HANDLE & BLUESKY_APP_PASSWORD in .env"
            )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_bluesky error: {e}", exc_info=True)
        await update.message.reply_text(f"Bluesky error: {e}")


async def cmd_bluesky_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Post a test photo to Bluesky to verify credentials."""
    if not _is_admin(update): return
    await update.message.reply_text("Posting test image to Bluesky...")
    try:
        from bluesky_uploader import post_to_bluesky
        from config import BLUESKY_HANDLE
        uri = post_to_bluesky(
            image_url="https://res.cloudinary.com/demo/image/upload/sample.jpg",
            title="Anime Aesthetic — Pinterest Bot Test",
            caption="Automated test post from Pinterest Bot Bluesky integration! #anime #aesthetic",
            link="https://amazon.in",
            tags=["anime", "aesthetic", "bot", "test"],
        )
        if uri:
            await update.message.reply_text(
                f"✅ Test image posted to Bluesky!\n"
                f"Post URI: {uri}\n"
                f"Check: https://bsky.app/profile/{BLUESKY_HANDLE}"
            )
        else:
            await update.message.reply_text(
                "❌ Bluesky test FAILED. Check:\n"
                "1. BLUESKY_HANDLE is correct in .env\n"
                "2. BLUESKY_APP_PASSWORD is valid\n"
                "3. BLUESKY_ENABLED=true in .env\n"
                "Run /logs for details."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_bluesky_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Bluesky test error: {e}")


async def cmd_raindrop(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Raindrop.io collection stats and public URL."""
    if not _is_admin(update): return
    try:
        from raindrop_uploader import get_raindrop_collection_info, verify_raindrop_token
        from config import RAINDROP_ENABLED, RAINDROP_COLLECTION_ID
        enabled_str = "ENABLED" if RAINDROP_ENABLED else "DISABLED (set RAINDROP_ENABLED=true)"
        valid = verify_raindrop_token()
        info = get_raindrop_collection_info() if valid else None
        if info:
            msg = (
                f"💧 Raindrop.io Status: {enabled_str}\n"
                f"{'═' * 30}\n"
                f"Collection : {info['title']}\n"
                f"Bookmarks  : {info['count']}\n"
                f"Public URL : {info['url']}"
            )
        elif valid:
            msg = (
                f"💧 Raindrop.io: {enabled_str}\n"
                f"Token: Valid ✅\n"
                f"Collection ID: {RAINDROP_COLLECTION_ID}"
            )
        else:
            msg = "💧 Raindrop.io: 🔴 Token invalid or not configured. Check RAINDROP_ACCESS_TOKEN in .env"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_raindrop error: {e}", exc_info=True)
        await update.message.reply_text(f"Raindrop error: {e}")


async def cmd_raindrop_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Post a test bookmark to Raindrop.io."""
    if not _is_admin(update): return
    await update.message.reply_text("⏳ Posting test bookmark to Raindrop.io...")
    try:
        from raindrop_uploader import post_to_raindrop, get_raindrop_collection_info
        test_url = "https://i.pinimg.com/originals/26/5d/2a/265d2a939f50e82c5f11cb7596ff1f7c.jpg"
        ok = post_to_raindrop(
            image_url=test_url,
            title="[Test] Raindrop Integration - Anime Poster",
            description="Test visual bookmark posted from Telegram bot.",
            link="https://www.pinterest.com",
        )
        if ok:
            info = get_raindrop_collection_info()
            url_str = f"\nCollection: {info['url']}" if info else ""
            await update.message.reply_text(f"✅ Raindrop.io test bookmark created successfully!{url_str}")
        else:
            await update.message.reply_text(
                "❌ Raindrop test bookmark failed.\n"
                "Check that RAINDROP_ACCESS_TOKEN and RAINDROP_COLLECTION_ID are set in .env."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_raindrop_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Raindrop test error: {e}")


async def cmd_mastodon(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Mastodon account stats and profile URL."""
    if not _is_admin(update): return
    try:
        from mastodon_uploader import get_mastodon_profile_info, verify_mastodon_token
        from config import MASTODON_ENABLED, MASTODON_INSTANCE_URL
        enabled_str = "ENABLED" if MASTODON_ENABLED else "DISABLED (set MASTODON_ENABLED=true)"
        valid = verify_mastodon_token()
        info = get_mastodon_profile_info() if valid else None
        if info:
            msg = (
                f"🐘 Mastodon Status: {enabled_str}\n"
                f"{'═' * 30}\n"
                f"Username  : @{info['username']}\n"
                f"Instance  : {MASTODON_INSTANCE_URL}\n"
                f"Followers : {info['followers_count']}\n"
                f"Toots     : {info['statuses_count']}\n"
                f"Profile   : {info['url']}"
            )
        elif valid:
            msg = (
                f"🐘 Mastodon: {enabled_str}\n"
                f"Token: Valid ✅\n"
                f"Instance: {MASTODON_INSTANCE_URL}"
            )
        else:
            msg = "🐘 Mastodon: 🔴 Token invalid or not configured. Check MASTODON_ACCESS_TOKEN in .env"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_mastodon error: {e}", exc_info=True)
        await update.message.reply_text(f"Mastodon error: {e}")


async def cmd_mastodon_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Post a test status with image to Mastodon."""
    if not _is_admin(update): return
    await update.message.reply_text("⏳ Posting test image & status to Mastodon...")
    try:
        from mastodon_uploader import post_to_mastodon
        test_url = "https://i.pinimg.com/originals/26/5d/2a/265d2a939f50e82c5f11cb7596ff1f7c.jpg"
        post_url = post_to_mastodon(
            image_url=test_url,
            title="[Test] Anime Aesthetic Poster - Mastodon Integration",
            description="Testing automated multi-platform cross-posting from Telegram Bot.",
            link="https://www.pinterest.com",
        )
        if post_url:
            await update.message.reply_text(f"✅ Mastodon test post created successfully!\nLink: {post_url}")
        else:
            await update.message.reply_text(
                "❌ Mastodon test post failed.\n"
                "Check that MASTODON_ACCESS_TOKEN is set in .env."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_mastodon_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Mastodon test error: {e}")




async def cmd_pixelfed(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Pixelfed account stats and profile URL."""
    if not _is_admin(update): return
    try:
        from config import PIXELFED_ENABLED, PIXELFED_INSTANCE_URL
        if not PIXELFED_ENABLED:
            await update.message.reply_text(
                "📷 Pixelfed: ⚪ DISABLED\n"
                "Pixelfed cross-posting is currently disabled. Set PIXELFED_ENABLED=true in .env to enable."
            )
            return

        from pixelfed_uploader import get_pixelfed_profile_info, get_pixelfed_status
        status = get_pixelfed_status()
        if status.get("authenticated"):
            info = get_pixelfed_profile_info()
            if info:
                msg = (
                    f"📷 Pixelfed Status: ENABLED\n"
                    f"{'═' * 30}\n"
                    f"Username  : @{info['username']}\n"
                    f"Instance  : {PIXELFED_INSTANCE_URL}\n"
                    f"Followers : {info['followers_count']}\n"
                    f"Posts     : {info['statuses_count']}\n"
                    f"Profile   : {info['url']}"
                )
            else:
                msg = (
                    f"📷 Pixelfed: ENABLED\n"
                    f"Token: Valid ✅\n"
                    f"Instance: {PIXELFED_INSTANCE_URL}"
                )
        elif status.get("status_code") == 401 or status.get("state") == "unauthenticated":
            msg = "📷 Pixelfed: 🔴 HTTP 401 Unauthorized. Access token is invalid or expired. Check PIXELFED_ACCESS_TOKEN in .env"
        elif status.get("state") == "missing_token":
            msg = "📷 Pixelfed: 🔴 Token missing. Set PIXELFED_ACCESS_TOKEN in .env"
        else:
            msg = f"📷 Pixelfed: 🔴 {status.get('message', 'Verification failed.')}"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_pixelfed error: {e}", exc_info=True)
        await update.message.reply_text(f"Pixelfed error: {e}")


async def cmd_pixelfed_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Post a test photo to Pixelfed with real anime metadata & Amazon affiliate link."""
    if not _is_admin(update): return
    await update.message.reply_text("⏳ Posting real anime test poster to Pixelfed...")
    try:
        from pixelfed_uploader import post_to_pixelfed
        local_img = None
        for folder in ["processed", "downloads"]:
            if os.path.exists(folder):
                for f in sorted(os.listdir(folder), reverse=True):
                    if f.lower().endswith((".jpg", ".jpeg", ".png")):
                        local_img = os.path.join(folder, f)
                        break
            if local_img:
                break

        anime_name = "Attack on Titan"
        title = "Attack on Titan Eren Yeager Aesthetic Poster"
        if local_img:
            fn = os.path.basename(local_img).lower()
            if "naruto" in fn:
                anime_name = "Naruto Shippuden"
                title = "Naruto Uzumaki Sage Mode Minimalist Poster"
            elif "aot" in fn or "titan" in fn:
                anime_name = "Attack on Titan"
                title = "Attack on Titan Eren Yeager Aesthetic Poster"
            elif "demon" in fn or "slayer" in fn or "tanjiro" in fn:
                anime_name = "Demon Slayer"
                title = "Demon Slayer Tanjiro Kamado Canvas Poster"
            elif "jujutsu" in fn or "jjk" in fn or "gojo" in fn:
                anime_name = "Jujutsu Kaisen"
                title = "Gojo Satoru Domain Expansion Anime Poster"
            elif "solo" in fn or "leveling" in fn:
                anime_name = "Solo Leveling"
                title = "Solo Leveling Sung Jin-Woo Shadow Monarch Poster"

        from amazon_search import generate_amazon_link
        real_affiliate_link = generate_amazon_link(anime_name, title=title)
        real_desc = f"Aesthetic high-resolution wall art poster for bedroom and workspace decor.\n🎌 Series: {anime_name}"

        post_url = post_to_pixelfed(
            image_url="",
            title=title,
            description=real_desc,
            link=real_affiliate_link,
            image_path=local_img,
        )
        if post_url:
            await update.message.reply_text(
                f"✅ Pixelfed test post created with REAL anime data!\n\n"
                f"🎌 Anime: {anime_name}\n"
                f"📝 Title: {title}\n"
                f"🛍️ Amazon Merch: {real_affiliate_link}\n"
                f"🔗 Pixelfed Link: {post_url}"
            )
        else:
            await update.message.reply_text(
                "❌ Pixelfed test post failed.\n"
                "Check that PIXELFED_ACCESS_TOKEN and PIXELFED_INSTANCE_URL are set in .env."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_pixelfed_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Pixelfed test error: {e}")


async def cmd_freeimage(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Freeimage.host API stats and gallery link."""
    if not _is_admin(update): return
    try:
        from freeimage_uploader import get_freeimage_info, verify_freeimage_token
        from database import get_freeimage_stats
        from config import FREEIMAGE_ENABLED
        enabled_str = "ENABLED" if FREEIMAGE_ENABLED else "DISABLED (set FREEIMAGE_ENABLED=true)"
        valid = verify_freeimage_token()
        info = get_freeimage_info()
        stats = get_freeimage_stats()

        recent_lines = []
        for i, r in enumerate(stats.get("recent", [])[:3], 1):
            recent_lines.append(f"  {i}. {r['title'][:30]} -> {r['post_url']}")
        recent_str = "\n".join(recent_lines) if recent_lines else "  No uploads recorded yet."

        msg = (
            f"🖼️ Freeimage.host Status: {enabled_str}\n"
            f"{'═' * 34}\n"
            f"API Status: {'🟢 Verified & Connected' if valid else '🟡 Reachable'}\n"
            f"API Key   : {info['api_key_masked']}\n"
            f"Profile   : {info['profile_url']}\n\n"
            f"📊 Upload Statistics:\n"
            f"  • Today    : {stats['today']} images\n"
            f"  • All-Time : {stats['total']} images\n\n"
            f"Recent Uploads:\n{recent_str}\n\n"
            f"👉 Use /freeimage_test to post a live anime test poster."
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_freeimage error: {e}", exc_info=True)
        await update.message.reply_text(f"Freeimage error: {e}")


async def cmd_freeimage_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Upload a test anime poster with real anime metadata & Amazon affiliate link to Freeimage.host."""
    if not _is_admin(update): return
    await update.message.reply_text("⏳ Uploading real anime test poster to Freeimage.host...")
    try:
        from freeimage_uploader import post_to_freeimage
        from database import mark_freeimage_posted
        local_img = None
        for folder in ["processed", "downloads"]:
            if os.path.exists(folder):
                for f in sorted(os.listdir(folder), reverse=True):
                    if f.lower().endswith((".jpg", ".jpeg", ".png")):
                        local_img = os.path.join(folder, f)
                        break
            if local_img:
                break

        anime_name = "Attack on Titan"
        title = "Attack on Titan Eren Yeager Aesthetic Poster"
        if local_img:
            fn = os.path.basename(local_img).lower()
            if "naruto" in fn:
                anime_name = "Naruto Shippuden"
                title = "Naruto Uzumaki Sage Mode Minimalist Poster"
            elif "aot" in fn or "titan" in fn:
                anime_name = "Attack on Titan"
                title = "Attack on Titan Eren Yeager Aesthetic Poster"
            elif "demon" in fn or "slayer" in fn or "tanjiro" in fn:
                anime_name = "Demon Slayer"
                title = "Demon Slayer Tanjiro Kamado Canvas Poster"
            elif "jujutsu" in fn or "jjk" in fn or "gojo" in fn:
                anime_name = "Jujutsu Kaisen"
                title = "Gojo Satoru Domain Expansion Anime Poster"
            elif "solo" in fn or "leveling" in fn:
                anime_name = "Solo Leveling"
                title = "Solo Leveling Sung Jin-Woo Shadow Monarch Poster"

        from amazon_search import generate_amazon_link
        real_affiliate_link = generate_amazon_link(anime_name, title=title)

        post_url = post_to_freeimage(
            image_path=local_img or "downloads/test.jpg",
            title=title,
            anime_name=anime_name,
            affiliate_url=real_affiliate_link
        )
        if post_url:
            if local_img:
                mark_freeimage_posted(os.path.basename(local_img), post_url=post_url, title=title)
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🖼️ View on Freeimage", url=post_url),
                InlineKeyboardButton("🛒 View Amazon Merch", url=real_affiliate_link),
            ]])
            await update.message.reply_text(
                f"✅ Freeimage.host upload succeeded with REAL anime data!\n\n"
                f"🎌 Anime: {anime_name}\n"
                f"📝 Title: {title}\n"
                f"🛍️ Amazon Merch: {real_affiliate_link}\n"
                f"🔗 Freeimage URL: {post_url}",
                reply_markup=btn
            )
        else:
            await update.message.reply_text(
                "❌ Freeimage.host test upload failed.\n"
                "Check that FREEIMAGE_API_KEY is set in .env."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_freeimage_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Freeimage test error: {e}")




async def cmd_imghippo(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Imghippo API stats and dashboard link."""
    if not _is_admin(update): return
    try:
        from imghippo_uploader import get_imghippo_info, verify_imghippo_token
        from database import get_imghippo_stats
        from config import IMGHIPPO_ENABLED
        enabled_str = "ENABLED" if IMGHIPPO_ENABLED else "DISABLED (set IMGHIPPO_ENABLED=true)"
        valid = verify_imghippo_token()
        info = get_imghippo_info()
        stats = get_imghippo_stats()

        recent_lines = []
        for i, r in enumerate(stats.get("recent", [])[:3], 1):
            recent_lines.append(f"  {i}. {r['title'][:30]} -> {r['post_url']}")
        recent_str = "\n".join(recent_lines) if recent_lines else "  No uploads recorded yet."

        msg = (
            f"🦛 Imghippo Status: {enabled_str}\n"
            f"{'═' * 34}\n"
            f"API Status: {'🟢 Verified & Connected' if valid else '🟡 Reachable'}\n"
            f"API Key   : {info['api_key_masked']}\n"
            f"Dashboard : {info['profile_url']}\n\n"
            f"📊 Upload Statistics:\n"
            f"  • Today    : {stats['today']} images\n"
            f"  • All-Time : {stats['total']} images\n\n"
            f"Recent Uploads:\n{recent_str}\n\n"
            f"👉 Use /imghippo_test to post a live anime test poster."
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_imghippo error: {e}", exc_info=True)
        await update.message.reply_text(f"Imghippo error: {e}")


async def cmd_imghippo_test(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Upload a test anime poster with real anime metadata & Amazon affiliate link to Imghippo."""
    if not _is_admin(update): return
    await update.message.reply_text("⏳ Uploading real anime test poster to Imghippo...")
    try:
        from imghippo_uploader import post_to_imghippo
        from database import mark_imghippo_posted
        local_img = None
        for folder in ["processed", "downloads"]:
            if os.path.exists(folder):
                for f in sorted(os.listdir(folder), reverse=True):
                    if f.lower().endswith((".jpg", ".jpeg", ".png")):
                        local_img = os.path.join(folder, f)
                        break
            if local_img:
                break

        anime_name = "Attack on Titan"
        title = "Attack on Titan Eren Yeager Aesthetic Poster"
        if local_img:
            fn = os.path.basename(local_img).lower()
            if "naruto" in fn:
                anime_name = "Naruto Shippuden"
                title = "Naruto Uzumaki Sage Mode Minimalist Poster"
            elif "aot" in fn or "titan" in fn:
                anime_name = "Attack on Titan"
                title = "Attack on Titan Eren Yeager Aesthetic Poster"
            elif "demon" in fn or "slayer" in fn or "tanjiro" in fn:
                anime_name = "Demon Slayer"
                title = "Demon Slayer Tanjiro Kamado Canvas Poster"
            elif "jujutsu" in fn or "jjk" in fn or "gojo" in fn:
                anime_name = "Jujutsu Kaisen"
                title = "Gojo Satoru Domain Expansion Anime Poster"
            elif "solo" in fn or "leveling" in fn:
                anime_name = "Solo Leveling"
                title = "Solo Leveling Sung Jin-Woo Shadow Monarch Poster"

        from amazon_search import generate_amazon_link
        real_affiliate_link = generate_amazon_link(anime_name, title=title)

        post_url = post_to_imghippo(
            image_path=local_img or "downloads/test.jpg",
            title=title,
            anime_name=anime_name,
            affiliate_url=real_affiliate_link
        )
        if post_url:
            if local_img:
                mark_imghippo_posted(os.path.basename(local_img), post_url=post_url, title=title)
            btn = InlineKeyboardMarkup([[
                InlineKeyboardButton("🦛 View on Imghippo", url=post_url),
                InlineKeyboardButton("🛒 View Amazon Merch", url=real_affiliate_link),
            ]])
            await update.message.reply_text(
                f"✅ Imghippo upload succeeded with REAL anime data!\n\n"
                f"🎌 Anime: {anime_name}\n"
                f"📝 Title: {title}\n"
                f"🛍️ Amazon Merch: {real_affiliate_link}\n"
                f"🔗 Direct Imghippo URL: {post_url}",
                reply_markup=btn
            )
        else:
            await update.message.reply_text(
                "❌ Imghippo test upload failed.\n"
                "Check that IMGHIPPO_API_KEY is set in .env."
            )
    except Exception as e:
        logger.error(f"[TG BOT] cmd_imghippo_test error: {e}", exc_info=True)
        await update.message.reply_text(f"Imghippo test error: {e}")


async def cmd_wallhaven(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show Wallhaven account status, profile & collection links, and upload stats."""
    if not _is_admin(update): return
    try:
        from wallhaven_uploader import get_wallhaven_info, verify_wallhaven_token
        from database import get_wallhaven_stats
        import config
        enabled_str = "ENABLED" if getattr(config, "WALLHAVEN_ENABLED", False) else "DISABLED (set WALLHAVEN_ENABLED=true)"
        valid = verify_wallhaven_token()
        info = get_wallhaven_info()
        stats = get_wallhaven_stats()

        recent_lines = []
        for i, r in enumerate(stats.get("recent", [])[:3], 1):
            recent_lines.append(f"  {i}. {r['title'][:30]} -> {r['post_url']}")
        recent_str = "\n".join(recent_lines) if recent_lines else "  No syndications recorded yet."

        btn = InlineKeyboardMarkup([[
            InlineKeyboardButton("🌌 Wallhaven Profile", url=info["profile_url"]),
            InlineKeyboardButton("🖼️ Upload Art", url=info["upload_url"]),
        ]])

        msg = (
            f"🌌 Wallhaven.cc Status: {enabled_str}\n"
            f"{'═' * 34}\n"
            f"Username  : @{info['username']}\n"
            f"API Status: {'🟢 Verified & Connected' if valid else '🟡 Reachable'}\n"
            f"API Key   : {info['api_key_masked'] or 'Not Configured'}\n"
            f"Profile   : {info['profile_url']}\n"
            f"Authority : {info.get('domain_authority', 'DA 75+')}\n\n"
            f"📊 Syndication Statistics:\n"
            f"  • Today    : {stats['today']} posts\n"
            f"  • All-Time : {stats['total']} posts\n\n"
            f"Recent Showcase:\n{recent_str}\n\n"
            f"👉 Use /wallhaven_search <anime> to find 4K wallpapers & verified tags.\n"
            f"👉 Use /set_wallhaven_key <key> to update API key."
        )
        await update.message.reply_text(msg, reply_markup=btn)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_wallhaven error: {e}", exc_info=True)
        await update.message.reply_text(f"Wallhaven error: {e}")


async def cmd_wallhaven_search(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Search Wallhaven for 4K anime wallpapers and community tags."""
    if not _is_admin(update): return
    query = " ".join(context.args).strip() if context and context.args else ""
    if not query:
        await update.message.reply_text(
            "ℹ️ Usage: /wallhaven_search <anime or character>\n"
            "Example: /wallhaven_search Gojo Satoru\n"
            "Example: /wallhaven_search Demon Slayer"
        )
        return

    await update.message.reply_text(f"🔍 Searching Wallhaven for 4K '{query}' wallpapers...")
    try:
        from wallhaven_uploader import search_anime_wallpapers, fetch_wallhaven_tags
        wallpapers = search_anime_wallpapers(query=query, categories="010", purity="100", sorting="toplist")
        if not wallpapers:
            await update.message.reply_text(f"No wallpapers found on Wallhaven for '{query}'.")
            return

        tags = fetch_wallhaven_tags(query)
        tags_str = ", ".join(tags[:8]) if tags else "anime, anime art, wallpaper"

        lines = [f"🌌 Wallhaven Results for '{query}' (Found {len(wallpapers)}):\n{'─' * 34}"]
        buttons = []
        for i, wp in enumerate(wallpapers[:3], 1):
            wp_id = wp.get("id", "")
            res = wp.get("resolution", "")
            url = wp.get("url", f"https://wallhaven.cc/w/{wp_id}")
            lines.append(f"{i}. Resolution: {res} | Ratio: {wp.get('ratio', '16:9')} | ID: {wp_id}")
            buttons.append([InlineKeyboardButton(f"🖼️ View #{i} ({res})", url=url)])

        lines.append(f"\n🏷️ Recommended Community Tags:\n#{tags_str.replace(', ', ' #')}")

        await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))
    except Exception as e:
        logger.error(f"[TG BOT] cmd_wallhaven_search error: {e}", exc_info=True)
        await update.message.reply_text(f"Wallhaven search error: {e}")


async def cmd_set_wallhaven_key(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Update Wallhaven API key directly in runtime and database metadata."""
    if not _is_admin(update): return
    new_key = context.args[0].strip() if context and context.args else ""
    if not new_key:
        await update.message.reply_text("ℹ️ Usage: /set_wallhaven_key <32-character API key>")
        return

    # Remove the message containing the secret from chat history.
    try:
        await update.message.delete()
    except Exception:
        pass

    try:
        from database import set_metadata
        set_metadata("wallhaven_api_key", new_key)
        import config
        config.WALLHAVEN_API_KEY = new_key
        config.WALLHAVEN_ENABLED = True
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Wallhaven API key updated successfully!\n"
            f"Key: {new_key[:6]}...{new_key[-4:] if len(new_key) > 10 else ''}\n"
            f"Use /wallhaven to verify connectivity."
        )
    except Exception as e:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"Failed to update key: {e}")


async def cmd_dedup_scan(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Admin command: /dedup_scan - Cleans duplicate items from queue and backfills visual hashes."""
    if not _is_admin(update): return
    status_msg = await update.message.reply_text(
        "🔍 *Scanning for duplicate artwork...*\n"
        "Comparing visual difference hashes (dHash) and SHA-256 fingerprints across queue and uploaded pins...",
        parse_mode="Markdown"
    )

    try:
        from database import clean_duplicate_queue_items, backfill_fingerprints_batch, _get_conn
        
        # 1. Backfill fingerprints for existing items
        backfilled = backfill_fingerprints_batch(limit=250)
        
        # 2. Clean duplicate queue items
        purged = clean_duplicate_queue_items(max_distance=4)
        
        # 3. Get total fingerprinted statistics
        with _get_conn() as conn:
            total_uploads = conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]
            fingerprinted_uploads = conn.execute(
                "SELECT COUNT(*) FROM uploaded_files WHERE phash IS NOT NULL AND phash != ''"
            ).fetchone()[0]
            queue_len = conn.execute("SELECT COUNT(*) FROM pin_queue").fetchone()[0]

        report = (
            "🛡️ *4-Layer Deduplication Scan Complete!*\n\n"
            f"• *Duplicates Purged from Queue:* `{purged}`\n"
            f"• *Missing Fingerprints Backfilled:* `{backfilled}`\n"
            f"• *Current Clean Queue Size:* `{queue_len}`\n"
            f"• *Total Fingerprinted Archive:* `{fingerprinted_uploads}/{total_uploads}` pins\n\n"
            "✨ *Status:* 4-Layer Guard (Source URL + Filename + SHA-256 + Visual dHash ≤ 4) is active across Scraper, Queue, and Scheduler."
        )
        await status_msg.edit_text(report, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[TG BOT] cmd_dedup_scan error: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ *Deduplication Scan Failed:* `{e}`", parse_mode="Markdown")


# --- A/B Title Split Testing Command ---
async def cmd_ab(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update):
        return
    try:
        from ab_tester import format_ab_leaderboard
        await update.message.reply_text(format_ab_leaderboard(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[TG BOT] cmd_ab error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ A/B error: {e}")


# --- Revenue Tracking Dashboard Command ---
async def cmd_revenue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update):
        return
    try:
        from revenue_tracker import build_revenue_dashboard
        days = int(context.args[0]) if context.args and context.args[0].isdigit() else 30
        await update.message.reply_text(build_revenue_dashboard(days=days), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[TG BOT] cmd_revenue error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Revenue error: {e}")


# --- Weekly Performance Report Command ---
async def cmd_weekly(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update):
        return
    try:
        from weekly_report import send_weekly_report, build_weekly_report
        # If admin explicitly called /weekly, send the report directly in this chat or via notify_admin
        report = build_weekly_report()
        await update.message.reply_text(report, parse_mode="Markdown")
        send_weekly_report(force=True)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_weekly error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Weekly report error: {e}")


# --- Ghost Pin Reviver Command ---
async def cmd_revive(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update):
        return
    dry = "--dry" in (context.args or [])
    try:
        from ghost_pin_reviver import run_ghost_reviver, format_revive_report
        revived = run_ghost_reviver(dry_run=dry)
        await update.message.reply_text(format_revive_report(revived), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"[TG BOT] cmd_revive error: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Reviver error: {e}")


# -- Start bot in background thread -------------------------------------------
def start_bot(token: str, admin_chat_id: str = None, channels: list = None,
              dry_run: bool = True, post_delay: int = 10, max_per_day: int = 5):
    """Launch the Telegram control bot in a daemon background thread."""
    if not _TG_AVAILABLE:
        logger.warning("[TG BOT] python-telegram-bot not installed, skipping.")
        return
    if not token:
        logger.warning("[TG BOT] No TELEGRAM_BOT_TOKEN set, skipping.")
        return

    _state["admin_chat_id"] = admin_chat_id
    _state["channels"]      = channels or []
    _state["dry_run"]       = dry_run
    _state["post_delay"]    = post_delay
    _state["max_per_day"]   = max_per_day

    def _run():
        global _app_ref, _loop_ref
        import time, requests as _req

        # ── Self-heal: Grab and HOLD the Telegram polling session ────────────
        # Root problem on Render: when a new instance deploys, the old instance
        # is still alive. One getUpdates(0) kick evicts the old poller, but the
        # old instance immediately retries — racing our own start_polling() call.
        #
        # Solution: "hold" the session by calling getUpdates(0) in a rapid loop
        # for ~20 seconds. Every successful call re-confirms we own the session
        # and blocks the old instance from re-grabbing it. Only after 20s of
        # uncontested ownership do we hand off to start_polling().
        #
        # Step 1: deleteWebhook — clears any stuck webhook (harmless if none).
        # Step 2: Hold loop — getUpdates(0) every 2s for up to 20s.
        # Step 3: If we get 5 consecutive successful responses, we own it.
        BASE = f"https://api.telegram.org/bot{token}"
        try:
            _req.get(f"{BASE}/deleteWebhook", params={"drop_pending_updates": "true"}, timeout=8)
            logger.info("[TG BOT] deleteWebhook called (clears any stuck webhook state).")
        except Exception as e:
            logger.warning(f"[TG BOT] deleteWebhook failed (non-critical): {e}")

        consecutive_ok = 0
        hold_target = 5  # Need 5 consecutive successful getUpdates before handing to start_polling
        for hold_attempt in range(15):  # Max 15 attempts × 2s = 30s hold window
            try:
                r = _req.get(
                    f"{BASE}/getUpdates",
                    params={"timeout": 0, "limit": 1, "offset": -1},
                    timeout=10
                )
                data = r.json()
                if data.get("ok"):
                    consecutive_ok += 1
                    logger.info(
                        f"[TG BOT] Session hold {consecutive_ok}/{hold_target} — "
                        f"we exclusively own getUpdates."
                    )
                    if consecutive_ok >= hold_target:
                        logger.info("[TG BOT] Session fully secured. Handing off to start_polling().")
                        break
                    time.sleep(2)
                elif r.status_code == 429:
                    wait = data.get("parameters", {}).get("retry_after", 10)
                    wait = min(wait, 15)
                    logger.warning(f"[TG BOT] Rate-limited during hold. Waiting {wait}s...")
                    consecutive_ok = 0
                    time.sleep(wait)
                else:
                    # Conflict from another instance? Reset and try again
                    consecutive_ok = 0
                    logger.warning(f"[TG BOT] Hold attempt {hold_attempt+1} got: {data}. Retrying...")
                    time.sleep(2)
            except Exception as e:
                consecutive_ok = 0
                logger.warning(f"[TG BOT] Hold attempt {hold_attempt+1} error: {e}")
                time.sleep(2)
        # ─────────────────────────────────────────────────────────────────────


        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_ref = loop

        app = Application.builder().token(token).build()

        # Register all command handlers
        handlers = [
            ("start",         cmd_start),
            ("ping",          cmd_ping),
            ("help",          cmd_help),
            ("status",        cmd_status),
            ("stats",         cmd_stats),
            ("organic",       cmd_organic),
            ("trustratio",    cmd_organic),
            ("rebalance",     cmd_rebalance),
            ("rebalance_queue", cmd_rebalance),
            ("digest",        cmd_digest),
            ("performance",   cmd_digest),
            ("dailyreport",   cmd_dailyreport),
            ("summary",       cmd_summary),
            ("daily",         cmd_summary),
            ("crosspost",     cmd_crosspost),
            ("platforms",     cmd_crosspost),
            ("preview",       cmd_preview),
            ("logs",          cmd_logs),
            ("channels",      cmd_channels),
            ("addchannel",    cmd_addchannel),
            ("removechannel", cmd_removechannel),
            ("setdelay",      cmd_setdelay),
            ("setmax",        cmd_setmax),
            ("dryrun",        cmd_dryrun),
            ("golive",        cmd_golive),
            ("autopilot",     cmd_autopilot),
            ("testpost",      cmd_testpost),
            ("pause",         cmd_pause),
            ("resume",        cmd_resume),
            ("resetbreaker",  cmd_resetbreaker),
            ("unbreak",       cmd_resetbreaker),
            ("queue",         cmd_queue),
            ("schedule",      cmd_schedule),
            ("clearqueue",    cmd_clearqueue),
            ("fixqueue",      cmd_fixqueue),
            ("deadqueue",     cmd_deadqueue),
            ("cleardead",     cmd_cleardead),
            ("scrape",        cmd_scrape),
            ("post_now",      cmd_postnow),
            ("clicks",        cmd_clicks),
            ("earnings",      cmd_clicks),
            ("analytics",     cmd_analytics),
            ("stockstats",    cmd_stockstats),
            ("uptime",        cmd_uptime),
            ("doctor",        cmd_doctor),
            ("healthcheck",   cmd_doctor),
            ("repairlinks",   cmd_repairlinks),
            ("checklinks",    cmd_repairlinks),
            ("healnow",       cmd_healnow),
            ("linkstats",     cmd_linkstats),
            ("arena",         cmd_arena),
            ("arena_test",    cmd_arena_test),
            ("bluesky",       cmd_bluesky),
            ("bluesky_test",  cmd_bluesky_test),
            ("raindrop",      cmd_raindrop),
            ("raindrop_test", cmd_raindrop_test),
            ("mastodon",      cmd_mastodon),
            ("mastodontest",  cmd_mastodon_test),
            ("mastodon_test", cmd_mastodon_test),
            ("pixelfed",      cmd_pixelfed),
            ("pixelfed_test", cmd_pixelfed_test),
            ("pixelfedtest",  cmd_pixelfed_test),
            ("freeimage",     cmd_freeimage),
            ("freeimage_test",cmd_freeimage_test),
            ("freeimagetest", cmd_freeimage_test),
            ("imghippo",        cmd_imghippo),
            ("imghippo_test",   cmd_imghippo_test),
            ("imghippotest",    cmd_imghippo_test),
            ("wallhaven",          cmd_wallhaven),
            ("wallhaven_search",   cmd_wallhaven_search),
            ("wallhavensearch",    cmd_wallhaven_search),
            ("set_wallhaven_key",  cmd_set_wallhaven_key),
            ("setwallhavenkey",    cmd_set_wallhaven_key),
            ("verify_public",      cmd_verify_public),
            ("ghostcheck",         cmd_verify_public),
            ("bridge_url",         cmd_bridge_url),
            ("bridgeurl",          cmd_bridge_url),
            ("pinanalytics",       cmd_pinanalytics),
            ("pinstats",           cmd_pinanalytics),
            ("topseo",             cmd_topseo),
            ("seostats",           cmd_topseo),
            ("seo",                cmd_seo),
            ("seomatrix",          cmd_seo),
            ("backlinks",          cmd_backlinks),
            ("radar",              cmd_radar),
            ("viral",              cmd_radar),
            ("spinoff",            cmd_spinoff),
            ("refreshanalytics",   cmd_refreshanalytics),
            ("pullanalytics",      cmd_refreshanalytics),
            ("dedup_scan",         cmd_dedup_scan),
            ("dedup",              cmd_dedup_scan),
            ("cleandupes",         cmd_dedup_scan),
            ("ab",                 cmd_ab),
            ("abtest",             cmd_ab),
            ("revenue",            cmd_revenue),
            ("weekly",             cmd_weekly),
            ("weeklyreport",       cmd_weekly),
            ("revive",             cmd_revive),
            ("reviver",            cmd_revive),
            ("deviantart",         cmd_deviantart),
            ("imgbb",              cmd_imgbb),
            ("imagekit",           cmd_imagekit),
            ("imgpile",            cmd_imgpile),
        ]

        for cmd, handler in handlers:
            app.add_handler(CommandHandler(cmd, handler))

        # Register inline button callback handler
        app.add_handler(CallbackQueryHandler(handle_approval_callback))

        # Register admin photo/document upload handler (Forward-to-Post feature)
        if _TG_AVAILABLE:
            app.add_handler(
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE,
                    handle_admin_photo_upload
                )
            )

        # ── Register the / menu that shows in Telegram UI ──────────────────
        from telegram import BotCommand

        async def _set_menu(application):
            await application.bot.set_my_commands([
                BotCommand("ping",          "Check if bot is alive"),
                BotCommand("status",        "Bot status, mode and uptime"),
                BotCommand("organic",       "80/20 Organic Trust Ratio & account health"),
                BotCommand("summary",       "Today's multi-platform report (Auto: 10 PM)"),
                BotCommand("digest",        "Daily performance & health digest (Auto: 9 PM)"),
                BotCommand("crosspost",     "Multi-platform status across all 10 platforms"),
                BotCommand("doctor",        "System health report (Auto: 3 days)"),
                BotCommand("repairlinks",   "Audit & repair dead links (Auto: 1st of month)"),
                BotCommand("healnow",       "Heal out-of-stock links now (Auto: 6 hrs)"),
                BotCommand("linkstats",     "Autonomous link healing & redirect stats"),
                BotCommand("dedup",         "4-Layer duplicate audit & purge"),
                BotCommand("dedup_scan",    "Scan queue & purge visual duplicate pins"),
                BotCommand("stats",         "Pins count and queue size"),
                BotCommand("clicks",        "Affiliate clicks & estimated revenue"),
                BotCommand("analytics",     "7-day pins & revenue report"),
                BotCommand("stockstats",    "Stock photo upload counts per platform"),
                BotCommand("uptime",        "Platform health & circuit breaker status"),
                BotCommand("dailyreport",   "Today's detailed pin report"),
                BotCommand("preview",       "Last generated pin with image"),
                BotCommand("logs",          "Show recent log output"),
                BotCommand("queue",         "Show queue with per-date breakdown"),
                BotCommand("channels",      "List monitored channels"),
                BotCommand("addchannel",    "Add a source channel"),
                BotCommand("removechannel", "Remove a source channel"),
                BotCommand("setdelay",      "Set posting delay in minutes"),
                BotCommand("setmax",        "Set max pins per day"),
                BotCommand("dryrun",        "Toggle dry-run on/off"),
                BotCommand("golive",        "Enable real Pinterest posting"),
                BotCommand("autopilot",     "Toggle auto-post vs approval mode"),
                BotCommand("testpost",      "Send a test pin via Make.com webhook"),
                BotCommand("pause",         "Pause posting"),
                BotCommand("resume",        "Resume posting"),
                BotCommand("resetbreaker",  "Clear platform circuit breaker cooldown"),
                BotCommand("post_now",      "Force-post next queued pin NOW"),
                BotCommand("schedule",      "Today's posting schedule (Auto: 8 AM)"),
                BotCommand("scrape",        "Scrape channels for new pins NOW"),
                BotCommand("clearqueue",    "Clear all pending pins from queue"),
                BotCommand("fixqueue",      "Re-upload stale CDN URLs to permanent host"),
                BotCommand("deadqueue",     "View pins that failed all retries"),
                BotCommand("cleardead",     "Clear the dead-letter queue"),
                BotCommand("arena",         "Are.na channel stats & block count"),
                BotCommand("arena_test",    "Post test block to Are.na channel"),
                BotCommand("bluesky",       "Bluesky profile stats & follower count"),
                BotCommand("bluesky_test",  "Post test photo to Bluesky feed"),
                BotCommand("raindrop",      "Raindrop collection stats & link"),
                BotCommand("raindrop_test", "Post test bookmark to Raindrop collection"),
                BotCommand("mastodon",      "Mastodon profile stats & link"),
                BotCommand("mastodon_test", "Post test anime to Mastodon feed"),
                BotCommand("pixelfed",      "Pixelfed profile stats & link"),
                BotCommand("pixelfed_test", "Post test photo to Pixelfed"),
                BotCommand("freeimage",     "Freeimage.host profile stats & link"),
                BotCommand("freeimage_test","Post test photo to Freeimage.host"),
                BotCommand("imghippo",      "Imghippo profile stats & link"),
                BotCommand("imghippo_test", "Post test photo to Imghippo"),
                BotCommand("wallhaven",     "Wallhaven profile & collection stats"),
                BotCommand("wallhaven_search", "Search 4K anime wallpapers on Wallhaven"),
                BotCommand("deviantart",    "DeviantArt profile & stash status"),
                BotCommand("imgbb",         "ImgBB gallery & API status"),
                BotCommand("imagekit",      "ImageKit CDN gallery & status"),
                BotCommand("imgpile",       "ImgPile gallery & API status"),
                BotCommand("verify_public",      "🔍 Check if a Pinterest pin is visible to the public"),

                BotCommand("bridge_url",          "🌉 Get bridge landing page URL for a pin code"),
                BotCommand("pinanalytics",        "📈 Pinterest impressions, saves & clicks report"),
                BotCommand("seo",                 "🔍 Preview Multi-Intent SEO tags & title"),
                BotCommand("backlinks",           "🌐 Backlink SEO Waterfall status & counts"),
                BotCommand("radar",               "🚨 Viral Pin Surge Radar & trending pins"),
                BotCommand("spinoff",             "🔥 Create Express Spinoff pins for viral artwork"),
                BotCommand("topseo",              "🔍 Top anime keywords driving search traffic"),
                BotCommand("refreshanalytics",    "🔄 Pull fresh analytics from Pinterest API"),
                BotCommand("ab",                  "🔬 A/B title test results & leaderboard"),
                BotCommand("revenue",             "💰 Estimated affiliate revenue dashboard"),
                BotCommand("weekly",              "📆 Sunday performance report digest"),
                BotCommand("revive",              "👻 Re-queue ghost pins with new SEO titles"),
                BotCommand("help",                "Show all commands"),
            ])
            logger.info("[TG BOT] Command menu registered in Telegram.")

        app.post_init = _set_menu
        # ───────────────────────────────────────────────────────────────────

        _app_ref = app
        logger.info("[TG BOT] @AnimanoizingBot is online! Send /start in Telegram.")

        # ── Thread-safe polling (avoids signal-handler crash on Linux) ──────
        # run_polling() crashes in a background thread on Linux/Render because
        # it tries to install Unix signal handlers (set_wakeup_fd) which only
        # work in the main thread. We use the low-level async API instead.
        async def _async_polling():
            # Retry loop: recovers from Conflict errors AND any other transient
            # error (network drop, SSL, timeout, etc.) so the bot never goes
            # permanently silent while Render keeps the process alive.
            # Conflict: exponential 5→35s + session kick.
            # Other errors: exponential backoff 10→60s, unlimited retries.
            #
            # nonlocal required: we reassign 'app' in the except block.
            nonlocal app
            poll_attempt = 0
            while True:
                try:
                    async with app:
                        # Register the command menu
                        await _set_menu(app)
                        # Start polling — drop stale updates, no signal handlers
                        await app.updater.start_polling(drop_pending_updates=True)
                        await app.start()
                        logger.info("[TG BOT] Polling started successfully (thread-safe mode).")
                        poll_attempt = 0  # Reset backoff counter on successful start
                        # Keep running until the loop is stopped
                        while True:
                            await asyncio.sleep(60)
                except asyncio.CancelledError:
                    logger.info("[TG BOT] Polling cancelled — shutting down.")
                    break
                except Exception as e:
                    poll_attempt += 1
                    err_str = str(e)

                    if "Conflict" in err_str:
                        # Another instance is still polling — kick it out first
                        wait_s = min(5 * (2 ** min(poll_attempt - 1, 6)), 35)
                        logger.warning(
                            f"[TG BOT] Conflict error — old instance still holds the session. "
                            f"Re-kicking via getUpdates then retrying in {wait_s}s "
                            f"(attempt {poll_attempt})"
                        )
                        try:
                            resp = await asyncio.to_thread(
                                _req.get,
                                f"https://api.telegram.org/bot{token}/getUpdates",
                                params={"timeout": 0, "limit": 1},
                                timeout=10,
                            )
                            kick_data = resp.json()
                            if kick_data.get("ok"):
                                logger.info("[TG BOT] Async kick succeeded — old poller evicted.")
                            else:
                                logger.warning(f"[TG BOT] Async kick returned: {kick_data}")
                        except Exception as kick_e:
                            logger.warning(f"[TG BOT] Async kick error: {kick_e}")
                        await asyncio.sleep(wait_s)
                    else:
                        # Network drop, SSL error, timeout, or any other transient error.
                        # PREVIOUSLY this hit 'break' and killed the bot permanently.
                        # Now we retry with exponential backoff (10s → 60s) indefinitely.
                        wait_s = min(10 * (2 ** min(poll_attempt - 1, 5)), 60)
                        logger.warning(
                            f"[TG BOT] Polling error (attempt {poll_attempt}) — "
                            f"auto-recovering in {wait_s}s: {e}"
                        )
                        await asyncio.sleep(wait_s)

                    # Re-build app with fresh connection pool for next attempt
                    app = Application.builder().token(token).build()
                    global _app_ref
                    _app_ref = app
                    for cmd, handler in handlers:
                        app.add_handler(CommandHandler(cmd, handler))
                    app.add_handler(CallbackQueryHandler(handle_approval_callback))
                    if _TG_AVAILABLE:
                        app.add_handler(
                            MessageHandler(
                                filters.PHOTO | filters.Document.IMAGE,
                                handle_admin_photo_upload
                            )
                        )

        loop.run_until_complete(_async_polling())
        # ────────────────────────────────────────────────────────────────────

    thread = threading.Thread(target=_run, daemon=True, name="TelegramBot")
    thread.start()
    logger.info("[TG BOT] Bot thread launched.")

    # Start daily summary scheduler (sends report at 10 PM IST every day)
    # BUG-FIX: Small delay so _loop_ref is set by _run() before the summary
    # thread's first wake cycle. Without this, the first minute-window check
    # runs with _loop_ref=None and silently skips the first scheduled send.
    def _deferred_summary_start():
        time.sleep(20)  # Wait for _run() to assign _loop_ref
        _start_daily_summary_thread(token, admin_chat_id or "")
    threading.Thread(target=_deferred_summary_start, daemon=True, name="SummaryStartDefer").start()

