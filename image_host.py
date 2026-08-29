"""
image_host.py - Uploads a local image to a free hosting service and returns a public HTTPS URL.

Used by the Make.com webhook flow, which requires images to be publicly accessible via URL.
Uses Catbox.moe (no API key needed, free, permanent URLs, supports up to 200MB).
Falls back to 0x0.st if Catbox fails.
"""

import requests
from logger import get_logger

logger = get_logger(__name__)

_CATBOX_URL = "https://catbox.moe/user/api.php"
_NULLPOINTER_URL = "https://0x0.st"


def upload_image_to_host(image_path: str) -> str | None:
    """
    Upload a local image file to a free public host.
    Returns the public HTTPS URL string, or None if all attempts fail.

    Primary host  : catbox.moe  (permanent, no account needed)
    Fallback host : 0x0.st      (auto-expires after ~1 year, no account needed)
    """
    # ── Primary: Catbox.moe ───────────────────────────────────────────────────
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
            logger.warning(f"[ImageHost] Catbox returned unexpected: {res.status_code} {res.text[:100]}")
    except Exception as e:
        logger.warning(f"[ImageHost] Catbox upload failed: {e}")

    # ── Fallback: 0x0.st ─────────────────────────────────────────────────────
    try:
        logger.info(f"[ImageHost] Trying fallback host 0x0.st: {image_path}")
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
            logger.warning(f"[ImageHost] 0x0.st returned: {res.status_code} {res.text[:100]}")
    except Exception as e:
        logger.warning(f"[ImageHost] 0x0.st upload failed: {e}")

    logger.error("[ImageHost] All upload hosts failed. Cannot get public image URL.")
    return None
