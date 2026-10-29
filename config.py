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

# Amazon Config (Dual-Routing: India vs US/Global)
AMAZON_AFFILIATE_TAG = os.getenv('AMAZON_AFFILIATE_TAG', 'animeasthet-20')
AMAZON_AFFILIATE_TAG_US = os.getenv('AMAZON_AFFILIATE_TAG_US', AMAZON_AFFILIATE_TAG or 'animeasthet-20')
AMAZON_AFFILIATE_TAG_IN = os.getenv('AMAZON_AFFILIATE_TAG_IN', 'animeasthet06-21')

# Displate Affiliate Config (Metal Art Wall Posters — 15-25% commission)
DISPLATE_AFFILIATE_TAG = os.getenv('DISPLATE_AFFILIATE_TAG', '').strip()
DISPLATE_ENABLED       = os.getenv('DISPLATE_ENABLED', 'true').lower() in ('true', '1', 'yes')

# Click Tracking & Redirect Config (active Render URL enables sub-15ms self-healing redirects & click tracking)
DEFAULT_RENDER_URL = 'https://pinterest-automation-bot.onrender.com'
APP_BASE_URL = (
    os.getenv('APP_BASE_URL')
    or os.getenv('RENDER_EXTERNAL_URL')
    or DEFAULT_RENDER_URL
).rstrip('/')
CLICK_NOTIFICATION = os.getenv('CLICK_NOTIFICATION', 'true').lower() in ('true', '1', 'yes')

# Pinterest Profile URL (for direct buttons in Telegram)
PINTEREST_PROFILE_URL = os.getenv('PINTEREST_PROFILE_URL', 'https://in.pinterest.com/muthelyrics/')



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

# Are.na Visual Curation Platform
ARENA_ACCESS_TOKEN = os.getenv('ARENA_ACCESS_TOKEN', '')
ARENA_CHANNEL_SLUG = os.getenv('ARENA_CHANNEL_SLUG', 'aesthetic-inspiration')
ARENA_ENABLED      = os.getenv('ARENA_ENABLED', 'false').lower() in ('true', '1', 'yes')


# Bluesky Cross-Post (AT Protocol)
BLUESKY_HANDLE       = os.getenv('BLUESKY_HANDLE', '')
BLUESKY_APP_PASSWORD = os.getenv('BLUESKY_APP_PASSWORD', '')
BLUESKY_ENABLED      = os.getenv('BLUESKY_ENABLED', 'false').lower() in ('true', '1', 'yes')

# Raindrop.io Visual Bookmarking (Pinterest-like collections & boards)
RAINDROP_ACCESS_TOKEN  = os.getenv('RAINDROP_ACCESS_TOKEN', '')
RAINDROP_COLLECTION_ID = int(os.getenv('RAINDROP_COLLECTION_ID', '0') or '0')
RAINDROP_ENABLED       = os.getenv('RAINDROP_ENABLED', 'false').lower() in ('true', '1', 'yes')

# Mastodon (Fediverse) Social Microblogging
MASTODON_INSTANCE_URL  = os.getenv('MASTODON_INSTANCE_URL', 'https://mastodon.social').strip().rstrip('/')
MASTODON_ACCESS_TOKEN  = os.getenv('MASTODON_ACCESS_TOKEN', '').strip()
MASTODON_ENABLED       = os.getenv('MASTODON_ENABLED', 'false').lower() in ('true', '1', 'yes') and bool(MASTODON_ACCESS_TOKEN)

