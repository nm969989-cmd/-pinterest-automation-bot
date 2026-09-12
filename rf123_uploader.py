"""
rf123_uploader.py
=================
Automatic 123RF Contributor uploader via FTP.
Uploads images directly to 123RF with embedded metadata.

CREDENTIALS:
  123RF Contributor: https://www.123rf.com/contributors/
  FTP Server  : ftp.123rf.com
  FTP Port    : 21
  FTP User    : Your 123RF Contributor ID / Username
  FTP Password: Your 123RF FTP Password
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

RF123_FTP_HOST = os.getenv("RF123_FTP_HOST", "ftp.123rf.com")
RF123_FTP_PORT = int(os.getenv("RF123_FTP_PORT", 21))
RF123_FTP_USER = os.getenv("RF123_FTP_USER", "")
RF123_FTP_PASS = os.getenv("RF123_FTP_PASS", "")


def generate_123rf_metadata(image_path, caption=""):
    """Generate title, description, and keywords for 123RF."""
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    title = f"{caption or base} digital art"[:80]
    desc = f"Commercial digital art illustration: {base}. Ideal for web banners, posters, and wallpaper."
    keywords = ["digital art", "illustration", "anime style", "character", "artwork", "graphic design", "wallpaper", "poster"]
    return title, desc, keywords


def upload_to_123rf(image_path, title=None, description=None, keywords=None, caption=""):
    """Upload image to 123RF via FTP."""
    if not RF123_FTP_USER or not RF123_FTP_PASS:
        logger.error("[123RF FTP] Credentials missing. Set RF123_FTP_USER & RF123_FTP_PASS in .env")
        return False
    if not os.path.isfile(image_path):
        logger.error(f"[123RF FTP] File not found: {image_path}")
        return False

    logger.info(f"[123RF] Starting upload for: {os.path.basename(image_path)}")
    if not title or not keywords:
        title, description, keywords = generate_123rf_metadata(image_path, caption=caption)

    # Embed EXIF
    processed_path = image_path
    try:
        from freepik_uploader import embed_metadata_in_image
        temp_dir = os.path.join(os.path.dirname(image_path), "processed")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f"123rf_{os.path.basename(image_path)}")
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
                ftp.connect(RF123_FTP_HOST, RF123_FTP_PORT, timeout=35)
                ftp.login(RF123_FTP_USER, RF123_FTP_PASS)
                logger.info(f"[123RF FTP] Connected. Uploading {filename}...")
                with open(processed_path, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
            logger.info(f"[123RF FTP] Successfully uploaded: {filename}")
            if processed_path != image_path and os.path.exists(processed_path):
                try: os.remove(processed_path)
                except Exception: pass
            return True
        except Exception as e:
            logger.warning(f"[123RF FTP] Attempt {attempt} failed: {e}")

    if processed_path != image_path and os.path.exists(processed_path):
        try: os.remove(processed_path)
        except Exception: pass
    return False


if __name__ == "__main__":
    print("=== 123RF Uploader Module Initialized ===")
