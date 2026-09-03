import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Config (No API keys needed for scraping!)
# Supports multiple channels as a comma-separated list.
# TELEGRAM_CHANNELS=@Channel1,@Channel2,@Channel3
# Also accepts the old single-channel TELEGRAM_CHANNEL key for backward compat.
_raw_channels = os.getenv('TELEGRAM_CHANNELS') or os.getenv('TELEGRAM_CHANNEL', '')
TELEGRAM_CHANNELS = [ch.strip() for ch in _raw_channels.split(',') if ch.strip()]
SCRAPE_INTERVAL_MINUTES = int(os.getenv('SCRAPE_INTERVAL_MINUTES', 10))

# Pinterest Config
PINTEREST_ACCESS_TOKEN = os.getenv('PINTEREST_ACCESS_TOKEN')
PINTEREST_BOARD_ID = os.getenv('PINTEREST_BOARD_ID')

# Multi-Board Routing — set board IDs per genre in .env
# Get board IDs from: pinterest.com/YOUR_USERNAME/YOUR_BOARD_NAME/ (last part of URL)
# Falls back to PINTEREST_BOARD_ID if a genre-specific board is not configured.
BOARD_ID_SHONEN  = os.getenv('BOARD_ID_SHONEN',  '')   # Action/battle anime
BOARD_ID_ISEKAI  = os.getenv('BOARD_ID_ISEKAI',  '')   # Isekai/fantasy anime
BOARD_ID_ROMANCE = os.getenv('BOARD_ID_ROMANCE', '')   # Romance/slice-of-life
BOARD_ID_HORROR  = os.getenv('BOARD_ID_HORROR',  '')   # Horror/dark anime
BOARD_ID_MECHA   = os.getenv('BOARD_ID_MECHA',   '')   # Mecha/robot anime
BOARD_ID_SPORTS  = os.getenv('BOARD_ID_SPORTS',  '')   # Sports anime
BOARD_ID_FANTASY = os.getenv('BOARD_ID_FANTASY', '')   # Fantasy/other anime
BOARD_ID_GENERAL = os.getenv('BOARD_ID_GENERAL', os.getenv('PINTEREST_BOARD_ID', ''))  # Default

# Make.com Webhook Config (bypass Pinterest API review — instant public pins!)
# Get this URL from Make.com after creating your scenario with a Custom Webhook trigger.
MAKE_WEBHOOK_URL = os.getenv('MAKE_WEBHOOK_URL', '')

# Auto-post mode: true = fully automatic, false = Telegram approval button required
AUTO_POST_MODE = os.getenv('AUTO_POST_MODE', 'true').lower() in ('true', '1', 'yes')

# Amazon Config
AMAZON_AFFILIATE_TAG = os.getenv('AMAZON_AFFILIATE_TAG')

# Click Tracking & Redirect Config (optional — set your Render URL to track Pinterest clicks in Telegram)
# e.g. APP_BASE_URL=https://pinterest-bot.onrender.com
APP_BASE_URL = (os.getenv('APP_BASE_URL') or os.getenv('RENDER_EXTERNAL_URL', '')).rstrip('/')
CLICK_NOTIFICATION = os.getenv('CLICK_NOTIFICATION', 'false').lower() in ('true', '1', 'yes')

# Pinterest Profile URL (for direct buttons in Telegram)
PINTEREST_PROFILE_URL = os.getenv('PINTEREST_PROFILE_URL', 'https://www.pinterest.com/animeasthetic/')


# Gemini AI Config (optional — enables AI-powered captions)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')  # Free key from aistudio.google.com

# OpenRouter Config (optional — enables vision AI captions, sees the actual image!)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # Free key from openrouter.ai/keys

# Telegram Control Bot Config
TELEGRAM_BOT_TOKEN     = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_CHAT_ID')  # Set after first /start

# JSONBin.io — cloud persistence (survives Render restarts)
JSONBIN_API_KEY = os.getenv('JSONBIN_API_KEY', '')
JSONBIN_BIN_ID  = os.getenv('JSONBIN_BIN_ID', '')

# Scheduler Config
POST_DELAY_MINUTES = int(os.getenv('POST_DELAY_MINUTES', 60))
MAX_POSTS_PER_DAY  = int(os.getenv('MAX_POSTS_PER_DAY',  3))

# Dry Run Mode — set DRY_RUN=false in .env to go live
# When MAKE_WEBHOOK_URL is set, DRY_RUN=false uses Make.com (no Pinterest API approval needed!)
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() in ('true', '1', 'yes')

# Validation
def validate_config():
    # Pinterest token is optional when Make.com webhook is configured
    if not MAKE_WEBHOOK_URL and not PINTEREST_ACCESS_TOKEN:
        raise ValueError(
            "Missing required config: set either MAKE_WEBHOOK_URL (recommended, instant) "
            "or PINTEREST_ACCESS_TOKEN in your .env file."
        )

    if not TELEGRAM_CHANNELS:
        raise ValueError("Missing required environment variable: TELEGRAM_CHANNELS")

    if not AMAZON_AFFILIATE_TAG:
        raise ValueError("Missing required environment variable: AMAZON_AFFILIATE_TAG")

    # Print active posting method
    if MAKE_WEBHOOK_URL:
        mode_label = "AUTO-PILOT" if AUTO_POST_MODE else "TELEGRAM APPROVAL (tap button to post)"
        print(f"[config] Posting method : Make.com Webhook ({mode_label})")
    elif PINTEREST_ACCESS_TOKEN:
        print(f"[config] Posting method : Pinterest API (Official)")

    if DRY_RUN:
        print("[config] Mode           : DRY RUN (pins logged, not posted)")
    else:
        print("[config] Mode           : LIVE (pins will be posted!)")

    if GEMINI_API_KEY:
        print(f"[config] Gemini AI captions: ENABLED")
    else:
        print("[config] Gemini AI captions: DISABLED (set GEMINI_API_KEY to enable)")

    print(f"[config] Monitoring {len(TELEGRAM_CHANNELS)} channel(s): {', '.join(TELEGRAM_CHANNELS)}")

validate_config()
