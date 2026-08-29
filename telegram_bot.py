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
  /queue         - Show pending pin queue size
  /clearqueue    - Clear the pending queue
  /ping          - Check if bot responds
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
    from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
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
        "/stats          - Pins count and queue\n"
        "/preview        - Last pin with image\n"
        "/logs           - Recent log output\n"
        "/queue          - Pending queue size\n"
        "/channels       - Monitored channels\n"
        "/ping           - Check bot is alive\n\n"
        "--- MAKE.COM WEBHOOK ---\n"
        f"/autopilot      - Toggle auto-post vs approval mode (Webhook: {make_status})\n"
        "/testpost       - Send a test pin right now via webhook\n\n"
        "--- CONTROL ---\n"
        "/pause          - Pause posting\n"
        "/resume         - Resume posting\n"
        "/dryrun         - Toggle dry-run on/off\n"
        "/golive         - Enable real Pinterest posting\n"
        "/clearqueue     - Clear pending queue\n\n"
        "--- SETTINGS ---\n"
        "/addchannel @ch - Add source channel\n"
        "/removechannel @ch - Remove channel\n"
        "/setdelay [min] - Set posting delay\n"
        "/setmax [num]   - Set max pins/day\n"
    )


async def cmd_status(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    uptime = datetime.datetime.now() - _state["start_time"]
    h, rem = divmod(int(uptime.total_seconds()), 3600)
    m = rem // 60
    mode   = "DRY RUN" if _state["dry_run"] else "LIVE (posting to Pinterest!)"
    paused = "PAUSED" if _state["is_paused"] else "RUNNING"
    make_url = os.getenv("MAKE_WEBHOOK_URL", "")
    post_method = "Make.com Webhook" if make_url else "Pinterest API"
    auto_label  = "AUTO-PILOT" if _state.get("auto_post", True) else "APPROVAL MODE (tap button)"
    await update.message.reply_text(
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
        f"Queue    : {_state['queue_size']} pending"
    )


async def cmd_stats(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
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
    await update.message.reply_text(
        f"Pin Statistics\n"
        f"{'='*25}\n"
        f"Today    : {today_db} pins\n"
        f"Total    : {total_db} pins\n"
        f"Queue    : {_state['queue_size']} pending\n"
        f"Last pin : {last_time}\n"
        f"Mode     : {'DRY RUN' if _state['dry_run'] else 'LIVE'}\n\n"
        f"Top Anime:\n{top_str or '  (none yet)'}"
    )


async def cmd_dailyreport(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    """Send today's detailed pin report on demand."""
    if not _is_admin(update): return
    await _send_daily_report(update.effective_chat.id)


async def _send_daily_report(chat_id):
    """Build and send the daily summary to the given chat_id."""
    if not _app_ref:
        return
    try:
        from database import get_today_uploads, get_all_time_stats
        pins   = get_today_uploads()
        stats  = get_all_time_stats()
        today  = datetime.date.today().strftime("%d %b %Y")
        count  = len(pins)

        if count == 0:
            msg = (
                f"Daily Pinterest Report — {today}\n"
                f"{'='*30}\n"
                f"No pins were posted today.\n\n"
                f"Total all-time: {stats['total']} pins"
            )
        else:
            lines = []
            for i, p in enumerate(pins, 1):
                anime = p.get('anime') or 'Unknown'
                title = p.get('title') or 'Untitled'
                lines.append(f"{i}. [{anime}] {title}")

            # Top anime today
            from collections import Counter
            anime_counts = Counter(p.get('anime') or 'Unknown' for p in pins)
            top_today = "\n".join(
                f"  • {a}: {c} pins" for a, c in anime_counts.most_common(5)
            )

            msg = (
                f"Daily Pinterest Report — {today}\n"
                f"{'='*30}\n"
                f"Pins posted today : {count}\n"
                f"All-time total    : {stats['total']} pins\n\n"
                f"Today's Anime Breakdown:\n{top_today}\n\n"
                f"Today's Pins:\n"
                + "\n".join(lines)
            )

        # Send (split if too long)
        for chunk in [msg[i:i+4000] for i in range(0, len(msg), 4000)]:
            await _app_ref.bot.send_message(chat_id=chat_id, text=chunk)
        logger.info(f"[TG BOT] Daily report sent to {chat_id}")

    except Exception as e:
        logger.error(f"[TG BOT] Daily report error: {e}")


def _start_daily_summary_thread(token: str, admin_chat_id: str):
    """
    Background thread that sends a daily summary at 9:00 AM IST (03:30 UTC).
    Runs forever, waking up every minute to check the time.
    """
    def _loop():
        logger.info("[TG BOT] Daily summary scheduler started (fires at 09:00 AM IST / 03:30 UTC).")
        sent_today = None
        while True:
            now_utc = datetime.datetime.utcnow()
            # 09:00 AM IST = 03:30 UTC
            if now_utc.hour == 3 and now_utc.minute == 30 and now_utc.date() != sent_today:
                if _app_ref and admin_chat_id:
                    sent_today = now_utc.date()
                    logger.info("[TG BOT] Sending scheduled daily report (9 AM IST)...")
                    asyncio.run_coroutine_threadsafe(
                        _send_daily_report(admin_chat_id), _loop_ref
                    )
            time.sleep(55)  # Check every ~1 min

    t = threading.Thread(target=_loop, daemon=True, name="DailySummary")
    t.start()


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
    if not _is_admin(update): return
    q = _state.get("queue_size", 0)
    if q == 0:
        await update.message.reply_text(
            "Queue is empty.\n"
            "Bot will add pins after next scrape cycle."
        )
    else:
        await update.message.reply_text(
            f"{q} pin(s) are waiting in the queue.\n"
            f"They will be posted {_state['post_delay']} min apart."
        )


async def cmd_clearqueue(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    _state["queue_size"] = 0
    if _scheduler_ref:
        try:
            with _scheduler_ref._lock:
                _scheduler_ref._queue.clear()
        except Exception:
            pass
    await update.message.reply_text(
        "Queue cleared! All pending pins removed.\n"
        "New pins will be added on next scrape cycle."
    )
    logger.info("[TG BOT] Queue cleared by admin.")


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


# -- Notify admin helper ------------------------------------------------------
_app_ref  = None
_loop_ref = None


def notify_admin(message: str):
    """Send a notification to the admin from any thread."""
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

        # ── Self-heal: Force Telegram to release any stuck polling session ──
        # Retries with backoff if rate-limited (429). Prevents Conflict errors.
        for attempt in range(5):
            try:
                r = _req.get(
                    f"https://api.telegram.org/bot{token}/close",
                    timeout=8
                )
                data = r.json()
                if data.get("ok"):
                    logger.info("[TG BOT] Session closed cleanly.")
                    time.sleep(3)
                    break
                elif r.status_code == 429:
                    wait = data.get("parameters", {}).get("retry_after", 30)
                    logger.warning(f"[TG BOT] Rate-limited on /close. Waiting {wait}s...")
                    time.sleep(wait + 2)
                else:
                    # Already closed or not running — fine to proceed
                    time.sleep(2)
                    break
            except Exception as e:
                logger.warning(f"[TG BOT] /close attempt {attempt+1} failed: {e}")
                time.sleep(3)
        # ────────────────────────────────────────────────────────────────────

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
            ("clearqueue",    cmd_clearqueue),
        ]
        for cmd, handler in handlers:
            app.add_handler(CommandHandler(cmd, handler))

        # Register inline button callback handler
        app.add_handler(CallbackQueryHandler(handle_approval_callback))

        # ── Register the / menu that shows in Telegram UI ──────────────────
        from telegram import BotCommand

        async def _set_menu(application):
            await application.bot.set_my_commands([
                BotCommand("ping",          "Check if bot is alive"),
                BotCommand("status",        "Bot status, mode and uptime"),
                BotCommand("stats",         "Pins count and queue size"),
                BotCommand("dailyreport",   "Today's detailed Pinterest report"),
                BotCommand("preview",       "Last generated pin with image"),
                BotCommand("logs",          "Show recent log output"),
                BotCommand("queue",         "Show pending queue size"),
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
                BotCommand("clearqueue",    "Clear pending queue"),
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
            try:
                async with app:
                    # Register the command menu
                    await _set_menu(app)
                    # Start polling — no signal handlers
                    await app.updater.start_polling(drop_pending_updates=True)
                    await app.start()
                    logger.info("[TG BOT] Polling started successfully (thread-safe mode).")
                    # Keep running until the loop is stopped
                    while True:
                        await asyncio.sleep(60)
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"[TG BOT] Polling error: {e}")

        loop.run_until_complete(_async_polling())
        # ────────────────────────────────────────────────────────────────────

    thread = threading.Thread(target=_run, daemon=True, name="TelegramBot")
    thread.start()
    logger.info("[TG BOT] Bot thread launched.")

    # Start daily summary scheduler (sends report at 9 PM IST every day)
    _start_daily_summary_thread(token, admin_chat_id or "")

