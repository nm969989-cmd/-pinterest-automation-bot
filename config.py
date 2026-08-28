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

# Amazon Config
AMAZON_AFFILIATE_TAG = os.getenv('AMAZON_AFFILIATE_TAG')

# Gemini AI Config (optional — enables AI-powered captions)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')  # Free key from aistudio.google.com

# OpenRouter Config (optional — enables vision AI captions, sees the actual image!)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY')  # Free key from openrouter.ai/keys

# Telegram Control Bot Config
TELEGRAM_BOT_TOKEN     = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_ADMIN_CHAT_ID = os.getenv('TELEGRAM_ADMIN_CHAT_ID')  # Set after first /start

# Scheduler Config
POST_DELAY_MINUTES = int(os.getenv('POST_DELAY_MINUTES', 10))
MAX_POSTS_PER_DAY = int(os.getenv('MAX_POSTS_PER_DAY', 15))

# Dry Run Mode — set DRY_RUN=false in .env to go live once API access is approved
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() in ('true', '1', 'yes')

# Validation
def validate_config():
    required_vars = [
        ('TELEGRAM_CHANNELS', TELEGRAM_CHANNELS),
        ('PINTEREST_ACCESS_TOKEN', PINTEREST_ACCESS_TOKEN),
        ('AMAZON_AFFILIATE_TAG', AMAZON_AFFILIATE_TAG)
    ]

    missing = [name for name, val in required_vars if not val]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

    if GEMINI_API_KEY:
        print(f"[config] Gemini AI captions: ENABLED")
    else:
        print("[config] Gemini AI captions: DISABLED (set GEMINI_API_KEY to enable)")

    print(f"[config] Monitoring {len(TELEGRAM_CHANNELS)} channel(s): {', '.join(TELEGRAM_CHANNELS)}")

validate_config()
