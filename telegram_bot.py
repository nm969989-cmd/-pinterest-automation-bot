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
    "max_per_day":   15,
}

# Pending approval queue: maps callback_data key -> upload args dict
_pending_approvals: dict = {}

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
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not admin_id:
        return True
    return str(update.effective_chat.id) == str(admin_id)


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
            InlineKeyboardButton("🩺 Doctor Check", callback_data="btn_doctor"),
        ],
    ])


def get_post_confirmation_keyboard(amazon_url: str = "", pinterest_url: str = "") -> "InlineKeyboardMarkup | None":
    """Returns interactive direct link buttons for a posted pin."""
    if not _TG_AVAILABLE:
        return None
    import config
    p_url = pinterest_url or getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
    row1 = [InlineKeyboardButton("📌 View Pinterest Profile", url=p_url)]

    if amazon_url and amazon_url.startswith("http"):
        # Show different label depending on whether it's a direct product or search results
        if "/dp/" in amazon_url:
            amazon_label = "🎯 View Amazon Product"
        else:
            # Search link — honest label so user knows what to expect
            amazon_label = "🔍 Browse Amazon Products"
        row1.append(InlineKeyboardButton(amazon_label, url=amazon_url))

    return InlineKeyboardMarkup([
        row1,
        [
            InlineKeyboardButton("🚀 Post Next Now", callback_data="btn_postnow"),
            InlineKeyboardButton("📋 View Queue", callback_data="btn_queue"),
        ],
    ])



# -- Command handlers ---------------------------------------------------------