# DeviantArt Art Community Cross-Post
DEVIANTART_CLIENT_ID     = os.getenv('DEVIANTART_CLIENT_ID', '').strip()
DEVIANTART_CLIENT_SECRET = os.getenv('DEVIANTART_CLIENT_SECRET', '').strip()
DEVIANTART_ACCESS_TOKEN  = os.getenv('DEVIANTART_ACCESS_TOKEN', '').strip()
DEVIANTART_REFRESH_TOKEN = os.getenv('DEVIANTART_REFRESH_TOKEN', '').strip()
DEVIANTART_ENABLED       = os.getenv('DEVIANTART_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET)


# Pixelfed (Fediverse) Photo & Visual Art Sharing
PIXELFED_INSTANCE_URL  = os.getenv('PIXELFED_INSTANCE_URL', 'https://pixelfed.social').strip().rstrip('/')
PIXELFED_ACCESS_TOKEN  = os.getenv('PIXELFED_ACCESS_TOKEN', '').strip()
PIXELFED_ENABLED       = os.getenv('PIXELFED_ENABLED', 'false').lower() in ('true', '1', 'yes')

# Freeimage.host Dedicated Image Uploading
FREEIMAGE_API_KEY      = os.getenv('FREEIMAGE_API_KEY', '').strip()
FREEIMAGE_ENABLED      = os.getenv('FREEIMAGE_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(FREEIMAGE_API_KEY)

# ImgBB Dedicated Image Uploading
IMGBB_API_KEY          = os.getenv('IMGBB_API_KEY', '').strip()
IMGBB_ENABLED          = os.getenv('IMGBB_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(IMGBB_API_KEY)


# Imghippo Dedicated Image Uploading
IMGHIPPO_API_KEY       = os.getenv('IMGHIPPO_API_KEY', '').strip()
IMGHIPPO_ENABLED       = os.getenv('IMGHIPPO_ENABLED', 'false').lower() in ('true', '1', 'yes') and bool(IMGHIPPO_API_KEY)

# ImageKit.io Global CDN & Media Storage (Mumbai, India)
IMAGEKIT_PUBLIC_KEY    = os.getenv('IMAGEKIT_PUBLIC_KEY', '').strip()
IMAGEKIT_PRIVATE_KEY   = os.getenv('IMAGEKIT_PRIVATE_KEY', '').strip()
IMAGEKIT_URL_ENDPOINT  = os.getenv('IMAGEKIT_URL_ENDPOINT', '').strip().rstrip('/')
IMAGEKIT_ENABLED       = os.getenv('IMAGEKIT_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(IMAGEKIT_PRIVATE_KEY)

# ImgPile Free Permanent Image Hosting
IMGPILE_API_KEY        = os.getenv('IMGPILE_API_KEY', '').strip()
IMGPILE_ENABLED        = os.getenv('IMGPILE_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(IMGPILE_API_KEY)


# Wallhaven.cc Anime Wallpaper Platform
WALLHAVEN_API_KEY      = os.getenv('WALLHAVEN_API_KEY', '').strip()
WALLHAVEN_USERNAME     = os.getenv('WALLHAVEN_USERNAME', 'animizing').strip()
WALLHAVEN_ENABLED      = os.getenv('WALLHAVEN_ENABLED', 'true').lower() in ('true', '1', 'yes') and bool(WALLHAVEN_API_KEY or WALLHAVEN_USERNAME)
# Scheduler Config
POST_DELAY_MINUTES = int(os.getenv('POST_DELAY_MINUTES', 60))
_raw_max = int(os.getenv('MAX_POSTS_PER_DAY', 5))
# Legacy Render blueprint fix: 15 was an early template placeholder before the 5-slot scheduler was built.
# Sanitize 15 -> 5 to match the 5 IST slots (9 AM, 1 PM, 4 PM, 6 PM, 8 PM).
MAX_POSTS_PER_DAY  = 5 if _raw_max == 15 else max(1, _raw_max)

# Daily Performance Digest Config
DAILY_DIGEST_ENABLED = os.getenv('DAILY_DIGEST_ENABLED', 'true').lower() in ('true', '1', 'yes')
DAILY_DIGEST_TIME    = os.getenv('DAILY_DIGEST_TIME', '21:00').strip()  # 24-hr IST format (HH:MM)

# Viral Pin Surge Radar Config
VIRAL_RADAR_ENABLED         = os.getenv('VIRAL_RADAR_ENABLED', 'true').lower() in ('true', '1', 'yes')
VIRAL_SURGE_MIN_VISITS      = int(os.getenv('VIRAL_SURGE_MIN_VISITS', 10))
VIRAL_ALERT_COOLDOWN_HOURS  = int(os.getenv('VIRAL_ALERT_COOLDOWN_HOURS', 48))
VIRAL_MAX_DAILY_ALERTS      = int(os.getenv('VIRAL_MAX_DAILY_ALERTS', 3))
VIRAL_AUTO_SPINOFF          = os.getenv('VIRAL_AUTO_SPINOFF', 'false').lower() in ('true', '1', 'yes')

# Telegram Watermark Cleaner & Inpainter Config (Zero Stock Rejections)
WATERMARK_CLEANER_ENABLED = os.getenv('WATERMARK_CLEANER_ENABLED', 'true').lower() in ('true', '1', 'yes')

# 80/20 Organic Trust Ratio Config (Pinterest Ban & Shadowban Protection)
ORGANIC_TRUST_ENABLED = os.getenv('ORGANIC_TRUST_ENABLED', 'true').lower() in ('true', '1', 'yes')
ORGANIC_TRUST_RATIO   = float(os.getenv('ORGANIC_TRUST_RATIO', '0.20'))  # 0.20 = 20% (1 in 5 pins organic)
ORGANIC_PIN_LINK      = os.getenv('ORGANIC_PIN_LINK', '').strip() or os.getenv('PINTEREST_PROFILE_URL', 'https://www.pinterest.com/animanoizing').strip()

# Dry Run Mode — set DRY_RUN=false in .env to go live
# When MAKE_WEBHOOK_URL is set, DRY_RUN=false uses Make.com (no Pinterest API approval needed!)
DRY_RUN = os.getenv('DRY_RUN', 'true').lower() in ('true', '1', 'yes')

# A/B Title Split Testing
AB_TESTING_ENABLED = os.getenv("AB_TESTING_ENABLED", "false").lower() in ("true", "1", "yes")
AB_DECISION_HOURS  = int(os.getenv("AB_DECISION_HOURS", "48"))
AB_MIN_VISITS      = int(os.getenv("AB_MIN_VISITS", "3"))

# Revenue Tracking
REVENUE_TRACKING_ENABLED = os.getenv("REVENUE_TRACKING_ENABLED", "true").lower() in ("true", "1", "yes")
REVENUE_PER_CLICK_INR    = float(os.getenv("REVENUE_PER_CLICK_INR", "25.0"))

# Weekly Performance Report
WEEKLY_REPORT_ENABLED = os.getenv("WEEKLY_REPORT_ENABLED", "true").lower() in ("true", "1", "yes")
WEEKLY_REPORT_TIME    = os.getenv("WEEKLY_REPORT_TIME", "09:00").strip()
WEEKLY_REPORT_DAY     = int(os.getenv("WEEKLY_REPORT_DAY", "6"))  # 6 = Sunday

# Ghost Pin Reviver
GHOST_REVIVER_ENABLED      = os.getenv("GHOST_REVIVER_ENABLED", "true").lower() in ("true", "1", "yes")
GHOST_REVIVER_MIN_AGE_DAYS = int(os.getenv("GHOST_REVIVER_MIN_AGE_DAYS", "7"))
GHOST_REVIVER_MAX_BATCH    = int(os.getenv("GHOST_REVIVER_MAX_BATCH", "3"))

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
