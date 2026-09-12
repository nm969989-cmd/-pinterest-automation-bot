"""
freepik_uploader.py
===================
Fully automatic Freepik Contributor uploader.
Uploads images directly to Freepik via FTP with AI-generated metadata.

HOW FREEPIK CONTRIBUTOR WORKS:
  - Freepik Contributor DOES NOT have an upload API key.
  - All automated uploads are handled via FTP (Host: ftp.contributor.freepik.com).
  - When you upload images via FTP, Freepik automatically scans IPTC / EXIF
    metadata (Title, Description, Keywords) embedded inside the JPEG.
  - This module embeds the AI metadata into the image before uploading,
    so your photos arrive on Freepik with title and tags pre-filled!

HOW TO GET CREDENTIALS:
  1. Go to https://contributor.freepik.com and sign in.
  2. Click "Upload" in the left menu.
  3. Click the "FTP" tab.
  4. Copy your Username and FTP Password into .env:
     FREEPIK_FTP_USER=your_username
     FREEPIK_FTP_PASS=your_ftp_password
"""

import os
import re
import json
import time
import base64
import ftplib
import requests
from PIL import Image, ExifTags
from logger import get_logger

logger = get_logger(__name__)

# Freepik FTP credentials from .env
FREEPIK_FTP_HOST = os.getenv("FREEPIK_FTP_HOST", "ftp.contributor.freepik.com")
FREEPIK_FTP_PORT = int(os.getenv("FREEPIK_FTP_PORT", 21))
FREEPIK_FTP_USER = os.getenv("FREEPIK_FTP_USER", "")
FREEPIK_FTP_PASS = os.getenv("FREEPIK_FTP_PASS", "")

# AI keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

_OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
_VISION_MODELS   = [
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

_FREEPIK_VISION_PROMPT = (
    "You are a Freepik Contributor SEO specialist.\n"
    "Inspect this image to submit it for sale on Freepik.\n"
    "Caption hint: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<clean, descriptive commercial title, max 100 chars, no brands, no emojis>",\n'
    '  "description": "<detailed 2-sentence description of the visual elements and concept, max 250 chars>",\n'
    '  "keywords": ["<25 to 40 relevant lowercase keywords, ordered from most relevant to least>"]\n'
    "}}\n\n"
    "Rules for Freepik:\n"
    "- Professional stock asset vocabulary\n"
    "- Include style, subject, colors, theme, usage concepts (background, wallpaper, print, banner)\n"
    "- NO trademarked or copyrighted character names (use generic terms: anime boy, warrior, fantasy character)"
)

_FREEPIK_TEXT_PROMPT = (
    "You are a Freepik Contributor SEO specialist.\n"
    "Generate metadata for a stock asset submission on Freepik based on this caption.\n"
    "Caption: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<clean descriptive stock title, max 100 chars>",\n'
    '  "description": "<2-sentence visual description, max 250 chars>",\n'
    '  "keywords": ["<25 to 40 relevant lowercase tags>"]\n'
    "}}"
)


def _parse_freepik_ai_response(raw):
    """Parse AI JSON response into (title, description, keywords)."""
    try:
        clean = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        clean = re.sub(r'\n?```$', '', clean)
        data = json.loads(clean)
        title = data.get("title", "").strip()[:100]
        description = data.get("description", "").strip()[:250]
        keywords = data.get("keywords", [])
        keywords = [str(k).strip().lower() for k in keywords if k][:45]
        if title and keywords:
            logger.info(f"[Freepik AI] Title: '{title}' | Keywords: {len(keywords)} tags")
            return title, description, keywords
    except Exception as e:
        logger.warning(f"[Freepik AI] Failed to parse AI response: {e}")
    return None


def generate_freepik_metadata(image_path, caption=""):
    """
    Auto-generates Freepik Contributor title, description, and keywords.
    Uses OpenRouter Vision -> Gemini Text -> Offline Fallback.
    """
    logger.info("[Freepik AI] Generating Freepik metadata...")

    # Vision AI
    if OPENROUTER_API_KEY and image_path and os.path.isfile(image_path):
        try:
            with open(image_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')
            mime = "image/jpeg"
            if image_path.lower().endswith('.png'):
                mime = "image/png"
            prompt = _FREEPIK_VISION_PROMPT.format(caption=caption or "(digital artwork)")
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            }
            for model in _VISION_MODELS:
                try:
                    payload = {
                        "model": model,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                                {"type": "text", "text": prompt}
                            ]
                        }],
                        "max_tokens": 500
                    }
                    res = requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=25)
                    if res.status_code == 200:
                        raw = res.json()['choices'][0]['message']['content']
                        parsed = _parse_freepik_ai_response(raw)
                        if parsed:
                            return parsed
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Freepik AI Vision] Error: {e}")

    # Gemini text fallback
    if GEMINI_API_KEY:
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=GEMINI_API_KEY)
            prompt = _FREEPIK_TEXT_PROMPT.format(caption=caption or "anime digital art illustration")
            resp = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            parsed = _parse_freepik_ai_response(resp.text)
            if parsed:
                return parsed
        except Exception as e:
            logger.warning(f"[Freepik AI Gemini] Error: {e}")

    # Offline fallback
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    hint = (caption or base or "digital artwork").split('\n')[0][:80]
    clean = re.sub(r'[^\w\s]', '', hint).strip()
    title = f"{clean} digital illustration" if clean else "Anime digital art illustration"
    description = f"High quality digital asset: {clean}. Suitable for wallpapers, prints, banners, and digital designs."
    keywords = [
        "digital art", "illustration", "anime style", "character", "artwork",
        "graphic design", "vibrant", "creative", "fantasy", "wallpaper", "poster",
        "banner", "creative asset", "drawing", "modern illustration", "concept art"
    ]
    return title, description, keywords


