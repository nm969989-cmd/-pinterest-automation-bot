"""
dreamstime_uploader.py
======================
Automatic Dreamstime Contributor uploader via FTP.
Uploads images directly to Dreamstime with AI-embedded EXIF metadata.

CREDENTIALS:
  Dreamstime Contributor: https://www.dreamstime.com/uploadfile
  FTP Server  : upload.dreamstime.com
  FTP Port    : 21
  FTP User    : Your Dreamstime Contributor ID or Username
  FTP Password: Your Dreamstime Password
"""

import os
import re
import json
import time
import base64
import ftplib
import requests
from PIL import Image
from logger import get_logger

logger = get_logger(__name__)

DREAMSTIME_FTP_HOST = os.getenv("DREAMSTIME_FTP_HOST", "upload.dreamstime.com")
DREAMSTIME_FTP_PORT = int(os.getenv("DREAMSTIME_FTP_PORT", 21))
DREAMSTIME_FTP_USER = os.getenv("DREAMSTIME_FTP_USER", "")
DREAMSTIME_FTP_PASS = os.getenv("DREAMSTIME_FTP_PASS", "")

OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY      = os.getenv("GEMINI_API_KEY", "")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_VISION_MODELS  = [
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

_DT_VISION_PROMPT = (
    "You are a stock photography expert creating metadata for Dreamstime.\n"
    "Caption hint: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<commercial stock title, max 80 chars, no brands>",\n'
    '  "description": "<detailed 2-sentence description, max 250 chars>",\n'
    '  "keywords": ["<25 to 40 relevant lowercase tags>"]\n'
    "}}"
)


def _parse_dt_ai_response(raw):
    try:
        clean = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        clean = re.sub(r'\n?```$', '', clean)
        data = json.loads(clean)
        title = data.get("title", "").strip()[:80]
        desc = data.get("description", "").strip()[:250]
        tags = [str(k).strip().lower() for k in data.get("keywords", []) if k][:40]
        if title and tags:
            return title, desc, tags
    except Exception:
        pass
    return None


def generate_dreamstime_metadata(image_path, caption=""):
    """Auto-generate metadata for Dreamstime."""
    if OPENROUTER_API_KEY and image_path and os.path.isfile(image_path):
        try:
            with open(image_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')
            mime = "image/jpeg" if not image_path.lower().endswith('.png') else "image/png"
            prompt = _DT_VISION_PROMPT.format(caption=caption or "digital artwork")
            headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
            for model in _VISION_MODELS:
                try:
                    payload = {
                        "model": model,
                        "messages": [{"role": "user", "content": [
                            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                            {"type": "text", "text": prompt}
                        ]}],
                        "max_tokens": 500
                    }
                    res = requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=25)
                    if res.status_code == 200:
                        parsed = _parse_dt_ai_response(res.json()['choices'][0]['message']['content'])
                        if parsed:
                            return parsed
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Dreamstime AI] Vision error: {e}")

    # Fallback
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    title = f"{base} illustration"[:80]
    desc = f"Commercial digital art asset: {base}. Perfect for posters, backgrounds, wallpaper and web design."
    keywords = ["digital art", "illustration", "anime style", "character", "artwork", "design", "creative", "wallpaper", "poster"]
    return title, desc, keywords


def upload_to_dreamstime(image_path, title=None, description=None, keywords=None, caption=""):
    """Upload image to Dreamstime via FTP."""
    if not DREAMSTIME_FTP_USER or not DREAMSTIME_FTP_PASS:
        logger.error("[Dreamstime FTP] Credentials missing. Set DREAMSTIME_FTP_USER & DREAMSTIME_FTP_PASS in .env")
        return False
    if not os.path.isfile(image_path):
        logger.error(f"[Dreamstime FTP] File not found: {image_path}")
        return False

    logger.info(f"[Dreamstime] Starting upload for: {os.path.basename(image_path)}")
    if not title or not keywords:
        title, description, keywords = generate_dreamstime_metadata(image_path, caption=caption)

    # Embed EXIF
    processed_path = image_path
    try:
        from freepik_uploader import embed_metadata_in_image
        temp_dir = os.path.join(os.path.dirname(image_path), "processed")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f"dt_{os.path.basename(image_path)}")
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
                ftp.connect(DREAMSTIME_FTP_HOST, DREAMSTIME_FTP_PORT, timeout=35)
                ftp.login(DREAMSTIME_FTP_USER, DREAMSTIME_FTP_PASS)
                logger.info(f"[Dreamstime FTP] Connected. Uploading {filename}...")
                with open(processed_path, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
            logger.info(f"[Dreamstime FTP] Successfully uploaded: {filename}")
            if processed_path != image_path and os.path.exists(processed_path):
                try: os.remove(processed_path)
                except Exception: pass
            return True
        except Exception as e:
            logger.warning(f"[Dreamstime FTP] Attempt {attempt} failed: {e}")

    if processed_path != image_path and os.path.exists(processed_path):
        try: os.remove(processed_path)
        except Exception: pass
    return False


if __name__ == "__main__":
    print("=== Dreamstime Uploader Module Initialized ===")
