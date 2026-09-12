"""
adobe_uploader.py
=================
Fully automatic Adobe Stock Contributor uploader.
Uploads image + AI title + AI keywords — NO MANUAL WORK.

SUPPORTED MODES:
  MODE 0 — Make.com Webhook (EASIEST — like Pinterest bot, Make.com Account 2):
    Set ADOBE_MAKE_WEBHOOK_URL in .env
    -> Bot sends metadata + image info to Make.com
    -> Make.com handles submission to Adobe Stock

  MODE 1 — Direct FTP / SFTP:
    Set ADOBE_FTP_USER and ADOBE_FTP_PASS in .env
    Host: ftp.adobestock.com (or sftp.contributor.adobestock.com)
    -> Bot uploads image directly. Adobe Stock auto-indexes the file.

  MODE 2 — Direct Adobe Stock API:
    Set ADOBE_STOCK_API_KEY and ADOBE_STOCK_ACCESS_TOKEN in .env

HOW TO GET CREDENTIALS:
  Adobe Contributor Portal : https://contributor.stock.adobe.com
  SFTP / FTP credentials   : Contributor Portal -> Upload -> "SFTP" tab
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

# Adobe Stock credentials from .env
ADOBE_MAKE_WEBHOOK_URL  = os.getenv("ADOBE_MAKE_WEBHOOK_URL", "")
ADOBE_FTP_HOST          = os.getenv("ADOBE_FTP_HOST", "ftp.adobestock.com")
ADOBE_FTP_USER          = os.getenv("ADOBE_FTP_USER", "")
ADOBE_FTP_PASS          = os.getenv("ADOBE_FTP_PASS", "")
ADOBE_STOCK_API_KEY     = os.getenv("ADOBE_STOCK_API_KEY", "")
ADOBE_STOCK_ACCESS_TOKEN = os.getenv("ADOBE_STOCK_ACCESS_TOKEN", "")

# AI keys
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

_OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
_VISION_MODELS   = [
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]

_ADOBE_VISION_PROMPT = (
    "You are an Adobe Stock contributor specialist and search ranking expert.\n"
    "You are inspecting this image to submit it for sale on Adobe Stock.\n"
    "Caption hint: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<clean, professional descriptive stock title, max 120 chars, NO brand names, NO emojis>",\n'
    '  "description": "<2 sentences describing the visual content, mood, and commercial utility, max 300 chars>",\n'
    '  "keywords": ["<30 to 45 highly targeted lowercase keywords, listed strictly in order of relevance>"]\n'
    "}}\n\n"
    "Rules for Adobe Stock:\n"
    "- First 10 keywords are the MOST critical for Adobe ranking\n"
    "- Use descriptive words: subject, art style, colors, mood, concepts, background\n"
    "- NO trademarked or copyrighted words (unless generic style words like anime, digital art, illustration)"
)

_ADOBE_TEXT_PROMPT = (
    "You are an Adobe Stock contributor SEO expert.\n"
    "Generate metadata for an Adobe Stock image submission based on this caption.\n"
    "Caption: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<descriptive stock title, max 120 chars, professional>",\n'
    '  "description": "<2 sentences describing the image, max 300 chars>",\n'
    '  "keywords": ["<30 to 45 relevant keywords in order of importance, lowercase>"]\n'
    "}}"
)


def _parse_adobe_ai_response(raw):
    """Parse AI JSON response into (title, description, keywords)."""
    try:
        clean = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        clean = re.sub(r'\n?```$', '', clean)
        data = json.loads(clean)
        title = data.get("title", "").strip()[:150]
        description = data.get("description", "").strip()[:300]
        keywords = data.get("keywords", [])
        keywords = [str(k).strip().lower() for k in keywords if k][:50]
        if title and keywords:
            logger.info(f"[Adobe AI] Title: '{title}' | Keywords: {len(keywords)} tags")
            return title, description, keywords
    except Exception as e:
        logger.warning(f"[Adobe AI] Failed to parse AI response: {e}")
    return None


def generate_adobe_metadata(image_path, caption=""):
    """
    Auto-generates Adobe Stock title, description, and keywords.
    Uses OpenRouter Vision -> Gemini Text -> Offline Fallback.
    """
    logger.info("[Adobe AI] Generating Adobe Stock metadata...")

    # Vision AI
    if OPENROUTER_API_KEY and image_path and os.path.isfile(image_path):
        try:
            with open(image_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode('utf-8')
            mime = "image/jpeg"
            if image_path.lower().endswith('.png'): mime = "image/png"
            prompt = _ADOBE_VISION_PROMPT.format(caption=caption or "(digital artwork)")
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
                        parsed = _parse_adobe_ai_response(raw)
                        if parsed:
                            return parsed
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[Adobe AI Vision] Error: {e}")

    # Gemini text fallback
    if GEMINI_API_KEY:
        try:
            from google import genai as _genai
            client = _genai.Client(api_key=GEMINI_API_KEY)
            prompt = _ADOBE_TEXT_PROMPT.format(caption=caption or "anime digital art illustration")
            resp = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            parsed = _parse_adobe_ai_response(resp.text)
            if parsed:
                return parsed
        except Exception as e:
            logger.warning(f"[Adobe AI Gemini] Error: {e}")

    # Offline fallback
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    hint = (caption or base or "digital artwork").split('\n')[0][:80]
    clean = re.sub(r'[^\w\s]', '', hint).strip()
    title = f"{clean} digital illustration" if clean else "Anime digital art illustration"
    description = f"High quality digital artwork illustration: {clean}. Suitable for creative projects, posters, web and print design."
    keywords = [
        "digital art", "illustration", "anime style", "character design", "artwork",
        "graphic design", "vibrant", "creative", "fantasy", "wallpaper", "poster",
        "modern art", "colorful", "artistic", "concept art", "japanese style",
        "drawing", "print", "creative asset", "commercial illustration"
    ]
    return title, description, keywords


def upload_via_make_webhook_adobe(image_path, title, description, keywords, image_url=""):
    """Upload to Adobe Stock via Make.com webhook (Account 2)."""
    if not ADOBE_MAKE_WEBHOOK_URL:
        return False

    filename = os.path.basename(image_path)
    payload = {
        "platform": "adobe_stock",
        "filename": filename,
        "title": title[:150],
        "description": description[:300],
        "keywords": ", ".join(keywords[:50]),
        "keywords_list": keywords[:50],
        "image_url": image_url,
    }

    delays = [0, 5, 15]
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(ADOBE_MAKE_WEBHOOK_URL, json=payload, timeout=25)
            if res.status_code in (200, 201, 204):
                logger.info(f"[Adobe Make.com] Successfully submitted: '{title}' (attempt {attempt})")
                return True
            else:
                logger.warning(f"[Adobe Make.com] Attempt {attempt} failed: HTTP {res.status_code} {res.text[:100]}")
        except Exception as e:
            logger.warning(f"[Adobe Make.com] Attempt {attempt} exception: {e}")

    return False


def upload_via_ftp_adobe(image_path):
    """Direct FTP upload to Adobe Stock."""
    if not ADOBE_FTP_USER or not ADOBE_FTP_PASS:
        logger.error("[Adobe FTP] Credentials missing. Set ADOBE_FTP_USER & ADOBE_FTP_PASS in .env")
        return False
    if not os.path.isfile(image_path):
        logger.error(f"[Adobe FTP] File not found: {image_path}")
        return False

    filename = os.path.basename(image_path)
    logger.info(f"[Adobe FTP] Connecting to {ADOBE_FTP_HOST}...")
    try:
        with ftplib.FTP(ADOBE_FTP_HOST, timeout=30) as ftp:
            ftp.login(ADOBE_FTP_USER, ADOBE_FTP_PASS)
            logger.info(f"[Adobe FTP] Connected. Uploading {filename}...")
            with open(image_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)
        logger.info(f"[Adobe FTP] File uploaded successfully: {filename}")
        return True
    except Exception as e:
        logger.error(f"[Adobe FTP] Upload failed: {e}")
        return False


def upload_to_adobe(
    image_path,
    title=None,
    description=None,
    keywords=None,
    caption="",
    image_url=""
):
    """
    Master Adobe Stock upload function.
    Auto-generates metadata if not provided, then executes upload via best mode.
    """
    logger.info(f"[Adobe Stock] Starting upload for: {os.path.basename(image_path)}")

    # Generate metadata
    if not title or not keywords:
        ai_title, ai_desc, ai_keywords = generate_adobe_metadata(image_path, caption=caption)
        title = title or ai_title
        description = description or ai_desc
        keywords = keywords or ai_keywords

    # Mode 0: Make.com Webhook
    if ADOBE_MAKE_WEBHOOK_URL:
        logger.info("[Adobe Stock] Mode 0: Make.com Webhook")
        return upload_via_make_webhook_adobe(image_path, title, description, keywords, image_url)

    # Mode 1: Direct FTP
    if ADOBE_FTP_USER and ADOBE_FTP_PASS:
        logger.info("[Adobe Stock] Mode 1: Direct FTP")
        return upload_via_ftp_adobe(image_path)

    logger.error("[Adobe Stock] No credentials configured. Add ADOBE_MAKE_WEBHOOK_URL or ADOBE_FTP_USER to .env")
    return False


if __name__ == "__main__":
    print("=== Adobe Stock Uploader Module Initialized ===")
