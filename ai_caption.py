"""
ai_caption.py - Vision-powered Pinterest Pin content generator.

Priority:
  1. OpenRouter vision  - actually SEES the anime image -> best quality (FREE)
  2. Gemini 1.5 Flash   - text-only fallback
  3. Regex              - offline fallback
"""

import os
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

# Vision model fallback chain (active multimodal free tiers on OpenRouter)
_VISION_MODELS = [
    "nvidia/nemotron-3.5-super-vl:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
]

_last_character_name = ""

def get_last_character_name() -> str:
    """Returns the most recently AI-extracted character name, or empty string."""
    global _last_character_name
    return _last_character_name

_VISION_PROMPT = (
    "You are a Pinterest content and safety expert specialising in anime merchandise marketing.\n"
    "Your primary duty is to determine whether the image is authentic anime/manga artwork, or an advertisement, marketing graphic, app screenshot, or non-anime content.\n\n"
    "Telegram caption: {caption}\n"
    "Channel: @{channel_name}\n\n"
    "Analyze the image.\n"
    "If the image is an advertisement, marketing flyer, channel promotion, app/phone UI screenshot, ad review notice, crypto/financial offer, or NOT authentic anime artwork:\n"
    "Respond ONLY with valid JSON:\n"
    '{{\n'
    '  "is_ad_or_marketing": true,\n'
    '  "rejection_reason": "<brief explanation e.g. Telegram ad flyer / UI screenshot>"\n'
    '}}\n\n'
    "ONLY if the image is genuine anime artwork or character illustration, respond with:\n"
    '{{\n'
    '  "is_ad_or_marketing": false,\n'
    '  "anime_name": "Attack on Titan",\n'
    '  "character_name": "Levi Ackerman",\n'
    '  "title": "Levi Ackerman - Cleanest Blade in the Corps 🔥 Anime Art",\n'
    '  "description": "High-octane artwork capturing Captain Levi in mid-air maneuver. Perfect for decorating your bedroom or gaming room with premium prints and posters. Grab official figures and wall art on Amazon today!\\n\\n#anime #attackontitan #levi #animeart"\n'
    '}}\n\n'
    "CRITICAL RULES:\n"
    "- Identify the real anime series and character from the image/caption.\n"
    "- anime_name MUST be only the series title (e.g. 'Attack on Titan', 'Demon Slayer', 'Solo Leveling'). NEVER include quotes, dialogue, or full sentences.\n"
    "- Never output angle brackets, placeholders, or copy the example names verbatim.\n"
    "- title under 90 chars, description under 450 chars, hashtags at END, include #anime"
)

