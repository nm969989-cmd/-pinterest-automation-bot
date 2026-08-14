import os
import re
from logger import get_logger
from keep_alive import keep_alive
from telegram_listener import start_listener, set_image_callback
from image_processor import process_image
from amazon_search import generate_amazon_link
from pinterest_uploader import upload_to_pinterest
from scheduler import scheduler
import config # Ensure env vars are loaded and validated

logger = get_logger(__name__)

def generate_pin_content(caption, channel_name):
    """
    Generates the title and description for the Pin based on the Telegram caption.
    """
    # Clean up caption to extract a potential anime name
    # This is a simple heuristic: take the first line, remove emojis
    first_line = caption.split('\n')[0] if caption else "Awesome Anime Art"
    # Remove common emojis/hashtags to get a cleaner title
    clean_title = re.sub(r'[^\w\s\-\.]', '', first_line).strip()
    
    if not clean_title:
        clean_title = f"Anime Art from {channel_name}"
        
    title = f"{clean_title} — Epic Anime Poster 🔥"
    
    description = (
        f"Amazing {clean_title} artwork! 🎌✨\n\n"
        f"Find merchandise, figures & posters for {clean_title} on Amazon 👇\n"
        "##LINK_PLACEHOLDER##\n\n"
        f"Source: @{channel_name}\n"
        "#ad #affiliate #anime #animeart #manga #fanart"
    )
    
    return clean_title, title, description

def handle_new_image(filepath, caption, channel_name):
    """
    Callback fired when a new image is downloaded from Telegram.
    This runs synchronously within the async Telegram loop, so we delegate
    heavy work to the scheduler thread.
    """
    logger.info(f"Main handler received new image: {filepath}")
    
    # 1. Process Image
    processed_path = process_image(filepath, channel_name)
    
    # 2. Generate Content
    anime_name, title, desc_template = generate_pin_content(caption, channel_name)
    
    # 3. Generate Amazon Link
    amazon_link = generate_amazon_link(anime_name)
    
    # Insert link into description
    description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link)
    
    # 4. Queue the upload
    # We pass the upload function and its arguments to the scheduler
    scheduler.add_to_queue(
        upload_to_pinterest, 
        image_path=processed_path,
        title=title,
        description=description,
        link=amazon_link
    )
    
    # Note: We don't delete the original file here because the upload is asynchronous.
    # A robust system would clean up files after successful upload in the scheduler.

def main():
    logger.info("Initializing Anime Pinterest Bot (Web Scraper Edition)...")
    
    # 1. Start Flask web server (Keep-alive for Render)
    keep_alive()
    logger.info("Keep-alive server started on port 8080.")
    
    # 2. Start Scheduler (handles rate limiting and uploading)
    scheduler.start()
    logger.info("Upload scheduler started.")
    
    # 3. Set callback and start Telegram scraper (blocks forever)
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
