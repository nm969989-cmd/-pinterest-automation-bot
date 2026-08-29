import os
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from logger import get_logger
from config import TELEGRAM_CHANNELS, SCRAPE_INTERVAL_MINUTES
from database import is_post_processed, mark_post_processed, get_processed_count

logger = get_logger(__name__)

# Callback function to handle new images
_on_new_image_callback = None

# Duplicate tracking is now handled by SQLite (see database.py)
# Persistent across restarts — no more re-uploading after a crash.

# A list of modern User-Agents to rotate through (Spoofing)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"
]

def set_image_callback(callback):
    global _on_new_image_callback
    _on_new_image_callback = callback

def download_image(url, filename_hint):
    """Downloads an image from a URL to the downloads directory"""
    if not os.path.exists('downloads'):
        os.makedirs('downloads')
        
    # Extract extension from URL, default to .jpg
    ext = os.path.splitext(urlparse(url).path)[1]
    if not ext:
        ext = ".jpg"
        
    filepath = os.path.join('downloads', f"{filename_hint}{ext}")
    
    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        return filepath
    except Exception as e:
        logger.error(f"Failed to download image from {url}: {e}")
        return None

def check_channel(channel: str):
    """Scrapes a single public Telegram channel for new posts"""
    clean_channel = channel.replace('@', '')
    url = f"https://t.me/s/{clean_channel}"
    
    # Pick a random User-Agent
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    
    try:
        logger.info(f"Scraping channel: {url}")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # Find all message widgets
        messages = soup.find_all('div', class_='tgme_widget_message')
        
        if not messages:
            logger.warning("No messages found on the channel page. Check the channel name or if it's private.")
            return
            
        # ── First-run guard: if DB is empty (fresh Render restart), only ──
        # process the LATEST 3 posts to avoid re-uploading old content.
        is_fresh_db = get_processed_count() == 0
        if is_fresh_db:
            logger.info("Fresh DB detected — only processing latest 3 posts to avoid duplicates.")
            # Mark all older posts as processed immediately
            for msg in messages[:-3]:
                old_id = msg.get('data-post')
                if old_id:
                    mark_post_processed(old_id)
            messages = messages[-3:]  # only process last 3

        for msg in messages:
            post_id = msg.get('data-post')
            
            # Skip if we already processed this post (persistent DB check)
            if not post_id or is_post_processed(post_id):
                continue
                
            # Check for photo
            photo_wrap = msg.find('a', class_='tgme_widget_message_photo_wrap')
            if not photo_wrap:
                # Mark as processed even if it has no photo so we don't check it again
                mark_post_processed(post_id)
                continue
                
            # Extract background-image URL
            style = photo_wrap.get('style', '')
            if "background-image:url(" in style:
                # Extract URL from string like: background-image:url('https://...')
                start = style.find("url('") + 5
                end = style.find("')", start)
                if start > 4 and end > start:
                    img_url = style[start:end]
                    
                    # Extract caption text if available
                    caption_div = msg.find('div', class_='tgme_widget_message_text')
                    caption = caption_div.text if caption_div else ""
                    
                    logger.info(f"Found new image in post {post_id}")
                    
                    # Download the image
                    safe_post_id = post_id.replace('/', '_')
                    filepath = download_image(img_url, f"post_{safe_post_id}")
                    
                    if filepath and _on_new_image_callback:
                        _on_new_image_callback(filepath, caption, clean_channel)

            # Mark as processed in persistent DB
            mark_post_processed(post_id)
            
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 429:
            logger.error("Rate limited by Telegram! Waiting longer before next attempt.")
        elif e.response.status_code == 404:
            logger.error(f"Channel not found: {url}")
        else:
            logger.error(f"HTTP Error checking channel: {e}")
    except Exception as e:
        logger.error(f"Error scraping channel: {str(e)}")

def start_listener():
    """Starts the synchronous polling loop across all configured channels."""
    if not TELEGRAM_CHANNELS:
        logger.error("No channels configured. Set TELEGRAM_CHANNELS in .env")
        return

    logger.info(f"Starting Telegram Web Scraper — watching {len(TELEGRAM_CHANNELS)} channel(s)...")
    for ch in TELEGRAM_CHANNELS:
        logger.info(f"  • {ch}")

    while True:
        for channel in TELEGRAM_CHANNELS:
            check_channel(channel)
            # Small pause between channels to avoid hammering Telegram
            if len(TELEGRAM_CHANNELS) > 1:
                time.sleep(random.randint(3, 8))

        # Jitter: Calculate delay in seconds (+/- 20% of base interval)
        base_seconds = SCRAPE_INTERVAL_MINUTES * 60
        jitter_range = int(base_seconds * 0.2)
        actual_delay = base_seconds + random.randint(-jitter_range, jitter_range)

        logger.info(f"All channels checked. Sleeping for {actual_delay / 60:.1f} min before next cycle (jitter applied)")
        time.sleep(actual_delay)
