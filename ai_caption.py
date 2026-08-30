"""
ai_caption.py - Vision-powered Pinterest Pin content generator.

Priority:
  1. OpenRouter vision  - actually SEES the anime image -> best quality (FREE)
  2. Gemini 1.5 Flash   - text-only fallback
  3. Regex              - offline fallback
"""

import re
import json
import random
import base64
import requests as http_requests
from logger import get_logger

logger = get_logger(__name__)

# -- Gemini SDK (optional) ----------------------------------------------------
try:
    from google import genai as _genai
    _GENAI_AVAILABLE = True
except ImportError:
    _GENAI_AVAILABLE = False

# -- OpenRouter config ---------------------------------------------------------
_OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
_GEMINI_MODEL    = "gemini-1.5-flash"

# Vision model fallback chain (tried in order, skip on rate-limit/error)
_VISION_MODELS = [
    "minimax/minimax-m3:free",          # Primary   — free, vision capable
    "google/gemma-4-31b-it:free",       # Fallback1 — free Google model
    "google/gemma-4-26b-a4b-it:free",   # Fallback2 — free Google model
]

_VISION_PROMPT = (
    "You are a Pinterest content expert specialising in anime merchandise marketing.\n"
    "You are looking at an anime artwork image.\n\n"
    "Telegram caption: {caption}\n"
    "Channel: @{channel_name}\n\n"
    "Analyze the image and identify the anime series, character(s) and mood.\n\n"
    "Respond ONLY with valid JSON - no markdown:\n"
    '{{\n'
    '  "anime_name": "<specific anime series name>",\n'
    '  "title": "<punchy title max 90 chars, character name if visible, 1-2 emojis>",\n'
    '  "description": "<3-4 exciting sentences about artwork + find merch on Amazon. End with 6-8 hashtags>"\n'
    "}}\n\n"
    "Rules: title under 90 chars, description under 450 chars, hashtags at END, include #anime"
)

_TEXT_PROMPT = (
    "You are a Pinterest content expert for anime merchandise.\n"
    "Telegram post from @{channel_name}:\n"
    "Caption: {caption}\n\n"
    "Respond ONLY with valid JSON - no markdown:\n"
    '{{\n'
    '  "anime_name": "<specific anime series name>",\n'
    '  "title": "<punchy title max 90 chars, 1-2 emojis>",\n'
    '  "description": "<3-4 exciting sentences + Amazon merch mention + 6-8 hashtags at end>"\n'
    "}}\n"
    "Rules: title <90 chars, description <450 chars, hashtags at end, include #anime"
)


# High-converting CTA templates — rotated randomly per pin for organic feel
# ##LINK_PLACEHOLDER## is replaced with the actual Amazon affiliate link
_CTA_TEMPLATES = [
    "Shop this exact merch on Amazon India: ##LINK_PLACEHOLDER##",
    "Find posters, hoodies & figures for this anime: ##LINK_PLACEHOLDER##",
    "Get this as a poster or action figure on Amazon: ##LINK_PLACEHOLDER##",
    "Limited stock available - check it out: ##LINK_PLACEHOLDER##",
    "Tap the link to grab official merch: ##LINK_PLACEHOLDER##",
    "Own this art as a wall poster or hoodie: ##LINK_PLACEHOLDER##",
    "Perfect gift for any anime fan: ##LINK_PLACEHOLDER##",
    "Shop the collection on Amazon India: ##LINK_PLACEHOLDER##",
]


def _inject_link(description_body, channel_name):
    """Inserts a high-converting CTA with ##LINK_PLACEHOLDER## before hashtags."""
    cta = random.choice(_CTA_TEMPLATES)
    hashtag_idx = description_body.rfind('\n#')
    if hashtag_idx == -1:
        return (
            f"{description_body}\n\n"
            f"{cta}\n\n"
            f"Source: @{channel_name}"
        )
    before = description_body[:hashtag_idx].strip()
    tags   = description_body[hashtag_idx:].strip()
    return f"{before}\n\n{cta}\n\nSource: @{channel_name}\n{tags}"


def _parse_ai_response(raw, channel_name):
    """Parse JSON from AI response, return (anime_name, title, description) or None."""
    try:
        clean = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        clean = re.sub(r'\n?```$', '', clean)
        data  = json.loads(clean)
        anime_name  = data.get("anime_name", "Anime").strip()
        title       = data.get("title", "").strip()[:100]
        desc_body   = data.get("description", "").strip()[:480]
        description = _inject_link(desc_body, channel_name)
        logger.info(f"[AI] Anime: '{anime_name}' | Title: '{title}'")
        return anime_name, title, description
    except Exception as e:
        logger.warning(f"[AI] Failed to parse response: {e}")
        return None


