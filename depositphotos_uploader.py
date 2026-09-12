"""
depositphotos_uploader.py
=========================
Automatic Depositphotos Contributor uploader via FTP.
Uploads images directly to Depositphotos with embedded metadata.

CREDENTIALS:
  Depositphotos Contributor : https://depositphotos.com
  FTP Server  : ftp.depositphotos.com
  FTP Port    : 21
  FTP User    : Your Depositphotos username / Contributor ID
  FTP Password: Your Depositphotos FTP password
"""

import os
import re
import json
import time
import base64
import ftplib
import requests
from logger import get_logger

logger = get_logger(__name__)

DEPOSITPHOTOS_FTP_HOST = os.getenv("DEPOSITPHOTOS_FTP_HOST", "ftp.depositphotos.com")
DEPOSITPHOTOS_FTP_PORT = int(os.getenv("DEPOSITPHOTOS_FTP_PORT", 21))
DEPOSITPHOTOS_FTP_USER = os.getenv("DEPOSITPHOTOS_FTP_USER", "")
DEPOSITPHOTOS_FTP_PASS = os.getenv("DEPOSITPHOTOS_FTP_PASS", "")

OPENROUTER_API_KEY     = os.getenv("OPENROUTER_API_KEY", "")
_OPENROUTER_URL        = "https://openrouter.ai/api/v1/chat/completions"
_VISION_MODELS         = [
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
]


def generate_depositphotos_metadata(image_path, caption=""):
    """Auto-generate title and keywords for Depositphotos."""
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    title = f"{caption or base} digital artwork"[:80]
    desc = f"Commercial high-resolution digital artwork: {base}. Ideal for web, posters, and creative designs."
    keywords = ["digital art", "illustration", "anime style", "character", "artwork", "graphic design", "wallpaper", "poster", "concept art"]
    return title, desc, keywords


def upload_to_depositphotos(image_path, title=None, description=None, keywords=None, caption=""):
    """Upload image to Depositphotos via FTP."""
    if not DEPOSITPHOTOS_FTP_USER or not DEPOSITPHOTOS_FTP_PASS:
        logger.error("[Depositphotos FTP] Credentials missing. Set DEPOSITPHOTOS_FTP_USER & DEPOSITPHOTOS_FTP_PASS in .env")
        return False
    if not os.path.isfile(image_path):
        logger.error(f"[Depositphotos FTP] File not found: {image_path}")
        return False

    logger.info(f"[Depositphotos] Starting upload for: {os.path.basename(image_path)}")
    if not title or not keywords:
        title, description, keywords = generate_depositphotos_metadata(image_path, caption=caption)

    # Embed EXIF
    processed_path = image_path
    try:
        from freepik_uploader import embed_metadata_in_image
        temp_dir = os.path.join(os.path.dirname(image_path), "processed")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f"dp_{os.path.basename(image_path)}")
        processed_path = embed_metadata_in_image(image_path, title, description, keywords, temp_file)
    except Exception:
        processed_path = image_path

    filename = os.path.basename(image_path)
    if not filename.lower().endswith(('.jpg', '.jpeg')):
        filename = f"{os.path.splitext(filename)[0]}.jpg"

    delays = [0, 5, 15]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            with ftplib.FTP() as ftp:
                ftp.connect(DEPOSITPHOTOS_FTP_HOST, DEPOSITPHOTOS_FTP_PORT, timeout=35)
                ftp.login(DEPOSITPHOTOS_FTP_USER, DEPOSITPHOTOS_FTP_PASS)
                logger.info(f"[Depositphotos FTP] Connected. Uploading {filename}...")
                with open(processed_path, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
            logger.info(f"[Depositphotos FTP] Successfully uploaded: {filename}")
            if processed_path != image_path and os.path.exists(processed_path):
                try: os.remove(processed_path)
                except Exception: pass
            return True
        except Exception as e:
            logger.warning(f"[Depositphotos FTP] Attempt {attempt} failed: {e}")

    if processed_path != image_path and os.path.exists(processed_path):
        try: os.remove(processed_path)
        except Exception: pass
    return False


if __name__ == "__main__":
    print("=== Depositphotos Uploader Module Initialized ===")