async def cmd_start(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    chat_id = update.effective_chat.id
    _state["admin_chat_id"] = chat_id
    await update.message.reply_text(
        f"Welcome to Animanoizing Bot!\n\n"
        f"Your Chat ID: {chat_id}\n\n"
        f"Add this to your .env and Render:\n"
        f"TELEGRAM_ADMIN_CHAT_ID={chat_id}\n\n"
        f"Use /help to see all commands."
    )
    logger.info(f"[TG BOT] Admin registered: chat_id={chat_id}")


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
        "/doctor         - System health report (Auto: runs every 3 days)\n"
        "/repairlinks    - Audit & repair dead Amazon links (Auto: 1st of month)\n"
        "/stats          - Pins count and queue\n"
        "/summary        - Today's multi-platform report (Auto: 9 PM IST)\n"
        "/dailyreport    - Detailed pin & cross-post report\n"
        "/clicks         - Affiliate clicks & estimated earnings\n"
        "/analytics      - 7-day pins & revenue report\n"
        "/preview        - Last pin with image\n"
        "/logs           - Recent log output\n"
        "/queue          - Pending queue breakdown\n"
        "/schedule       - Today's posting schedule (Auto: 8 AM IST)\n"
        "/channels       - Monitored channels\n"
        "/ping           - Check bot is alive\n\n"
        "--- CROSS-POSTING ---\n"
        "/crosspost      - Multi-platform status (Pinterest, Are.na, Tumblr)\n"
        "/arena          - Are.na channel stats & block count\n"
        "/arena_test     - Post test block to Are.na channel\n"
        "/tumblr         - Tumblr blog stats & follower count\n"
        "/tumblr_test    - Post test photo to Tumblr blog\n\n"
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
        "/fixqueue       - Re-upload stale CDN URLs to permanent host\n\n"
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

    pin = _state.get("last_pin")
    last_time = pin["time"] if pin else "None yet"
    await msg.reply_text(
        f"Pin Statistics\n"
        f"{'='*25}\n"
        f"Today    : {today_db} pins\n"
        f"Total    : {total_db} pins\n"
        f"Queue    : {_state['queue_size']} pending\n"
        f"Last pin : {last_time}\n"
        f"Mode     : {'DRY RUN' if _state['dry_run'] else 'LIVE'}\n\n"
        f"Top Anime:\n{top_str or '  (none yet)'}",
        reply_markup=get_queue_keyboard()
    )


async def cmd_dailyreport(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Send today's detailed multi-platform report on demand."""
    if not _is_admin(update): return
    await _send_daily_report(update.effective_chat.id)


async def cmd_summary(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Send today's multi-platform summary on demand (alias for /dailyreport)."""
    if not _is_admin(update): return
    await _send_daily_report(update.effective_chat.id)


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

    est_rev = f"₹{cstats['est_revenue_min']} - ₹{cstats['est_revenue_max']}"
    est_orders = f"{cstats['est_orders_min']} - {cstats['est_orders_max']} items"

    msg = (
        f"📊 Affiliate Clicks & Earnings Report\n"
        f"{'═' * 30}\n"
        f"🖱️ Clicks Today    : {cstats['today']}\n"
        f"📈 Clicks This Week : {cstats['week']}\n"
        f"🌐 All-Time Clicks  : {cstats['total']}\n\n"
        f"💰 Estimated Performance (7 Days):\n"
        f"  • Estimated Orders  : {est_orders}\n"
        f"  • Estimated Revenue : {est_rev}\n\n"
        f"🎌 Top Clicked Anime:\n{top_anime_str}\n\n"
        f"🔥 Top Clicked Pins:\n{top_pins_str}\n\n"
        f"💡 Note: Actual confirmed purchases and payout balance are finalized on affiliate-program.amazon.in"
    )
    await update.message.reply_text(msg)
    logger.info("[TG BOT] /clicks report sent.")


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
            get_arena_stats, get_tumblr_stats, get_click_stats
        )
        from config import (
            ARENA_ENABLED, ARENA_CHANNEL_SLUG,
            TUMBLR_ENABLED, TUMBLR_BLOG_NAME
        )
        # Use IST date for consistent timezone-aware reporting
        now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
        today_ist_str = now_ist.strftime("%Y-%m-%d")
        today_display = now_ist.strftime("%d %b %Y")

        pins   = get_today_uploads(today_ist_str)
        stats  = get_all_time_stats()
        count  = len(pins)

        # Cross-platform stats
        arena_st  = get_arena_stats(today_ist_str)
        tumblr_st = get_tumblr_stats(today_ist_str)

        # Click & earnings stats
        try:
            clicks = get_click_stats(days=1)
            clicks_today = clicks.get("total_clicks", 0)
            earnings_today = clicks.get("estimated_revenue_inr", 0.0)
        except Exception:
            clicks_today = 0
            earnings_today = 0.0

        header = (
            f"📊 Daily Multi-Platform Report — {today_display}\n"
            f"{'═' * 38}\n\n"
        )

        arena_badge  = "🟢 ON" if ARENA_ENABLED else "⚪ OFF"
        tumblr_badge = "🟢 ON" if TUMBLR_ENABLED else "⚪ OFF"

        summary_section = (
            f"🌐 Platform Breakdown:\n"
            f"  📌 Pinterest : {count} posted today  |  {stats['total']} all-time\n"
            f"  🔮 Are.na    : {arena_st['today']} posted today  |  {arena_st['total']} all-time  ({arena_badge})\n"
            f"  🎨 Tumblr    : {tumblr_st['today']} posted today  |  {tumblr_st['total']} all-time  ({tumblr_badge})\n\n"
        )

        revenue_section = ""
        if clicks_today > 0 or earnings_today > 0:
            revenue_section = (
                f"💰 Affiliate Performance (Today):\n"
                f"  • Clicks: {clicks_today}  |  Est. Revenue: ₹{earnings_today:,.2f}\n\n"
            )

        if count == 0:
            pin_section = "📌 Pinterest Activity:\n  (No pins posted today yet)\n\n"
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

        crosspost_section = "🌐 Connected Channels:\n"
        if ARENA_ENABLED:
            crosspost_section += f"  • Are.na: are.na/manoj-muthelyrics/{ARENA_CHANNEL_SLUG}\n"
        if TUMBLR_ENABLED:
            crosspost_section += f"  • Tumblr: https://{TUMBLR_BLOG_NAME}.tumblr.com\n"
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
      • 9:00 AM IST (03:30 UTC): Daily summary report
      • 10:00 AM IST (04:30 UTC): Automated 3-Day Health Check
      • 10:30 AM IST (05:00 UTC): Monthly Self-Healing Link Audit
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
        while True:
            now_utc = datetime.datetime.utcnow()

            # 08:00 AM IST = 02:30 UTC -> Daily Morning Schedule
            if now_utc.hour == 2 and now_utc.minute == 30 and now_utc.date() != sent_schedule_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    sent_schedule_today = now_utc.date()
                    logger.info(f"[TG BOT] Sending 8:00 AM daily schedule to {chat_id}...")
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_morning_schedule(chat_id), _loop_ref
                    )
                else:
                    logger.warning("[TG BOT] 8 AM schedule skipped — admin chat ID not set yet.")

            # 09:00 PM IST = 15:30 UTC -> Daily Summary Report
            # Moved from 9 AM IST to 9 PM IST: at 9 AM the first pin hasn't posted yet
            # so the report always said "No pins today". At 9 PM all 5 pins are done.
            if now_utc.hour == 15 and now_utc.minute == 30 and now_utc.date() != sent_today:
                chat_id = _get_chat_id()
                if _app_ref and chat_id:
                    sent_today = now_utc.date()
                    logger.info(f"[TG BOT] Sending daily report to {chat_id} (9 PM IST)...")
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_report(chat_id), _loop_ref
                    )
                else:
                    logger.warning("[TG BOT] Daily report skipped — admin chat ID not set yet.")


            # 10:00 AM IST = 04:30 UTC -> Automated 3-Day Health Check
            if now_utc.hour == 4 and now_utc.minute == 30:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    try:
                        from doctor import check_and_run_scheduled_health_check
                        check_and_run_scheduled_health_check(_app_ref, _loop_ref, chat_id)
                    except Exception as e:
                        logger.error(f"[TG BOT] Scheduled health check error: {e}")

            # 10:30 AM IST = 05:00 UTC -> Automated Monthly Self-Healing Link Audit (1st of month)
            if now_utc.hour == 5 and now_utc.minute == 0:
                chat_id = _get_chat_id()
                if _app_ref and chat_id and _loop_ref:
                    try:
                        from link_healer import check_and_run_monthly_repair
                        check_and_run_monthly_repair(_app_ref, _loop_ref, chat_id)
                    except Exception as e:
                        logger.error(f"[TG BOT] Scheduled monthly link repair error: {e}")

            time.sleep(55)  # Check every ~1 min

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
    await update.message.reply_text(
        f"Max pins per day set to {n}.\n\n"
        f"Add MAX_POSTS_PER_DAY={n} to Render env to persist."
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
            with sqlite3.connect(DB_PATH) as conn:
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
        pin_type = "NEW" if pin["priority"] == 1 else "BACKLOG"
        stale_note = f"\n🗑 Skipped {stale_dropped} stale pin(s) with expired CDN URLs." if stale_dropped else ""

        success = upload_to_pinterest(
            image_path=image_path,
            title=pin["title"],
            description=pin["description"],
            link=pin["link"],
            anime_name=pin["anime_name"],
            board_id=pin.get("board_id", ""),
        )

        if success:
            _state["posts_today"] = _state.get("posts_today", 0) + 1
            _state["posts_total"] = _state.get("posts_total", 0) + 1
            remaining = get_queue_counts()["total"]
            from database import get_tracked_target_url
            target_url = get_tracked_target_url(pin["link"])
            confirm_text = (
                f"📌 {pin_type} pin posted!{stale_note}\n"
                f"{'─' * 26}\n"
                f"📝 {pin['title']}\n"
                f"🎌 {pin['anime_name']} • {remaining} left in queue"
            )

            confirm_markup = get_post_confirmation_keyboard(target_url or pin["link"])
            if image_path and os.path.exists(image_path):
                try:
                    with open(image_path, "rb") as img_file:
                        await msg.reply_photo(photo=img_file, caption=confirm_text[:1024], reply_markup=confirm_markup)
                except Exception:
                    await msg.reply_text(confirm_text, reply_markup=confirm_markup)
            else:
                await msg.reply_text(confirm_text, reply_markup=confirm_markup)
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
                "Test pin posted successfully!\n"
                "Check your Pinterest board — the pin should be live now."
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
                        f"Posted to Pinterest!\n\n"
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


# -- Notify admin helper ------------------------------------------------------
_app_ref  = None
_loop_ref = None


def notify_admin(message: str):
    """Send a plain text notification to the admin from any thread."""
    global _app_ref, _loop_ref
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return
    try:
        asyncio.run_coroutine_threadsafe(
            _app_ref.bot.send_message(chat_id=admin_id, text=message),
            _loop_ref
        )
    except Exception as e:
        logger.warning(f"[TG BOT] Could not notify admin: {e}")


def notify_link_clicked(anime_name: str, title: str, today_count: int):
    """
    Sends a real-time notification to Telegram whenever a Pinterest user clicks your link.
    Silenced by default as requested by user to avoid message spam.
    """
    import config
    if not config.CLICK_NOTIFICATION:
        return
    global _app_ref, _loop_ref
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return

    text = (
        f"🔔 Affiliate Link Clicked on Pinterest!\n"
        f"{'─' * 28}\n"
        f"🎌 Anime : {anime_name or 'Anime'}\n"
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
    Handler for when admin sends/forwards any photo or image directly to the bot.
    Processes the image, generates AI vision captions, finds Amazon merchandise,
    and uploads directly to Pinterest immediately with a full detailed report!
    """
    if not _is_admin(update): return
    msg = update.effective_message
    status_msg = await msg.reply_text("📥 Processing your image & generating AI caption...")
    try:
        import time as _time
        os.makedirs("downloads", exist_ok=True)
        ts = int(_time.time())
        save_path = os.path.join("downloads", f"manual_{ts}.jpg")

        if update.message.photo:
            file_id = update.message.photo[-1].file_id
        elif update.message.document:
            file_id = update.message.document.file_id
        else:
            return

        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(save_path)

        from image_processor import process_image
        from ai_caption import generate_pin_content
        from amazon_search import generate_amazon_link
        from hashtag_optimizer import optimize_hashtags, replace_hashtags_in_description
        from board_router import get_board_for_anime
        from pinterest_uploader import upload_to_pinterest
        from database import mark_file_uploaded, enqueue_pin, get_tracked_target_url
        import config

        processed_path = process_image(save_path, "manual_upload")
        caption_hint = update.message.caption or ""
        anime_name, title, desc_template = generate_pin_content(
            caption_hint, "manual_upload", image_path=processed_path,
            api_key=None, openrouter_key=config.OPENROUTER_API_KEY
        )
        from amazon_search import clean_character_name
        raw_char = title.split(" - ")[0].split()[0] if title else ""
        character_name = clean_character_name(raw_char)
        genre, board_id = get_board_for_anime(anime_name)
        amazon_link = generate_amazon_link(anime_name, character_name=character_name, title=title)
        description = replace_hashtags_in_description(
            desc_template.replace("##LINK_PLACEHOLDER##", amazon_link),
            optimize_hashtags(anime_name=anime_name, genre=genre, character_name=character_name)
        )

        target_url = get_tracked_target_url(amazon_link)
        amazon_line = f"\n🎯 Direct Amazon : {target_url}" if target_url != amazon_link else ""
        pinterest_profile = getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")


        await status_msg.edit_text("🚀 Uploading pin to Pinterest now...")

        # Direct upload to Pinterest
        uploaded_ok = upload_to_pinterest(
            image_path=processed_path,
            title=title,
            description=description,
            link=amazon_link,
            anime_name=anime_name,
            board_id=board_id,
        )

        if uploaded_ok:
            mark_file_uploaded(os.path.basename(processed_path), title, anime_name)
            _state["posts_today"] = _state.get("posts_today", 0) + 1
            _state["posts_total"] = _state.get("posts_total", 0) + 1
            status_header = "📌 Live on Pinterest! (Uploaded Successfully)"
            action_note = "Your pin is live on Pinterest right now."
        else:
            # Enqueue if upload failed or dry-run
            import datetime as _dt
            today_ist_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
            enqueue_pin(
                post_id=f"manual_{ts}", image_path=processed_path, title=title,
                description=description, link=amazon_link, anime_name=anime_name,
                image_url="", board_id=board_id, priority=1, scheduled_date=today_ist_str
            )
            status_header = "📥 Queued for Posting (#1 in line)"
            action_note = "Queued! Tap [ 🚀 Post Now ] below to publish immediately."

        board_label = f" • {genre.title()}" if genre else ""
        char_label = f" ({character_name})" if character_name and character_name.lower() not in anime_name.lower() else ""

        if uploaded_ok:
            confirm_text = (
                f"📌 Live on Pinterest!\n"
                f"{'─' * 26}\n"
                f"📝 {title}\n"
                f"🎌 {anime_name}{char_label}{board_label}"
            )
        else:
            confirm_text = (
                f"📥 Queued (#1 in line)\n"
                f"{'─' * 26}\n"
                f"📝 {title}\n"
                f"🎌 {anime_name}{char_label}{board_label}"
            )

        # Direct link buttons
        buttons = [
            [
                InlineKeyboardButton("📌 View on Pinterest", url=pinterest_profile),
                InlineKeyboardButton("🎯 View Amazon Merch", url=target_url or amazon_link),
            ]
        ]
        if not uploaded_ok:
            buttons.append([InlineKeyboardButton("🚀 Post to Pinterest NOW", callback_data="btn_postnow")])
        buttons.append([InlineKeyboardButton("📋 View Queue", callback_data="btn_queue")])
        markup = InlineKeyboardMarkup(buttons)

        # Delete intermediate status message and send rich confirmation with photo
        try:
            await status_msg.delete()
        except Exception:
            pass

        if processed_path and os.path.exists(processed_path):
            with open(processed_path, "rb") as img:
                await msg.reply_photo(photo=img, caption=confirm_text[:1024], reply_markup=markup)
        else:
            await msg.reply_text(confirm_text, reply_markup=markup)

    except Exception as e:
        logger.error(f"[TG BOT] Mobile upload error: {e}", exc_info=True)
        await msg.reply_text(f"❌ Error processing upload: {e}")



def notify_admin_pin_posted(title: str, anime_name: str, link: str,
                             image_path: str, pin_type: str,
                             posted_today: int, max_today: int,
                             time_ist: str):
    """
    Send a rich Telegram notification after every successful Pinterest post.
    Sends the actual image + details. FREE — no limits at 3 messages/day.
    """
    global _app_ref, _loop_ref
    admin_id = _state.get("admin_chat_id") or os.getenv("TELEGRAM_ADMIN_CHAT_ID")
    if not _app_ref or not admin_id or not _loop_ref:
        return

    # Use config MAX_POSTS_PER_DAY so bar is always correct size (3, not 15)
    from config import MAX_POSTS_PER_DAY as _max
    actual_max = _max  # always read from config, ignore stale state

    type_emoji  = "🆕 NEW" if pin_type == "NEW" else "📦 BACKLOG"
    # Build correctly-sized progress bar
    filled  = min(posted_today, actual_max)
    bar     = "".join(["✅" if i <= filled else "⬜" for i in range(1, actual_max + 1)])

    from database import get_tracked_target_url
    target_url  = get_tracked_target_url(link)
    amazon_line = f"\n🎯 Amazon     : {target_url}" if target_url != link else ""

    caption = (
        f"📌 Pin Posted! ({bar} {posted_today}/{actual_max})\n"
        f"{'─' * 26}\n"
        f"📝 {title}\n"
        f"🎌 {anime_name} • {time_ist} IST"
    )



    reply_markup = get_post_confirmation_keyboard(target_url or link)

    async def _send():
        try:
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as img:
                    await _app_ref.bot.send_photo(
                        chat_id=admin_id,
                        photo=img,
                        caption=caption[:1024],
                        reply_markup=reply_markup,
                    )
            else:
                await _app_ref.bot.send_message(
                    chat_id=admin_id,
                    text=caption,
                    reply_markup=reply_markup,
                )
        except Exception as e:
            logger.warning(f"[TG BOT] Pin notification failed: {e}")

    try:
        asyncio.run_coroutine_threadsafe(_send(), _loop_ref)
    except Exception as e:
        logger.warning(f"[TG BOT] Could not schedule pin notification: {e}")



def send_pin_approval_request(image_path: str, title: str, description: str, link: str):
    """
    Sends a photo message to the admin with [Post to Pinterest] and [Discard] buttons.
    Called from the main pipeline when AUTO_POST_MODE=false.
    """
    global _app_ref, _loop_ref
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


async def cmd_tumblr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show Tumblr blog stats: post count, followers, blog URL."""
    if not _is_admin(update):
        return
    try:
        from tumblr_uploader import get_tumblr_blog_info, verify_tumblr_token
        from config import TUMBLR_ENABLED, TUMBLR_BLOG_NAME
        enabled_str = "ENABLED" if TUMBLR_ENABLED else "DISABLED (set TUMBLR_ENABLED=true)"
        token_ok    = verify_tumblr_token()
        info        = get_tumblr_blog_info() if token_ok else None
        if info:
            await update.message.reply_text(
                f"Tumblr Cross-Post: {enabled_str}\n"
                f"Blog    : {info['title']} ({info['name']})\n"
                f"Posts   : {info['posts']:,}\n"
                f"Followers: {info['followers']:,}\n"
                f"URL     : {info['url']}\n"
                f"Token   : OK"
            )
        elif token_ok:
            await update.message.reply_text(
                f"Tumblr: {enabled_str}\n"
                f"Blog: {TUMBLR_BLOG_NAME}\n"
                f"Token: OK (could not fetch blog info)\n"
                f"Check TUMBLR_BLOG_NAME in .env"
            )
        else:
            await update.message.reply_text(
                f"Tumblr: {enabled_str}\n"
                f"Token : INVALID\n"
                f"Set TUMBLR_ACCESS_TOKEN + TUMBLR_ACCESS_TOKEN_SECRET in .env\n"
                f"Run: python get_tumblr_token.py"
            )
    except Exception as e:
        await update.message.reply_text(f"Tumblr error: {e}")


async def cmd_tumblr_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a test photo to Tumblr to verify the token and blog work."""
    if not _is_admin(update):
        return
    await update.message.reply_text("Posting test photo to Tumblr...")
    try:
        from tumblr_uploader import post_to_tumblr
        from config import TUMBLR_BLOG_NAME
        pid = post_to_tumblr(
            image_url = "https://res.cloudinary.com/demo/image/upload/sample.jpg",
            title     = "Test Post - Pinterest Bot",
            caption   = "Automated test from your Pinterest Bot Tumblr integration. Working!",
            link      = "https://amazon.in",
            tags      = ["test", "bot", "pinterest", "anime"],
        )
        if pid:
            await update.message.reply_text(
                f"Test photo posted to Tumblr! Post ID: {pid}\n"
                f"Check: https://{TUMBLR_BLOG_NAME}.tumblr.com"
            )
        else:
            await update.message.reply_text(
                "Tumblr test FAILED. Check:\n"
                "1. TUMBLR_ACCESS_TOKEN is correct in .env\n"
                "2. TUMBLR_ACCESS_TOKEN_SECRET is set\n"
                "3. TUMBLR_ENABLED=true in .env\n"
                "Run /logs for details."
            )
    except Exception as e:
        await update.message.reply_text(f"Tumblr test error: {e}")


async def cmd_crosspost(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Show real-time multi-platform status (Pinterest, Are.na, Tumblr) with quick links."""
    if not _is_admin(update):
        return
    try:
        from config import (
            ARENA_ENABLED, ARENA_CHANNEL_SLUG,
            TUMBLR_ENABLED, TUMBLR_BLOG_NAME,
            DRY_RUN
        )
        from database import get_arena_stats, get_tumblr_stats, get_today_uploads, get_all_time_stats
        from arena_uploader import verify_arena_token, get_arena_channel_info
        from tumblr_uploader import verify_tumblr_token, get_tumblr_blog_info

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

        # Tumblr status
        tumblr_status = "DISABLED (TUMBLR_ENABLED=false)"
        tumblr_posts_str = ""
        if TUMBLR_ENABLED:
            tmblr_token_ok = verify_tumblr_token()
            if tmblr_token_ok:
                tb = get_tumblr_blog_info()
                posts_cnt = tb.get("posts", 0) if tb else "?"
                tumblr_status = "🟢 ACTIVE"
                tumblr_posts_str = f" ({posts_cnt} blog posts)"
            else:
                tumblr_status = "🔴 AUTH ERROR (Check token)"
        tumblr_st = get_tumblr_stats(today_str)

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
            f"🎨 Tumblr:\n"
            f"  • Status: {tumblr_status}{tumblr_posts_str}\n"
            f"  • Blog: {TUMBLR_BLOG_NAME}\n"
            f"  • Posts: {tumblr_st['today']} today  |  {tumblr_st['total']} all-time\n"
            f"  • Link: https://{TUMBLR_BLOG_NAME}.tumblr.com\n\n"
            f"🚀 Quick Commands:\n"
            f"  /summary — Today's multi-platform report\n"
            f"  /arena_test — Test Are.na block upload\n"
            f"  /tumblr_test — Test Tumblr photo upload\n"
            f"  /testpost — Test Pinterest webhook"
        )
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(f"[TG BOT] cmd_crosspost error: {e}", exc_info=True)
        await update.message.reply_text(f"Cross-post status error: {e}")


# -- Start bot in background thread -------------------------------------------
def start_bot(token: str, admin_chat_id: str = None, channels: list = None,
              dry_run: bool = True, post_delay: int = 10, max_per_day: int = 15):
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
            ("queue",         cmd_queue),
            ("schedule",      cmd_schedule),
            ("clearqueue",    cmd_clearqueue),
            ("fixqueue",      cmd_fixqueue),
            ("scrape",        cmd_scrape),
            ("post_now",      cmd_postnow),
            ("clicks",        cmd_clicks),
            ("earnings",      cmd_clicks),
            ("analytics",     cmd_analytics),
            ("doctor",        cmd_doctor),
            ("healthcheck",   cmd_doctor),
            ("repairlinks",   cmd_repairlinks),
            ("checklinks",    cmd_repairlinks),
            ("arena",         cmd_arena),
            ("arena_test",    cmd_arena_test),
            ("tumblr",        cmd_tumblr),
            ("tumblr_test",   cmd_tumblr_test),
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
                BotCommand("summary",       "Today's multi-platform report (Auto: 9 PM)"),
                BotCommand("crosspost",     "Multi-platform status (Pinterest, Are.na, Tumblr)"),
                BotCommand("doctor",        "System health report (Auto: 3 days)"),
                BotCommand("repairlinks",   "Audit & repair dead links (Auto: 1st of month)"),
                BotCommand("stats",         "Pins count and queue size"),
                BotCommand("clicks",        "Affiliate clicks & estimated revenue"),
                BotCommand("analytics",     "7-day pins & revenue report"),
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
                BotCommand("post_now",      "Force-post next queued pin NOW"),
                BotCommand("schedule",      "Today's posting schedule (Auto: 8 AM)"),
                BotCommand("scrape",        "Scrape channels for new pins NOW"),
                BotCommand("clearqueue",    "Clear all pending pins from queue"),
                BotCommand("fixqueue",      "Re-upload stale CDN URLs to permanent host"),
                BotCommand("arena",         "Are.na channel stats & block count"),
                BotCommand("arena_test",    "Post test block to Are.na channel"),
                BotCommand("tumblr",        "Tumblr blog stats & follower count"),
                BotCommand("tumblr_test",   "Post test photo to Tumblr blog"),
                BotCommand("help",          "Show all commands"),
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
            # Retry loop: if a Conflict error still occurs despite the pre-start
            # getUpdates kick above, we wait and re-kick via HTTP before retrying.
            # Exponential backoff: 5s, 10s, 20s, 35s, 35s, 35s, 35s, 35s.
            #
            # nonlocal required: we reassign 'app' in the except block.
            # Without nonlocal, Python treats 'app' as local throughout the
            # function and raises UnboundLocalError on the first 'async with app:'.
            # NOTE: _app_ref is a module global, not local to _run(), so we
            # cannot use nonlocal for it — we use 'global _app_ref' inline below.
            nonlocal app
            import aiohttp
            for poll_attempt in range(8):
                try:
                    async with app:
                        # Register the command menu
                        await _set_menu(app)
                        # Start polling — drop stale updates, no signal handlers
                        await app.updater.start_polling(drop_pending_updates=True)
                        await app.start()
                        logger.info("[TG BOT] Polling started successfully (thread-safe mode).")
                        # Keep running until the loop is stopped
                        while True:
                            await asyncio.sleep(60)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    err_str = str(e)
                    if "Conflict" in err_str and poll_attempt < 7:
                        # Exponential backoff capped at 35s
                        wait_s = min(5 * (2 ** poll_attempt), 35)
                        logger.warning(
                            f"[TG BOT] Conflict error — old instance still holds the session. "
                            f"Re-kicking via getUpdates then retrying in {wait_s}s "
                            f"(attempt {poll_attempt+1}/8)"
                        )
                        # Async HTTP kick: evict old poller via getUpdates(timeout=0)
                        try:
                            async with aiohttp.ClientSession() as sess:
                                async with sess.get(
                                    f"https://api.telegram.org/bot{token}/getUpdates",
                                    params={"timeout": 0, "limit": 1},
                                    timeout=aiohttp.ClientTimeout(total=10)
                                ) as resp:
                                    kick_data = await resp.json()
                                    if kick_data.get("ok"):
                                        logger.info("[TG BOT] Async kick succeeded — old poller evicted.")
                                    else:
                                        logger.warning(f"[TG BOT] Async kick returned: {kick_data}")
                        except Exception as kick_e:
                            logger.warning(f"[TG BOT] Async kick error: {kick_e}")

                        await asyncio.sleep(wait_s)
                        # Re-build app with fresh connection for next attempt
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
                    else:
                        logger.error(f"[TG BOT] Polling error: {e}")
                        break

        loop.run_until_complete(_async_polling())
        # ────────────────────────────────────────────────────────────────────

    thread = threading.Thread(target=_run, daemon=True, name="TelegramBot")
    thread.start()
    logger.info("[TG BOT] Bot thread launched.")

    # Start daily summary scheduler (sends report at 9 PM IST every day)
    _start_daily_summary_thread(token, admin_chat_id or "")