def _fallback_generate(caption, channel_name):
    """Regex-based fallback. Always works, no API needed."""
    first_line  = caption.split('\n')[0] if caption else "Awesome Anime Art"
    clean_title = re.sub(r'[^\w\s\-\.]', '', first_line).strip()
    if not clean_title:
        clean_title = f"Anime Art from {channel_name}"
    title = f"{clean_title} - Epic Anime Poster"
    description = (
        f"Amazing {clean_title} artwork!\n\n"
        "Find merchandise, figures & posters on Amazon\n"
        "##LINK_PLACEHOLDER##\n\n"
        f"Source: @{channel_name}\n"
        "#ad #affiliate #anime #animeart #manga #fanart"
    )
    logger.info(f"[FALLBACK] Generated pin content for: {clean_title}")
    return clean_title, title, description


def _openrouter_vision(image_path, caption, channel_name, api_key):
    """Tries each vision model in _VISION_MODELS until one succeeds."""
    try:
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')

        mime = "image/jpeg"
        if image_path.lower().endswith('.png'):
            mime = "image/png"
        elif image_path.lower().endswith('.webp'):
            mime = "image/webp"

        prompt = _VISION_PROMPT.format(
            caption=caption or "(no caption)",
            channel_name=channel_name
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pinterest-automation-bot.onrender.com",
            "X-Title": "Animanoizing Bot"
        }

        for model in _VISION_MODELS:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
                                {"type": "text", "text": prompt}
                            ]
                        }
                    ],
                    "max_tokens": 400
                }

                logger.info(f"[VISION] Trying model: {model}")
                res = http_requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=30)

                if res.status_code == 200:
                    raw    = res.json()['choices'][0]['message']['content']
                    result = _parse_ai_response(raw, channel_name)
                    if result:
                        logger.info(f"[VISION] Success with {model}!")
                        return result
                    logger.warning(f"[VISION] {model} returned unparseable JSON, trying next...")
                elif res.status_code in (429, 503):
                    logger.warning(f"[VISION] {model} rate-limited/overloaded, trying next...")
                elif res.status_code == 404:
                    logger.warning(f"[VISION] {model} not found, trying next...")
                else:
                    logger.warning(f"[VISION] {model} error {res.status_code}, trying next...")

            except Exception as e:
                logger.warning(f"[VISION] {model} exception: {e}, trying next...")

    except Exception as e:
        logger.warning(f"[VISION] Could not read image: {e}")

    logger.warning("[VISION] All vision models failed, falling back.")
    return None



_gemini_client = None

def _gemini_text(caption, channel_name, api_key):
    """Text-only Gemini fallback (no image)."""
    global _gemini_client
    if not _GENAI_AVAILABLE:
        return None
    try:
        if _gemini_client is None:
            _gemini_client = _genai.Client(api_key=api_key)
        prompt   = _TEXT_PROMPT.format(caption=caption or "(no caption)", channel_name=channel_name)
        response = _gemini_client.models.generate_content(model=_GEMINI_MODEL, contents=prompt)
        result   = _parse_ai_response(response.text, channel_name)
        if result:
            logger.info("[GEMINI] Caption generated from text analysis.")
        return result
    except Exception as e:
        logger.warning(f"[GEMINI] Failed: {e}")
        return None


def generate_pin_content(
    caption,
    channel_name,
    image_path=None,
    api_key=None,
    openrouter_key=None
):
    """
    Generates (anime_name, title, description_template) for a Pinterest pin.

    Priority:
      1. OpenRouter vision -- SEES the actual image (best quality, FREE)
      2. Gemini text       -- text-only (good quality)
      3. Regex fallback    -- always works

    description_template contains '##LINK_PLACEHOLDER##' for affiliate link injection.
    """
    if openrouter_key and image_path:
        result = _openrouter_vision(image_path, caption, channel_name, openrouter_key)
        if result:
            return result

    if api_key and _GENAI_AVAILABLE:
        result = _gemini_text(caption, channel_name, api_key)
        if result:
            return result

    return _fallback_generate(caption, channel_name)
