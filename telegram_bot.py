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
  /pause         - Pause posting
  /resume        - Resume posting
  /queue         - Show pending pin queue size
  /clearqueue    - Clear the pending queue
  /ping          - Check if bot responds
"""

import os
import threading
import datetime
import asyncio
from logger import get_logger

logger = get_logger(__name__)

try:
    from telegram import Update
    from telegram.ext import Application, CommandHandler, ContextTypes
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
    "channels":      [],
    "admin_chat_id": None,
    "post_delay":    10,
    "max_per_day":   15,
}

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
    await update.message.reply_text(
        f"Bot Status\n"
        f"{'='*25}\n"
        f"Status   : {paused}\n"
        f"Mode     : {mode}\n"
        f"Uptime   : {h}h {m}m\n"
        f"Channels : {len(_state['channels'])} monitored\n"
        f"Delay    : {_state['post_delay']} min between pins\n"
        f"Max/day  : {_state['max_per_day']} pins\n"
        f"Today    : {_state['posts_today']} pins posted\n"
        f"Queue    : {_state['queue_size']} pending"
    )


async def cmd_stats(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    if not _is_admin(update): return
    pin = _state.get("last_pin")
    last_time = pin["time"] if pin else "None yet"
    await update.message.reply_text(
        f"Pin Statistics\n"
        f"{'='*25}\n"
        f"Today    : {_state['posts_today']} pins\n"
        f"Total    : {_state['posts_total']} pins\n"
        f"Queue    : {_state['queue_size']} pending\n"
        f"Last pin : {last_time}\n"
        f"Mode     : {'DRY RUN' if _state['dry_run'] else 'LIVE'}"
    )


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
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        _loop_ref = loop

        app = Application.builder().token(token).build()

        # Register all commands
        handlers = [
            ("start",         cmd_start),
            ("ping",          cmd_ping),
            ("help",          cmd_help),
            ("status",        cmd_status),
            ("stats",         cmd_stats),
            ("preview",       cmd_preview),
            ("logs",          cmd_logs),
            ("channels",      cmd_channels),
            ("addchannel",    cmd_addchannel),
            ("removechannel", cmd_removechannel),
            ("setdelay",      cmd_setdelay),
            ("setmax",        cmd_setmax),
            ("dryrun",        cmd_dryrun),
            ("golive",        cmd_golive),
            ("pause",         cmd_pause),
            ("resume",        cmd_resume),
            ("queue",         cmd_queue),
            ("clearqueue",    cmd_clearqueue),
        ]
        for cmd, handler in handlers:
            app.add_handler(CommandHandler(cmd, handler))

        _app_ref = app
        logger.info("[TG BOT] @AnimanoizingBot is online! Send /start in Telegram.")
        app.run_polling(drop_pending_updates=True)

    thread = threading.Thread(target=_run, daemon=True, name="TelegramBot")
    thread.start()
    logger.info("[TG BOT] Bot thread launched.")