_TEXT_PROMPT = (
    "You are a Pinterest content expert for anime merchandise.\n"
    "Telegram post from @{channel_name}:\n"
    "Caption: {caption}\n\n"
    "Respond ONLY with valid JSON - no markdown:\n"
    '{{\n'
    '  "anime_name": "Attack on Titan",\n'
    '  "character_name": "Levi Ackerman",\n'
    '  "title": "Levi Ackerman - Cleanest Blade in the Corps 🔥 Anime Art",\n'
    '  "description": "High-octane artwork capturing Captain Levi in mid-air maneuver. Perfect for decorating your bedroom or gaming room with premium prints and posters. Grab official figures and wall art on Amazon today!\\n\\n#anime #attackontitan #levi #animeart"\n'
    "}}\n\n"
    "CRITICAL RULES:\n"
    "- anime_name MUST be only the series title (e.g. 'Attack on Titan', 'Demon Slayer', 'Solo Leveling'). NEVER include quotes, dialogue, or full sentences.\n"
    "- Never output angle brackets, placeholders, or copy the example names verbatim.\n"
    "- title <90 chars, description <450 chars, hashtags at end, include #anime"
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


def _parse_ai_response(raw, channel_name, caption=""):
    """Parse JSON from AI response, return (anime_name, title, description) or None."""
    global _last_character_name
    if not raw or not isinstance(raw, str):
        return None
    try:
        # Strip reasoning tags (e.g. <think>...</think>) from reasoning models
        clean = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.IGNORECASE).strip()
        # Strip markdown code fences
        clean = re.sub(r'^```(?:json)?\s*', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'\s*```$', '', clean)
        # Extract the outermost JSON object substring {...}
        json_match = re.search(r'\{[\s\S]*\}', clean)
        if not json_match:
            logger.debug(f"[AI] No JSON block found in AI response: '{raw[:100]}...'")
            return None
        clean = json_match.group(0)

        data = json.loads(clean)
        if not isinstance(data, dict):
            return None

        if data.get("is_ad_or_marketing") is True or "rejection_reason" in data:
            reason = data.get("rejection_reason") or "Identified as ad/marketing"
            logger.warning(f"[AI] Image classified as AD/MARKETING by Vision AI: {reason}")
            return "REJECTED_AD", reason, ""

        anime_name = str(data.get("anime_name") or "Anime").strip()
        character  = str(data.get("character_name") or "").strip()
        title      = str(data.get("title") or "").strip()[:100]
        desc_body  = str(data.get("description") or "").strip()[:480]

        from image_processor import is_promotional_caption
        is_promo, promo_reason = is_promotional_caption(f"{title} {desc_body}")
        if is_promo:
            logger.warning(f"[AI] Generated content contains promotional text: {promo_reason}")
            return "REJECTED_AD", promo_reason, ""

        # 1. Reject responses containing prompt template placeholders or angle brackets
        for val in (anime_name, character, title, desc_body):
            if "<" in val or ">" in val:
                logger.warning(f"[AI] Rejected AI response containing placeholder angle brackets: '{val}'")
                return None
            for bad_token in ("specific anime series", "catchy hook", "character name -", "3-4 exciting sentences"):
                if bad_token in val.lower():
                    logger.warning(f"[AI] Rejected AI response containing schema instruction: '{val}'")
                    return None

        # 2. Canonicalize anime_name using board_router taxonomy (strips quotes/concatenations)
        from board_router import extract_canonical_anime_name
        anime_name = extract_canonical_anime_name(anime_name, caption=caption)

        if not title or title.lower() == anime_name.lower():
            title = f"{character} - {anime_name} Poster" if character else f"{anime_name} Aesthetic Poster"

        description = _inject_link(desc_body, channel_name)
        _last_character_name = character
        logger.info(f"[AI] Anime: '{anime_name}' | Character: '{character}' | Title: '{title}'")
        return anime_name, title, description
    except Exception as e:
        logger.warning(f"[AI] Failed to parse response: {e}")
        return None


def _fallback_generate(caption, channel_name):
    """Regex-based fallback. Always works, no API needed."""
    global _last_character_name
    _last_character_name = ""

    from board_router import extract_canonical_anime_name
    clean_anime = extract_canonical_anime_name("", caption=caption)

    first_line  = caption.split('\n')[0].strip() if caption else "Awesome Anime Art"
    clean_title = re.sub(r'[^\w\s\-\.]', '', first_line).strip()
    if not clean_title or len(clean_title) > 60 or clean_title.lower() == clean_anime.lower() or any(clean_title.lower().startswith(w) for w in ("he ", "she ", "they ", "saving ", "some ", "her ", "his ")):
        clean_title = f"{clean_anime} Aesthetic Art"

    title = f"{clean_title} - Epic Anime Poster"
    description = (
        f"Amazing {clean_anime} artwork!\n\n"
        "Find merchandise, figures & posters on Amazon\n"
        "##LINK_PLACEHOLDER##\n\n"
        f"Source: @{channel_name}\n"
        "#ad #affiliate #anime #animeart #manga #fanart"
    )
    logger.info(f"[FALLBACK] Generated pin content for anime: '{clean_anime}', title: '{title}'")
    return clean_anime, title, description


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
                    "max_tokens": 700
                }

                logger.info(f"[VISION] Trying model: {model}")
                res = http_requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=30)

                if res.status_code == 200:
                    res_json = res.json()
                    choices = res_json.get('choices', [])
                    raw = ""
                    if choices and choices[0].get('message'):
                        msg = choices[0]['message']
                        c = msg.get('content')
                        if isinstance(c, list):
                            raw = "".join(item.get('text', '') for item in c if isinstance(item, dict))
                        else:
                            raw = c or msg.get('text') or msg.get('reasoning') or ""
                    result = _parse_ai_response(raw, channel_name, caption=caption)
                    if result:
                        logger.info(f"[VISION] Success with {model}!")
                        del img_b64
                        import gc
                        gc.collect()
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

    logger.warning("[VISION] All vision models failed, attempting text-based AI fallback...")
    return None


