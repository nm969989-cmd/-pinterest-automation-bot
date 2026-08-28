import os
from logger import get_logger
from keep_alive import keep_alive
from telegram_listener import start_listener, set_image_callback
from image_processor import process_image
from amazon_search import generate_amazon_link
from pinterest_uploader import upload_to_pinterest
from scheduler import scheduler
from ai_caption import generate_pin_content
from telegram_bot import start_bot, record_pin
import config  # Ensure env vars are loaded and validated

# Log AI status at startup
if config.OPENROUTER_API_KEY:
    print(f"[config] Vision AI captions: ENABLED (OpenRouter - sees actual images!)")
elif config.GEMINI_API_KEY:
    print("[config] AI captions: ENABLED (Gemini text-only)")
else:
    print("[config] AI captions: DISABLED (regex fallback) - set OPENROUTER_API_KEY to enable")

logger = get_logger(__name__)

def handle_new_image(filepath, caption, channel_name):
    """
    Callback fired when a new image is downloaded from Telegram.
    This runs synchronously within the async Telegram loop, so we delegate
    heavy work to the scheduler thread.
    """
    logger.info(f"Main handler received new image: {filepath}")

    # 1. Process Image
    processed_path = process_image(filepath, channel_name)

    # 2. Generate Content (Vision AI via OpenRouter sees the image, falls back to regex)
    anime_name, title, desc_template = generate_pin_content(
        caption,
        channel_name,
        image_path=processed_path,
        api_key=config.GEMINI_API_KEY,
        openrouter_key=config.OPENROUTER_API_KEY
    )

    # 3. Generate Amazon Link (amazon.in with affiliate tag)
    amazon_link = generate_amazon_link(anime_name)

    # 4. Insert affiliate link into description
    description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link)

    # 5. Queue the upload
    scheduler.add_to_queue(
        upload_to_pinterest,
        image_path=processed_path,
        title=title,
        description=description,
        link=amazon_link
    )

    # 6. Record pin for Telegram /preview and /stats
    record_pin(anime_name, title, description, amazon_link, processed_path)


def main():
    logger.info("Initializing Anime Pinterest Bot (Web Scraper Edition)...")
    
    # 1. Start Flask web server (Keep-alive for Render)
    keep_alive()
    logger.info("Keep-alive server started on port 8080.")

    # 2. Start Telegram Control Bot (in background thread)
    if config.TELEGRAM_BOT_TOKEN:
        start_bot(
            token=config.TELEGRAM_BOT_TOKEN,
            admin_chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            channels=config.TELEGRAM_CHANNELS,
            dry_run=config.DRY_RUN
        )
    else:
        logger.info("[TG BOT] No TELEGRAM_BOT_TOKEN set, control bot disabled.")

    # 3. Start Scheduler (handles rate limiting and uploading)
    scheduler.start()
    logger.info("Upload scheduler started.")

    # 4. Set callback and start Telegram scraper (blocks forever)
    set_image_callback(handle_new_image)
    start_listener()

if __name__ == "__main__":
    try:
        # Check if directories exist
        for d in ['downloads', 'processed', 'logs']:
            if not os.path.exists(d):
                os.makedirs(d)
                
        # Run synchronous loop
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception as e:
        logger.critical(f"Bot crashed: {str(e)}")
