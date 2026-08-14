import os
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from logger import get_logger
from config import TELEGRAM_CHANNEL, SCRAPE_INTERVAL_MINUTES

logger = get_logger(__name__)

# Callback function to handle new images
_on_new_image_callback = None

# Keep track of processed post IDs to avoid duplicates within a session
# (A database is better for persistence across restarts)
processed_post_ids = set()

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

def check_channel():
    """Scrapes the public Telegram channel for new posts"""
    clean_channel = TELEGRAM_CHANNEL.replace('@', '')
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
            
        for msg in messages:
            post_id = msg.get('data-post')
            
            # Skip if we already processed this post
            if not post_id or post_id in processed_post_ids:
                continue
                
            # Check for photo
            photo_wrap = msg.find('a', class_='tgme_widget_message_photo_wrap')
            if not photo_wrap:
                # Mark as processed even if it has no photo so we don't check it again
                processed_post_ids.add(post_id)
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
                        
            # Mark as processed
            processed_post_ids.add(post_id)
            
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
    """Starts the synchronous polling loop with Jitter"""
    if not TELEGRAM_CHANNEL:
        logger.error("TELEGRAM_CHANNEL not configured.")
        return
        
    logger.info("Starting Telegram Web Scraper listener...")
    
    while True:
        check_channel()
        
        # Jitter: Calculate delay in seconds (+/- 20% of base interval)
        base_seconds = SCRAPE_INTERVAL_MINUTES * 60
        jitter_range = int(base_seconds * 0.2)
        actual_delay = base_seconds + random.randint(-jitter_range, jitter_range)
        
        logger.info(f"Sleeping for {actual_delay / 60:.1f} minutes before next check (Jitter applied)")
        time.sleep(actual_delay)