def embed_metadata_in_image(image_path, title, description, keywords, output_path=None):
    """
    Embeds metadata into EXIF/JPEG so stock platforms like Freepik & Adobe
    automatically index title and keywords on FTP upload.
    """
    try:
        if not output_path:
            output_path = image_path

        with Image.open(image_path) as img:
            # Prepare EXIF data
            exif = img.getexif()
            # 0x010E is ImageDescription
            exif[0x010E] = f"{title}. {description}. Keywords: {', '.join(keywords)}"
            # 0x9C9B is XPTitle (Windows/Photoshop standard, UTF-16LE)
            exif[0x9C9B] = title.encode('utf-16le')
            # 0x9C9E is XPKeywords (UTF-16LE)
            exif[0x9C9E] = ";".join(keywords).encode('utf-16le')
            # 0x9C9C is XPComment
            exif[0x9C9C] = description.encode('utf-16le')

            # Save as JPEG with embedded EXIF
            if img.format != "JPEG" and output_path.lower().endswith(('.jpg', '.jpeg')):
                img = img.convert("RGB")
            img.save(output_path, quality=95, exif=exif)
            logger.info(f"[Freepik Metadata] Embedded EXIF metadata into {os.path.basename(output_path)}")
            return output_path
    except Exception as e:
        logger.warning(f"[Freepik Metadata] Could not embed EXIF: {e}. Uploading raw file.")
        return image_path


def upload_to_freepik(
    image_path,
    title=None,
    description=None,
    keywords=None,
    caption=""
):
    """
    Master Freepik Contributor upload function.
    Auto-generates metadata, embeds EXIF for auto-tagging, and uploads via FTP.
    """
    if not FREEPIK_FTP_USER or not FREEPIK_FTP_PASS:
        logger.error("[Freepik FTP] Credentials missing. Set FREEPIK_FTP_USER & FREEPIK_FTP_PASS in .env")
        return False

    if not os.path.isfile(image_path):
        logger.error(f"[Freepik FTP] File not found: {image_path}")
        return False

    logger.info(f"[Freepik Contributor] Starting upload for: {os.path.basename(image_path)}")

    # 1. Generate metadata if missing
    if not title or not keywords:
        ai_title, ai_desc, ai_keywords = generate_freepik_metadata(image_path, caption=caption)
        title = title or ai_title
        description = description or ai_desc
        keywords = keywords or ai_keywords

    # 2. Embed metadata into image so Freepik auto-populates title and tags
    processed_path = image_path
    try:
        temp_dir = os.path.join(os.path.dirname(image_path), "processed")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, f"freepik_{os.path.basename(image_path)}")
        processed_path = embed_metadata_in_image(image_path, title, description, keywords, temp_file)
    except Exception as e:
        logger.warning(f"[Freepik] Metadata embed fallback: {e}")
        processed_path = image_path

    filename = os.path.basename(image_path)
    # Ensure extension is .jpg
    if not filename.lower().endswith(('.jpg', '.jpeg')):
        filename = f"{os.path.splitext(filename)[0]}.jpg"

    # 3. Upload via FTP
    logger.info(f"[Freepik FTP] Connecting to {FREEPIK_FTP_HOST}:{FREEPIK_FTP_PORT}...")
    delays = [0, 5, 15]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            with ftplib.FTP() as ftp:
                ftp.connect(FREEPIK_FTP_HOST, FREEPIK_FTP_PORT, timeout=35)
                ftp.login(FREEPIK_FTP_USER, FREEPIK_FTP_PASS)
                logger.info(f"[Freepik FTP] Connected as '{FREEPIK_FTP_USER}'. Uploading {filename}...")
                with open(processed_path, "rb") as f:
                    ftp.storbinary(f"STOR {filename}", f)
            logger.info(f"[Freepik FTP] Successfully uploaded: {filename}")

            # Clean up temp file
            if processed_path != image_path and os.path.exists(processed_path):
                try:
                    os.remove(processed_path)
                except Exception:
                    pass

            return True
        except Exception as e:
            logger.warning(f"[Freepik FTP] Attempt {attempt} failed: {e}")

    # Clean up temp file on failure
    if processed_path != image_path and os.path.exists(processed_path):
        try:
            os.remove(processed_path)
        except Exception:
            pass

    logger.error(f"[Freepik FTP] All upload attempts failed for {filename}")
    return False


if __name__ == "__main__":
    print("=== Freepik Contributor Uploader Module Initialized ===")
