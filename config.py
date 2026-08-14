import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Config (No API keys needed for scraping!)
TELEGRAM_CHANNEL = os.getenv('TELEGRAM_CHANNEL')
SCRAPE_INTERVAL_MINUTES = int(os.getenv('SCRAPE_INTERVAL_MINUTES', 10))

# Pinterest Config
PINTEREST_ACCESS_TOKEN = os.getenv('PINTEREST_ACCESS_TOKEN')
PINTEREST_BOARD_ID = os.getenv('PINTEREST_BOARD_ID')

# Amazon Config
AMAZON_AFFILIATE_TAG = os.getenv('AMAZON_AFFILIATE_TAG')

# Scheduler Config
POST_DELAY_MINUTES = int(os.getenv('POST_DELAY_MINUTES', 10))
MAX_POSTS_PER_DAY = int(os.getenv('MAX_POSTS_PER_DAY', 15))

# Validation
def validate_config():
    required_vars = [
        ('TELEGRAM_CHANNEL', TELEGRAM_CHANNEL),
        ('PINTEREST_ACCESS_TOKEN', PINTEREST_ACCESS_TOKEN),
        ('AMAZON_AFFILIATE_TAG', AMAZON_AFFILIATE_TAG)
    ]
    
    missing = [name for name, val in required_vars if not val]
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

validate_config()
