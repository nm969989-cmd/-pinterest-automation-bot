"""
Telegram Channel Scraper + Backlog Mode
=======================================
1. Checks channel for NEW posts every 10 min
2. If new posts found → send to smart scheduler as PRIORITY (new > backlog)
3. If NO new posts found → enter BACKLOG MODE:
   - Fetch older posts via ?before=OLDEST_ID pagination
   - Double-check: skip if already posted (by post_id AND image_url)
   - Add unposted ones to backlog queue (max 100 old posts)
"""

import os
import time
import random
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from logger import get_logger
from config import TELEGRAM_CHANNELS, SCRAPE_INTERVAL_MINUTES
from database import (is_post_processed, mark_post_processed,
                      get_processed_count, get_oldest_seen_post_numeric_id,
                      is_image_url_uploaded)

logger = get_logger(__name__)

# Callback: fires for NEW images only
_on_new_image_callback = None

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
]

BACKLOG_MAX_POSTS = 100  # How many old posts to scan for backlog


def set_image_callback(callback):
    global _on_new_image_callback
    _on_new_image_callback = callback


def _make_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }


def download_image(url: str, filename_hint: str) -> str | None:
    """Downloads an image from a URL to the downloads directory."""
    os.makedirs("downloads", exist_ok=True)
    ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
    filepath = os.path.join("downloads", f"{filename_hint}{ext}")
    try:
        response = requests.get(url, stream=True, timeout=10)
        response.raise_for_status()
        with open(filepath, "wb") as f:
            for chunk in response.iter_content(1024):
                f.write(chunk)
        return filepath
    except Exception as e:
        logger.error(f"Failed to download image from {url}: {e}")
        return None


def _extract_image_url(msg) -> str | None:
    """Extract background-image URL from a tgme_widget_message div."""
    photo_wrap = msg.find("a", class_="tgme_widget_message_photo_wrap")
    if not photo_wrap:
        return None
    style = photo_wrap.get("style", "")
    if "background-image:url(" not in style:
        return None
    start = style.find("url('") + 5
    end = style.find("')", start)
    if start > 4 and end > start:
        return style[start:end]
    return None