def _openrouter_text(caption, channel_name, api_key):
    """Text-only OpenRouter fallback when vision models fail."""
    if not api_key:
        return None
    try:
        prompt = _TEXT_PROMPT.format(caption=caption or "(no caption)", channel_name=channel_name)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://pinterest-automation-bot.onrender.com",
            "X-Title": "Animanoizing Bot"
        }
        text_models = [
            "nvidia/nemotron-3.5-lightning:free",
            "liquid/lfm-2.5-2.6b:free",
            "openrouter/free",
        ]
        for model in text_models:
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 700
                }
                logger.info(f"[AI:Text] Trying model: {model}")
                res = http_requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=20)
                if res.status_code == 200:
                    res_json = res.json()
                    choices = res_json.get('choices', [])
                    raw = ""
                    if choices and choices[0].get('message'):
                        msg = choices[0]['message']
                        c = msg.get('content')
                        if isinstance(c, list):
                            raw = "".join(item.get('text', '') for item in c if isinstance(item, dict))
                        else:
                            raw = c or msg.get('text') or msg.get('reasoning') or ""
                    result = _parse_ai_response(raw, channel_name, caption=caption)
                    if result:
                        logger.info(f"[AI:Text] Success with OpenRouter text model {model}")
                        return result
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[AI:Text] OpenRouter text fallback failed: {e}")
    return None


_gemini_client = None

def _gemini_vision(image_path, caption, channel_name, api_key):
    """Vision-based Gemini content generator (analyzes actual anime artwork)."""
    global _gemini_client
    if not _GENAI_AVAILABLE or not image_path or not os.path.exists(image_path):
        return None
    try:
        if _gemini_client is None:
            _gemini_client = _genai.Client(api_key=api_key)
        from PIL import Image
        img = Image.open(image_path)
        prompt = _VISION_PROMPT.format(caption=caption or "(no caption)", channel_name=channel_name)
        response = _gemini_client.models.generate_content(model=_GEMINI_MODEL, contents=[img, prompt])
        raw = response.text if hasattr(response, 'text') else str(response)
        result = _parse_ai_response(raw, channel_name, caption=caption)
        if result:
            logger.info("[GEMINI:Vision] Caption generated from image analysis.")
        return result
    except Exception as e:
        logger.warning(f"[GEMINI:Vision] Failed: {e}")
        return None


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
        result   = _parse_ai_response(response.text, channel_name, caption=caption)
        if result:
            logger.info("[GEMINI:Text] Caption generated from text analysis.")
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
    from image_processor import is_promotional_caption, is_marketing_or_ad_image
    is_promo, promo_reason = is_promotional_caption(caption)
    if is_promo:
        logger.warning(f"[ContentGen] Aborting: caption contains promotional content ({promo_reason})")
        return "REJECTED_AD", promo_reason, ""

    if image_path:
        is_ad, ad_reason = is_marketing_or_ad_image(image_path, caption)
        if is_ad:
            logger.warning(f"[ContentGen] Aborting: image detected as ad/marketing ({ad_reason})")
            return "REJECTED_AD", ad_reason, ""
    """
    Generates (anime_name, title, description_template) for a Pinterest pin.

    Priority:
      1. Gemini vision     -- If GEMINI_API_KEY is configured (fast, accurate, high free limits)
      2. OpenRouter vision -- SEES the actual image (multimodal free tier)
      3. Gemini text       -- text-only fallback
      4. OpenRouter text   -- text-only NLP fallback
      5. Regex fallback    -- always works offline

    description_template contains '##LINK_PLACEHOLDER##' for affiliate link injection.
    """
    if api_key and image_path and _GENAI_AVAILABLE:
        result = _gemini_vision(image_path, caption, channel_name, api_key)
        if result:
            return result

    if openrouter_key and image_path:
        result = _openrouter_vision(image_path, caption, channel_name, openrouter_key)
        if result:
            return result

    if api_key and _GENAI_AVAILABLE:
        result = _gemini_text(caption, channel_name, api_key)
        if result:
            return result

    if openrouter_key:
        result = _openrouter_text(caption, channel_name, openrouter_key)
        if result:
            return result

    # Never generate generic fallback for an ad image
    if image_path:
        is_ad, ad_reason = is_marketing_or_ad_image(image_path, caption)
        if is_ad:
            logger.warning(f"[ContentGen] Aborting fallback: image is ad/marketing ({ad_reason})")
            return "REJECTED_AD", ad_reason, ""

    return _fallback_generate(caption, channel_name)
