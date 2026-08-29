"""
image_host.py - Uploads a local image to a free hosting service and returns a public HTTPS URL.

Used by the Make.com webhook flow, which requires images to be publicly accessible via URL.

Upload chain (auto-fallback):
  1. Catbox.moe     — no API key, permanent, fast (PRIMARY)
  2. 0x0.st         — no API key, expires ~1 year (FALLBACK 1)
  3. Cloudinary     — requires API key, permanent CDN (FALLBACK 2 — most reliable)

To enable Cloudinary, add to .env / Render environment:
  CLOUDINARY_CLOUD_NAME = your_cloud_name
  CLOUDINARY_API_KEY    = your_api_key
  CLOUDINARY_API_SECRET = your_api_secret
"""

import os
import requests
from logger import get_logger

logger = get_logger(__name__)

_CATBOX_URL      = "https://catbox.moe/user/api.php"
_NULLPOINTER_URL = "https://0x0.st"


def _upload_to_cloudinary(image_path: str) -> str | None:
    """Upload to Cloudinary if credentials are set. Returns public URL or None."""
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME", "")
    api_key    = os.getenv("CLOUDINARY_API_KEY", "")
    api_secret = os.getenv("CLOUDINARY_API_SECRET", "")

    if not (cloud_name and api_key and api_secret):
        return None  # Not configured — skip silently

    try:
        import hashlib, time
        timestamp = str(int(time.time()))
        params    = {"folder": "pinterest-bot", "timestamp": timestamp}

        # Cloudinary requires all params sorted alphabetically + api_secret appended
        sig_str   = "&".join(f"{k}={v}" for k, v in sorted(params.items())) + api_secret
        signature = hashlib.sha1(sig_str.encode()).hexdigest()

        upload_url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"

        with open(image_path, "rb") as f:
            res = requests.post(
                upload_url,
                data={
                    "api_key":   api_key,
                    "timestamp": timestamp,
                    "signature": signature,
                    "folder":    "pinterest-bot",
                },
                files={"file": f},
                timeout=30,
            )

        if res.status_code == 200:
            url = res.json().get("secure_url", "")
            if url:
                logger.info(f"[ImageHost] Cloudinary upload success: {url}")
                return url
            logger.warning("[ImageHost] Cloudinary response missing secure_url")
        else:
            logger.warning(
                f"[ImageHost] Cloudinary returned {res.status_code}: {res.text[:200]}"
            )
    except Exception as e:
        logger.warning(f"[ImageHost] Cloudinary upload failed: {e}")
    return None


def upload_image_to_host(image_path: str) -> str | None:
    """
    Upload a local image file to a free public host.
    Returns the public HTTPS URL string, or None if all attempts fail.

    Chain: Catbox → 0x0.st → Cloudinary (if configured)
    """
    # ── 1. Primary: Catbox.moe ───────────────────────────────────────────────
    try:
        logger.info(f"[ImageHost] Uploading to Catbox: {image_path}")
        with open(image_path, "rb") as f:
            res = requests.post(
                _CATBOX_URL,
                data={"reqtype": "fileupload"},
                files={"fileToUpload": f},
                timeout=20,
            )
        if res.status_code == 200 and res.text.startswith("https://"):
            url = res.text.strip()
            logger.info(f"[ImageHost] Catbox upload success: {url}")
            return url
        else:
            logger.warning(
                f"[ImageHost] Catbox returned unexpected: {res.status_code} {res.text[:100]}"
            )
    except Exception as e:
        logger.warning(f"[ImageHost] Catbox upload failed: {e}")

    # ── 2. Fallback: 0x0.st ─────────────────────────────────────────────────
    try:
        logger.info(f"[ImageHost] Trying 0x0.st: {image_path}")
        with open(image_path, "rb") as f:
            res = requests.post(
                _NULLPOINTER_URL,
                files={"file": f},
                timeout=20,
            )
        if res.status_code == 200 and res.text.strip().startswith("https://"):
            url = res.text.strip()
            logger.info(f"[ImageHost] 0x0.st upload success: {url}")
            return url
        else:
            logger.warning(
                f"[ImageHost] 0x0.st returned: {res.status_code} {res.text[:100]}"
            )
    except Exception as e:
        logger.warning(f"[ImageHost] 0x0.st upload failed: {e}")

    # ── 3. Final backup: Cloudinary (only if API key configured) ────────────
    url = _upload_to_cloudinary(image_path)
    if url:
        return url

    logger.error("[ImageHost] All upload hosts failed. Cannot get public image URL.")
    return None