def _scrape_url(url: str) -> list:
    """Fetch and parse a t.me/s page, returning message divs."""
    try:
        response = requests.get(url, headers=_make_headers(), timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        return soup.find_all("div", class_="tgme_widget_message")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        if code == 429:
            logger.error("Rate limited by Telegram. Backing off.")
        elif code == 404:
            logger.error(f"Channel not found: {url}")
        else:
            logger.error(f"HTTP {code} scraping {url}: {e}")
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
    return []


def check_channel(channel: str) -> int:
    """
    Scrapes a channel for NEW posts.
    Returns number of new images found.
    """
    clean_channel = channel.replace("@", "")
    url = f"https://t.me/s/{clean_channel}"

    logger.info(f"Scraping channel: {url}")
    messages = _scrape_url(url)

    if not messages:
        logger.warning("No messages found. Channel may be private or renamed.")
        return 0

    # ── First-run guard ────────────────────────────────────────────────────────
    if get_processed_count() == 0:
        skipped = sum(
            1 for msg in messages
            if msg.get("data-post") and (mark_post_processed(msg.get("data-post")) or True)
        )
        logger.info(
            f"Fresh DB — marked {skipped} existing posts as seen. "
            f"Only truly NEW posts will be uploaded going forward."
        )
        return 0

    new_found = 0
    for msg in messages:
        post_id = msg.get("data-post")
        if not post_id or is_post_processed(post_id):
            continue

        img_url = _extract_image_url(msg)
        if not img_url:
            mark_post_processed(post_id)
            continue

        # Double-check: skip if image URL already posted
        if is_image_url_uploaded(img_url):
            logger.info(f"[New] Image already posted (URL match), skipping: {post_id}")
            mark_post_processed(post_id)
            continue

        caption_div = msg.find("div", class_="tgme_widget_message_text")
        caption = caption_div.text if caption_div else ""
        logger.info(f"Found new image in post {post_id}")

        safe_post_id = post_id.replace("/", "_")
        filepath = download_image(img_url, f"post_{safe_post_id}")

        if filepath and _on_new_image_callback:
            _on_new_image_callback(filepath, caption, clean_channel)
            new_found += 1

        mark_post_processed(post_id)

    return new_found


def _backlog_scrape(channel: str, max_posts: int = BACKLOG_MAX_POSTS):
    """
    When no new posts exist, fetch OLDER posts via ?before=ID pagination.
    Adds unposted ones to the backlog queue (priority=0).
    Scans at most `max_posts` older posts.
    """
    from image_processor import process_image
    from amazon_search import generate_amazon_link
    from ai_caption import generate_pin_content
    from scheduler import scheduler
    import config

    clean_channel = channel.replace("@", "")
    oldest_id = get_oldest_seen_post_numeric_id()

    if oldest_id <= 1:
        logger.info("[Backlog] No pagination anchor found, skipping backlog scan.")
        return

    logger.info(f"[Backlog] Scanning older posts before ID {oldest_id} for {clean_channel}...")
    backlog_added = 0
    current_before = oldest_id

    while backlog_added < max_posts:
        url = f"https://t.me/s/{clean_channel}?before={current_before}"
        messages = _scrape_url(url)

        if not messages:
            logger.info("[Backlog] No more older posts found.")
            break

        for msg in messages:
            post_id = msg.get("data-post")
            if not post_id:
                continue

            # Already seen?
            if is_post_processed(post_id):
                continue

            img_url = _extract_image_url(msg)
            if not img_url:
                mark_post_processed(post_id)
                continue

            # Double-check: already uploaded?
            if is_image_url_uploaded(img_url):
                mark_post_processed(post_id)
                continue

            # Found an unposted old image — process it
            caption_div = msg.find("div", class_="tgme_widget_message_text")
            caption = caption_div.text if caption_div else ""

            safe_post_id = post_id.replace("/", "_")
            filepath = download_image(img_url, f"post_{safe_post_id}")
            if not filepath:
                mark_post_processed(post_id)
                continue

            # Generate content
            processed_path = process_image(filepath, clean_channel)
            anime_name, title, desc_template = generate_pin_content(
                caption, clean_channel,
                image_path=processed_path,
                api_key=config.GEMINI_API_KEY,
                openrouter_key=config.OPENROUTER_API_KEY
            )
            amazon_link = generate_amazon_link(anime_name)
            description = desc_template.replace("##LINK_PLACEHOLDER##", amazon_link)

            # Add to BACKLOG queue (priority=0)
            added = scheduler.enqueue_backlog_image(
                post_id=safe_post_id,
                image_path=processed_path,
                title=title,
                description=description,
                link=amazon_link,
                anime_name=anime_name,
                image_url=img_url,
            )
            if added:
                backlog_added += 1
                logger.info(f"[Backlog] Added old post to queue: '{title}' ({backlog_added}/{max_posts})")

            mark_post_processed(post_id)

            if backlog_added >= max_posts:
                break

        # Paginate further back
        ids_on_page = []
        for msg in messages:
            pid = msg.get("data-post", "")
            try:
                ids_on_page.append(int(pid.split("/")[-1]))
            except Exception:
                pass

        if ids_on_page:
            current_before = min(ids_on_page)
        else:
            break

        time.sleep(random.uniform(3, 6))  # Be polite to Telegram

    logger.info(f"[Backlog] Scan complete. Added {backlog_added} old posts to queue.")


def start_listener():
    """Starts the scraping loop. Runs backlog mode when no new posts found."""
    if not TELEGRAM_CHANNELS:
        logger.error("No channels configured. Set TELEGRAM_CHANNELS in .env")
        return

    logger.info(f"Starting Telegram Web Scraper — watching {len(TELEGRAM_CHANNELS)} channel(s)...")
    for ch in TELEGRAM_CHANNELS:
        logger.info(f"  • {ch}")

    while True:
        total_new = 0
        for channel in TELEGRAM_CHANNELS:
            new = check_channel(channel)
            total_new += new
            if len(TELEGRAM_CHANNELS) > 1:
                time.sleep(random.randint(3, 8))

        # If no new images found → check backlog
        if total_new == 0:
            from database import get_queue_counts
            counts = get_queue_counts()
            # Only fill backlog if queue is running low (< 3 backlog items)
            if counts["backlog"] < 3:
                logger.info(
                    f"[Backlog] No new posts and only {counts['backlog']} backlog items. "
                    f"Scanning for old posts..."
                )
                for channel in TELEGRAM_CHANNELS:
                    _backlog_scrape(channel, max_posts=BACKLOG_MAX_POSTS)
            else:
                logger.info(
                    f"[Backlog] No new posts. Backlog queue has {counts['backlog']} items — OK."
                )

        # Jitter sleep
        base_seconds = SCRAPE_INTERVAL_MINUTES * 60
        jitter = int(base_seconds * 0.2)
        actual_delay = base_seconds + random.randint(-jitter, jitter)
        logger.info(f"All channels checked. Sleeping {actual_delay/60:.1f} min before next cycle.")
        time.sleep(actual_delay)
