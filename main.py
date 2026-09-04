import os
from logger import get_logger
from keep_alive import keep_alive
from telegram_listener import start_listener, set_image_callback
from image_processor import process_image
from amazon_search import generate_amazon_link
from pinterest_uploader import upload_to_pinterest
from scheduler import scheduler
from ai_caption import generate_pin_content
from board_router import get_board_for_anime
from hashtag_optimizer import optimize_hashtags, replace_hashtags_in_description
from telegram_bot import start_bot, record_pin, send_pin_approval_request
from jsonbin_sync import restore_db_from_cloud, start_sync_thread, save_cloud_state
from crash_protection import init_crash_protection, cleanup_old_files
import config  # Ensure env vars are loaded and validated

# Log AI status at startup
if config.OPENROUTER_API_KEY:
    print("[config] Vision AI captions: ENABLED (OpenRouter - sees actual images!)")
else:
    print("[config] AI captions: DISABLED (regex fallback) - set OPENROUTER_API_KEY to enable")

# Log Pinterest board status at startup
if config.PINTEREST_BOARD_ID:
    print(f"[config] Pinterest board: CONFIGURED (ID: {config.PINTEREST_BOARD_ID})")
elif config.MAKE_WEBHOOK_URL:
    print("[config] Pinterest board: Using Make.com default board (PINTEREST_BOARD_ID is empty in .env)")
else:
    print("[config] Pinterest board: WARNING - No board ID or Make.com webhook configured")

logger = get_logger(__name__)

def handle_new_image(filepath, caption, channel_name, image_url=""):
    """
    Callback fired when a new image is downloaded from Telegram.
    image_url: the original Telegram CDN URL — stored in queue so we can
               re-download the image if Render's ephemeral FS wipes local files.
    Wrapped in try/except so one bad image never crashes the whole bot.
    """
    try:
        logger.info(f"Main handler received new image: {filepath}")

        # 1. Process Image
        processed_path = process_image(filepath, channel_name)

        # 2. Generate Content (Vision AI via OpenRouter)
        anime_name, title, desc_template = generate_pin_content(
            caption,
            channel_name,
            image_path=processed_path,
            api_key=None,  # Gemini removed — OpenRouter only
            openrouter_key=config.OPENROUTER_API_KEY
        )

        # 3. Extract character name from AI title for more precise Amazon product match
        #    Title format is usually: "CharacterName - AnimeName Poster 🔥" or "CharacterName AnimeName merch"
        character_name = ""
        if title:
            if " - " in title:
                # e.g. "Tanjiro - Demon Slayer Poster" → first word before dash = character
                raw_char = title.split(" - ")[0].strip().split()[0]
            else:
                # No dash: take first word
                raw_char = title.strip().split()[0]
            from amazon_search import clean_character_name
            character_name = clean_character_name(raw_char)
            if character_name:
                logger.info(f"[Main] Character extracted from title: '{character_name}'")


        # 4. Route to correct Pinterest board + get genre
        genre, board_id = get_board_for_anime(anime_name)

        # 5. Generate Amazon affiliate deep link (with character for specific product match)
        amazon_link = generate_amazon_link(anime_name, character_name=character_name, title=title)

        # 6. Insert affiliate link into description
        description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link)

        # 7. Replace AI hashtags with SEO-optimized hashtag set (12-15 tags)
        optimized_tags = optimize_hashtags(
            anime_name=anime_name,
            genre=genre,
            character_name=character_name,
            product_hint="anime merch",
        )
        description = replace_hashtags_in_description(description, optimized_tags)

        # Guarantee FTC & Pinterest required disclosure tags are ALWAYS present
        # (already included in optimized_tags, but double-check for safety)
        if "#ad" not in description.lower():
            description = description.rstrip() + "\n#ad #affiliate"

        # 8. Record pin for Telegram /preview and /stats
        record_pin(anime_name, title, description, amazon_link, processed_path)

        # 9. Queue with priority scheduling (new images always before backlog)
        if config.MAKE_WEBHOOK_URL and not config.DRY_RUN and not config.AUTO_POST_MODE:
            logger.info("[Main] Sending pin to Telegram for approval...")
            send_pin_approval_request(processed_path, title, description, amazon_link)
        else:
            safe_post_id = filepath.replace("/", "_").replace("\\", "_")
            scheduler.enqueue_new_image(
                post_id=safe_post_id,
                image_path=processed_path,
                title=title,
                description=description,
                link=amazon_link,
                anime_name=anime_name,
                board_id=board_id,
                image_url=image_url,  # Store Telegram CDN URL for file resurrection after restarts
            )

        # 10. Clean up original download to save disk space (keep processed copy)
        try:
            if filepath != processed_path and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[Main] handle_new_image failed for {filepath}: {e}", exc_info=True)


def main():
    logger.info("Initializing Anime Pinterest Bot (Web Scraper Edition)...")

    # -1. Crash protection FIRST — catches everything from here onwards
    init_crash_protection()

    # 0. Restore persisted state from JSONBin cloud (survives Render restarts!)
    restore_db_from_cloud()

    # 1. Start Flask web server (Keep-alive for Render)
    keep_alive()
    logger.info("Keep-alive server started on port 8080.")

    # 2. Start Telegram Control Bot (in background thread)
    if config.TELEGRAM_BOT_TOKEN:
        start_bot(
            token=config.TELEGRAM_BOT_TOKEN,
            admin_chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            channels=config.TELEGRAM_CHANNELS,
            dry_run=config.DRY_RUN,
            post_delay=config.POST_DELAY_MINUTES,
            max_per_day=config.MAX_POSTS_PER_DAY,   # ← fixes the "5/15" bug
        )
    else:
        logger.info("[TG BOT] No TELEGRAM_BOT_TOKEN set, control bot disabled.")

    # 3. Start Scheduler (smart time-slot posting)
    scheduler.start()
    logger.info("Upload scheduler started.")

    # 4. Start JSONBin sync thread (saves state every 30 min)
    start_sync_thread()

    # 5. Set callback and start Telegram scraper (blocks forever)
    set_image_callback(handle_new_image)
    start_listener()

if __name__ == "__main__":
    # Ensure required directories exist
    for d in ['downloads', 'processed', 'logs']:
        os.makedirs(d, exist_ok=True)
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user — saving state...")
        try:
            save_cloud_state()
        except Exception:
            pass
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
        try:
            save_cloud_state()  # Save to JSONBin before dying
        except Exception:
            pass
        raise  # Re-raise so Render sees the crash and restarts
