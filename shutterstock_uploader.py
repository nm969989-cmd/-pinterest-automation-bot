"""
shutterstock_uploader.py
========================
Fully automatic Shutterstock Contributor uploader.
Uploads image + AI title + AI keywords — NO MANUAL WORK, NO API TOKEN NEEDED.

SUPPORTED MODES (auto-detected, tried in order):

  MODE 0 — Make.com Webhook (EASIEST — like Pinterest, no API token needed!):
    Set SHUTTERSTOCK_MAKE_WEBHOOK_URL in .env
    -> Bot FTPs image, then sends title/keywords to Make.com
    -> Make.com submits everything to Shutterstock on your behalf
    -> SAME METHOD as your Pinterest bot uses!
    -> Setup: make.com -> Create scenario -> FTP + Shutterstock modules

  MODE A — API Only (most direct):
    Set SHUTTERSTOCK_ACCESS_TOKEN in .env
    -> Uploads image binary + metadata in one flow

  MODE B — FTP + API (fully automated):
    Set SHUTTERSTOCK_FTP_USER + SHUTTERSTOCK_FTP_PASS + SHUTTERSTOCK_ACCESS_TOKEN
    -> FTP uploads the file, API auto-submits title/keywords immediately

  MODE C — FTP Only (semi-manual, last resort):
    Set only SHUTTERSTOCK_FTP_USER + SHUTTERSTOCK_FTP_PASS
    -> File is uploaded, title/keywords added manually on portal

HOW TO GET CREDENTIALS:
  Make.com  : https://make.com -> Create account (FREE) -> New scenario
              Add: FTP module + Shutterstock module -> Get webhook URL
  FTP creds : https://submit.shutterstock.com -> Profile -> FTP
  API token : https://www.shutterstock.com/account/developers/apps

CATEGORY IDs (common):
  1=Animals, 2=Buildings, 3=Business, 5=Food, 9=Interiors,
  10=Misc, 11=Nature, 14=People, 18=Sports, 19=Technology
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

# Shutterstock credentials from .env
SHUTTERSTOCK_ACCESS_TOKEN = os.getenv("SHUTTERSTOCK_ACCESS_TOKEN", "")
SHUTTERSTOCK_FTP_HOST     = os.getenv("SHUTTERSTOCK_FTP_HOST", "ftp.shutterstock.com")
SHUTTERSTOCK_FTP_USER     = os.getenv("SHUTTERSTOCK_FTP_USER", "")
SHUTTERSTOCK_FTP_PASS     = os.getenv("SHUTTERSTOCK_FTP_PASS", "")

# AI keys (same ones used by your Pinterest bot)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY", "")

# Make.com Webhook — EASIEST METHOD (no API token, same as Pinterest bot!)
# How to set up (5 min):
#   1. Go to https://make.com and create a FREE account
#   2. Click "Create a new scenario"
#   3. Add module: "Webhooks" -> "Custom Webhook" -> Copy the webhook URL
#   4. Add module: "Shutterstock" -> "Upload a Content File"
#   5. Map the webhook fields (title, description, keywords, ftp_filename)
#   6. Paste the webhook URL as SHUTTERSTOCK_MAKE_WEBHOOK_URL in .env
SHUTTERSTOCK_MAKE_WEBHOOK_URL = os.getenv("SHUTTERSTOCK_MAKE_WEBHOOK_URL", "")

SS_API_BASE      = "https://api.shutterstock.com/v2"
_OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
_VISION_MODELS   = [
    "minimax/minimax-m3:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
]


# ─────────────────────────────────────────────────────────────────────────────
# MODE 0: MAKE.COM WEBHOOK (No API token needed — same as Pinterest method)
# ─────────────────────────────────────────────────────────────────────────────

def upload_via_make_webhook_ss(image_path, title, description, keywords, category_id=10):
    """
    Upload to Shutterstock via Make.com webhook — EXACTLY like the Pinterest bot.
    No Shutterstock API token needed. Make.com handles everything.

    Flow:
      1. FTP upload the image to Shutterstock
      2. Send title/keywords/filename to Make.com webhook
      3. Make.com submits the metadata to Shutterstock automatically

    Setup Make.com scenario (one-time, 5 minutes):
      https://make.com -> New Scenario ->
        [Webhook] -> [FTP: Upload] -> [Shutterstock: Submit Content]
      Paste the webhook URL into SHUTTERSTOCK_MAKE_WEBHOOK_URL in .env
    """
    if not SHUTTERSTOCK_MAKE_WEBHOOK_URL:
        return False

    # Step 1: FTP upload the image (so Shutterstock has the file)
    ftp_filename = _ftp_upload_file(image_path)
    if not ftp_filename:
        logger.error("[SS Make.com] FTP upload failed, cannot proceed.")
        return False

    # Step 2: Send all metadata to Make.com webhook
    payload = {
        "ftp_filename":  ftp_filename,
        "title":         title[:200],
        "description":   description[:500],
        "keywords":      ", ".join(keywords[:50]),   # comma-separated string
        "keywords_list": keywords[:50],              # also as array
        "category_id":   str(category_id),
    }

    delays = [0, 5, 15]  # retry with backoff (same as Pinterest bot)
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(
                SHUTTERSTOCK_MAKE_WEBHOOK_URL,
                json=payload,
                timeout=20,
            )
            if res.status_code in (200, 201, 204):
                logger.info(
                    f"[SS Make.com] Submitted: '{title}'"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                return True
            else:
                logger.warning(
                    f"[SS Make.com] Attempt {attempt}/3 failed: "
                    f"HTTP {res.status_code} {res.text[:100]}"
                )
        except Exception as e:
            logger.warning(f"[SS Make.com] Attempt {attempt}/3 exception: {e}")

    logger.error(f"[SS Make.com] All 3 attempts failed for: '{title}'")
    return False

# ─────────────────────────────────────────────────────────────────────────────
# AI TITLE + KEYWORD GENERATOR (same AI your Pinterest bot uses)
# ─────────────────────────────────────────────────────────────────────────────

# Shutterstock-specific prompt (professional stock photo style, not Pinterest)
_SS_VISION_PROMPT = (
    "You are a Shutterstock stock photo expert and SEO specialist.\n"
    "You are looking at this image to submit it as stock content.\n"
    "Caption hint: {caption}\n\n"
    "Analyze the image carefully and respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<clear descriptive title, max 200 chars, professional stock photo style>",\n'
    '  "description": "<2-3 sentences describing what is in the image, who would use it, max 400 chars>",\n'
    '  "keywords": ["<40 to 50 highly relevant single or two-word keywords for stock buyers>"]\n'
    "}}\n\n"
    "Rules:\n"
    "- Title: descriptive, professional, NO emojis, NO special chars\n"
    "- Keywords: 40-50 items, mix of specific and broad terms, all lowercase\n"
    "- Think like a buyer searching for this image (what words would they type?)\n"
    "- Include: subject, mood, style, colors, setting, use-case keywords"
)

_SS_TEXT_PROMPT = (
    "You are a Shutterstock stock photo SEO expert.\n"
    "Based on this caption hint, generate metadata for a stock image submission.\n"
    "Caption: {caption}\n\n"
    "Respond ONLY with valid JSON (no markdown):\n"
    '{{\n'
    '  "title": "<descriptive title max 200 chars, professional, no emojis>",\n'
    '  "description": "<2-3 sentences about the image, max 400 chars>",\n'
    '  "keywords": ["<40 relevant keywords for stock buyers, lowercase>"]\n'
    "}}\n"
    "Rules: professional tone, think like a buyer, include colors/mood/subject/use-case"
)


def _parse_ss_ai_response(raw):
    """Parse AI JSON response into (title, description, keywords) for Shutterstock."""
    try:
        clean = re.sub(r'^```[a-z]*\n?', '', raw.strip())
        clean = re.sub(r'\n?```$', '', clean)
        data  = json.loads(clean)
        title       = data.get("title", "").strip()[:200]
        description = data.get("description", "").strip()[:400]
        keywords    = data.get("keywords", [])
        # Ensure keywords is a list of clean strings
        keywords = [str(k).strip().lower() for k in keywords if k][:50]
        if title and keywords:
            logger.info(f"[SS AI] Title: '{title}' | Keywords: {len(keywords)} tags")
            return title, description, keywords
    except Exception as e:
        logger.warning(f"[SS AI] Failed to parse AI response: {e}")
    return None


def _ai_via_vision(image_path, caption):
    """Use OpenRouter vision AI to look at the image and generate SS metadata."""
    if not OPENROUTER_API_KEY or not image_path or not os.path.isfile(image_path):
        return None
    try:
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode('utf-8')
        mime = "image/jpeg"
        if image_path.lower().endswith('.png'):  mime = "image/png"
        if image_path.lower().endswith('.webp'): mime = "image/webp"

        prompt = _SS_VISION_PROMPT.format(caption=caption or "(no caption)")
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type":  "application/json",
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
                    "max_tokens": 600
                }
                logger.info(f"[SS AI Vision] Trying model: {model}")
                res = requests.post(_OPENROUTER_URL, json=payload, headers=headers, timeout=30)
                if res.status_code == 200:
                    raw    = res.json()['choices'][0]['message']['content']
                    result = _parse_ss_ai_response(raw)
                    if result:
                        logger.info(f"[SS AI Vision] Success with {model}!")
                        return result
                    logger.warning(f"[SS AI Vision] {model} returned bad JSON, trying next...")
                elif res.status_code in (429, 503):
                    logger.warning(f"[SS AI Vision] {model} rate-limited, trying next...")
                else:
                    logger.warning(f"[SS AI Vision] {model} error {res.status_code}, trying next...")
            except Exception as e:
                logger.warning(f"[SS AI Vision] {model} exception: {e}, trying next...")
    except Exception as e:
        logger.warning(f"[SS AI Vision] Could not read image: {e}")
    logger.warning("[SS AI Vision] All vision models failed.")
    return None


def _ai_via_gemini_text(caption):
    """Gemini text-only fallback for generating Shutterstock metadata."""
    if not GEMINI_API_KEY:
        return None
    try:
        from google import genai as _genai
        client  = _genai.Client(api_key=GEMINI_API_KEY)
        prompt  = _SS_TEXT_PROMPT.format(caption=caption or "anime digital artwork")
        resp    = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
        result  = _parse_ss_ai_response(resp.text)
        if result:
            logger.info("[SS AI Gemini] Metadata generated via Gemini text.")
        return result
    except Exception as e:
        logger.warning(f"[SS AI Gemini] Failed: {e}")
    return None


def _offline_fallback(image_path, caption):
    """Offline fallback when no AI is available — generates basic metadata from filename/caption."""
    base = os.path.splitext(os.path.basename(image_path or "artwork"))[0]
    base = re.sub(r'[_\-]+', ' ', base).strip()
    hint = (caption or base or "digital artwork").split('\n')[0][:80]
    clean = re.sub(r'[^\w\s]', '', hint).strip()

    title = f"{clean} digital art illustration" if clean else "Digital artwork illustration"
    description = (
        f"High quality digital illustration: {clean}. "
        "Suitable for commercial use, print, web, and editorial projects. "
        "Clean, vibrant, and professional artwork."
    )
    keywords = [
        "digital art", "illustration", "artwork", "creative", "colorful",
        "design", "graphic", "fantasy", "character", "background",
        "wallpaper", "poster", "painting", "vibrant", "professional",
        "anime", "manga", "japanese", "cartoon", "drawing",
        "high quality", "commercial", "editorial", "print", "web design",
        "decorative", "modern", "artistic", "creative design", "digital painting",
    ]
    logger.info(f"[SS AI Offline] Generated offline metadata for: {clean}")
    return title, description, keywords


def generate_shutterstock_metadata(image_path, caption=""):
    """
    Auto-generates professional title, description, and keywords for Shutterstock.
    Uses the same AI pipeline as your Pinterest bot:

      1. OpenRouter Vision AI — SEES the actual image (best, FREE)
         Requires: OPENROUTER_API_KEY in .env

      2. Gemini Text AI — text-based generation (good fallback)
         Requires: GEMINI_API_KEY in .env

      3. Offline fallback — uses filename/caption (always works, no API)

    Args:
        image_path : Path to the image file
        caption    : Optional text hint (filename, Telegram caption, etc.)

    Returns:
        (title, description, keywords) — tuple of strings + list
    """
    logger.info("[SS AI] Generating Shutterstock metadata automatically...")

    # Try vision AI first (sees the actual image)
    if OPENROUTER_API_KEY:
        result = _ai_via_vision(image_path, caption)
        if result:
            return result

    # Try Gemini text fallback
    if GEMINI_API_KEY:
        result = _ai_via_gemini_text(caption)
        if result:
            return result

    # Always-works offline fallback
    logger.warning("[SS AI] No AI keys set — using offline fallback.")
    logger.warning("[SS AI] Add OPENROUTER_API_KEY to .env for best results (FREE).")
    return _offline_fallback(image_path, caption)


def _api_headers():
    return {
        "Authorization": f"Bearer {SHUTTERSTOCK_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# METHOD 1: API Upload
# ─────────────────────────────────────────────────────────────────────────────

def upload_image_via_api(image_path, title, description, keywords, category_id=1, editorial=False):
    """Upload image via Shutterstock Contributor API (requires approved API access)."""
    if not SHUTTERSTOCK_ACCESS_TOKEN:
        logger.error("[SS API] Set SHUTTERSTOCK_ACCESS_TOKEN in .env")
        return False
    if not os.path.isfile(image_path):
        logger.error(f"[SS API] File not found: {image_path}")
        return False

    logger.info(f"[SS API] Uploading: {os.path.basename(image_path)}")
    try:
        # Step 1: Upload the image binary
        upload_headers = {
            "Authorization": f"Bearer {SHUTTERSTOCK_ACCESS_TOKEN}",
            "Content-Type": "image/jpeg",
            "Accept": "application/json",
        }
        with open(image_path, "rb") as f:
            upload_res = requests.post(
                f"{SS_API_BASE}/contributors/images/upload",
                headers=upload_headers,
                data=f,
                timeout=120,
            )
        if upload_res.status_code not in (200, 201):
            logger.error(f"[SS API] Upload failed: {upload_res.status_code} {upload_res.text[:200]}")
            return False

        upload_id = upload_res.json().get("upload_id") or upload_res.json().get("id")
        logger.info(f"[SS API] File uploaded. Upload ID: {upload_id}")

        # Step 2: Submit metadata
        metadata = {
            "upload_id": upload_id,
            "title": title[:200],
            "description": description[:1000],
            "keywords": keywords[:50],
            "categories": [{"id": str(category_id)}],
            "editorial": editorial,
            "content_type": "photo",
            "is_adult": False,
        }
        submit_res = requests.post(
            f"{SS_API_BASE}/contributors/images",
            headers=_api_headers(),
            json=metadata,
            timeout=30,
        )
        if submit_res.status_code in (200, 201):
            asset_id = submit_res.json().get("id", "unknown")
            logger.info(f"[SS API] Submitted for review! Asset ID: {asset_id}")
            logger.info("[SS API] Review takes 1-7 business days.")
            return True
        else:
            logger.error(f"[SS API] Submit failed: {submit_res.status_code} {submit_res.text[:200]}")
            return False

    except Exception as e:
        logger.error(f"[SS API] Exception: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# METHOD 2: FTP Upload + Auto Metadata (FULLY AUTOMATIC)
# ─────────────────────────────────────────────────────────────────────────────

def _ftp_upload_file(image_path):
    """
    Internal helper: uploads just the image file via FTP.
    Returns filename on success, None on failure.
    """
    if not SHUTTERSTOCK_FTP_USER or not SHUTTERSTOCK_FTP_PASS:
        logger.error("[SS FTP] Set SHUTTERSTOCK_FTP_USER and SHUTTERSTOCK_FTP_PASS in .env")
        logger.error("[SS FTP] Get credentials: https://submit.shutterstock.com -> Profile -> FTP")
        return None
    if not os.path.isfile(image_path):
        logger.error(f"[SS FTP] File not found: {image_path}")
        return None

    filename = os.path.basename(image_path)
    logger.info(f"[SS FTP] Connecting to {SHUTTERSTOCK_FTP_HOST}...")
    try:
        with ftplib.FTP(SHUTTERSTOCK_FTP_HOST) as ftp:
            ftp.login(SHUTTERSTOCK_FTP_USER, SHUTTERSTOCK_FTP_PASS)
            logger.info(f"[SS FTP] Connected. Uploading: {filename}")
            with open(image_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)
        logger.info(f"[SS FTP] File uploaded via FTP: {filename}")
        return filename
    except ftplib.all_errors as e:
        logger.error(f"[SS FTP] Error: {e}")
        return None


def _submit_ftp_metadata(ftp_filename, title, description, keywords, category_id=10, editorial=False):
    """
    After FTP upload, automatically submits title/keywords via API.
    This replaces the need to go to the Shutterstock portal manually.

    Shutterstock API accepts the FTP filename as 'ftp_filename' to link
    the already-uploaded file with the metadata submission.
    """
    if not SHUTTERSTOCK_ACCESS_TOKEN:
        logger.warning("[SS FTP] No API token — metadata must be added manually at:")
        logger.warning("[SS FTP] https://submit.shutterstock.com/edit")
        return False

    logger.info(f"[SS FTP] Auto-submitting metadata for: {ftp_filename}")
    import time
    # Small delay to let Shutterstock process the FTP file before we reference it
    time.sleep(3)

    try:
        metadata = {
            "ftp_filename": ftp_filename,    # Links to the FTP-uploaded file
            "title": title[:200],
            "description": description[:1000],
            "keywords": keywords[:50],
            "categories": [{"id": str(category_id)}],
            "editorial": editorial,
            "content_type": "photo",
            "is_adult": False,
        }
        res = requests.post(
            f"{SS_API_BASE}/contributors/images",
            headers=_api_headers(),
            json=metadata,
            timeout=30,
        )
        if res.status_code in (200, 201):
            asset_id = res.json().get("id", "unknown")
            logger.info(f"[SS FTP] Metadata submitted! Asset ID: {asset_id}")
            logger.info("[SS FTP] Image submitted for review (1-7 business days).")
            return True
        else:
            logger.error(f"[SS FTP] Metadata submit failed: {res.status_code} {res.text[:300]}")
            logger.warning("[SS FTP] File was FTP-uploaded. Add metadata manually at:")
            logger.warning("[SS FTP] https://submit.shutterstock.com/edit")
            return False
    except Exception as e:
        logger.error(f"[SS FTP] Metadata exception: {e}")
        return False


def upload_image_via_ftp_auto(image_path, title, description, keywords, category_id=10, editorial=False):
    """
    FULLY AUTOMATIC FTP upload.
    Step 1: FTP uploads the image file.
    Step 2: API automatically submits title + keywords — NO portal visit needed!

    Requires:
      - SHUTTERSTOCK_FTP_USER + SHUTTERSTOCK_FTP_PASS  (for file upload)
      - SHUTTERSTOCK_ACCESS_TOKEN                       (for metadata submission)

    If no API token, falls back to FTP-only (manual metadata on portal).
    """
    # Step 1: Upload the file via FTP
    ftp_filename = _ftp_upload_file(image_path)
    if not ftp_filename:
        return False

    # Step 2: Automatically submit metadata via API (no manual work needed!)
    meta_ok = _submit_ftp_metadata(ftp_filename, title, description, keywords, category_id, editorial)

    if meta_ok:
        logger.info("[SS FTP] Complete! Image uploaded + metadata submitted automatically.")
    else:
        logger.warning("[SS FTP] File uploaded via FTP but metadata needs manual entry.")
        logger.warning("[SS FTP] Visit: https://submit.shutterstock.com/edit")

    return True  # FTP upload succeeded even if metadata step failed


# ─────────────────────────────────────────────────────────────────────────────
# Check Submission Status
# ─────────────────────────────────────────────────────────────────────────────

def check_submission_status(asset_id=None):
    """
    Check review status of submitted images.
    Status: pending / approved / rejected
    """
    if not SHUTTERSTOCK_ACCESS_TOKEN:
        logger.error("[SS API] Set SHUTTERSTOCK_ACCESS_TOKEN in .env")
        return {}
    try:
        if asset_id:
            url = f"{SS_API_BASE}/contributors/images/{asset_id}"
        else:
            url = f"{SS_API_BASE}/contributors/images?sort=newest&per_page=10"
        res = requests.get(url, headers=_api_headers(), timeout=20)
        if res.status_code == 200:
            return res.json()
        else:
            logger.error(f"[SS API] Status check failed: {res.status_code}")
            return {}
    except Exception as e:
        logger.error(f"[SS API] Status check error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Get Earnings Summary
# ─────────────────────────────────────────────────────────────────────────────

def get_earnings_summary():
    """Fetch your Shutterstock contributor earnings."""
    if not SHUTTERSTOCK_ACCESS_TOKEN:
        logger.error("[SS API] Set SHUTTERSTOCK_ACCESS_TOKEN in .env")
        return {}
    try:
        res = requests.get(
            f"{SS_API_BASE}/contributors/earnings/summary",
            headers=_api_headers(),
            timeout=20,
        )
        if res.status_code == 200:
            data = res.json()
            total = data.get("total_amount", {})
            logger.info(f"[SS] Earnings: {total.get('currency', 'USD')} {total.get('value', '0.00')}")
            return data
    except Exception as e:
        logger.error(f"[SS API] Earnings error: {e}")
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# MASTER UPLOAD FUNCTION (auto-detects best method)
# ─────────────────────────────────────────────────────────────────────────────

def upload_to_shutterstock(
    image_path,
    title=None,
    description=None,
    keywords=None,
    caption="",
    category_id=10,
    editorial=False,
):
    """
    Master function — FULLY AUTOMATIC like your Pinterest bot.
    Bot generates title + keywords by LOOKING at the image using AI.

    TITLE/KEYWORD GENERATION (auto, no manual work):
      - If title/keywords are NOT provided, AI generates them automatically
      - Uses the same OpenRouter Vision AI + Gemini as your Pinterest bot
      - Falls back to offline generation if no AI keys are set

    UPLOAD MODES (auto-detected):
      MODE A — API token only  -> Binary upload + metadata in one flow
      MODE B — FTP + API token -> FTP upload + auto metadata (RECOMMENDED)
      MODE C — FTP only        -> Upload file, metadata needs manual entry

    Args:
        image_path   : Local path to JPG image (min 4MP)
        title        : Title (auto-generated by AI if not provided)
        description  : Description (auto-generated if not provided)
        keywords     : Keywords list (auto-generated if not provided)
        caption      : Hint text for AI (e.g. Telegram caption, filename)
        category_id  : Category ID (10=Misc, 14=People, 19=Technology...)
        editorial    : True for news/event images (no model release needed)

    Returns:
        True on success, False on failure.
    """
    logger.info(f"[Shutterstock] Starting: {os.path.basename(image_path)}")

    # ── AUTO-GENERATE title/keywords using AI (just like Pinterest bot!) ──────
    if not title or not keywords:
        logger.info("[Shutterstock] No title/keywords given — asking AI to generate them...")
        ai_title, ai_desc, ai_keywords = generate_shutterstock_metadata(
            image_path=image_path,
            caption=caption or os.path.splitext(os.path.basename(image_path))[0],
        )
        title       = title       or ai_title
        description = description or ai_desc
        keywords    = keywords    or ai_keywords
        logger.info(f"[Shutterstock] AI generated — Title: '{title}' | Keywords: {len(keywords)} tags")
    else:
        logger.info(f"[Shutterstock] Using provided title: '{title}'")

    if not keywords:
        keywords = []

    # ── MODE 0: Make.com Webhook (EASIEST — no API token needed!) ────────────
    # Exactly like your Pinterest bot uses Make.com — same approach!
    if SHUTTERSTOCK_MAKE_WEBHOOK_URL and SHUTTERSTOCK_FTP_USER:
        logger.info("[Shutterstock] Mode 0: Make.com webhook (no API token needed).")
        return upload_via_make_webhook_ss(image_path, title, description, keywords, category_id)

    # ── MODE A: API-only upload ───────────────────────────────────────────────
    if SHUTTERSTOCK_ACCESS_TOKEN and not (SHUTTERSTOCK_FTP_USER and SHUTTERSTOCK_FTP_PASS):
        logger.info("[Shutterstock] Mode A: API binary upload.")
        return upload_image_via_api(image_path, title, description, keywords, category_id, editorial)

    # ── MODE B: FTP + API (FULLY AUTOMATIC — recommended) ───────────────────
    if SHUTTERSTOCK_FTP_USER and SHUTTERSTOCK_FTP_PASS:
        logger.info("[Shutterstock] Mode B: FTP + auto-metadata (fully automatic).")
        return upload_image_via_ftp_auto(image_path, title, description, keywords, category_id, editorial)

    # ── No credentials ────────────────────────────────────────────────────────
    logger.error("[Shutterstock] No credentials configured! Add to .env:")
    logger.error("  EASIEST:  SHUTTERSTOCK_MAKE_WEBHOOK_URL + SHUTTERSTOCK_FTP_USER + SHUTTERSTOCK_FTP_PASS")
    logger.error("  Make.com setup: https://make.com -> New scenario -> Webhook + Shutterstock")
    logger.error("  FTP creds: https://submit.shutterstock.com -> Profile -> FTP")
    return False


# Quick test
if __name__ == "__main__":
    print("=== Shutterstock Uploader Test ===")
    print("\nEarnings Summary:")
    print(json.dumps(get_earnings_summary(), indent=2))
    print("\nRecent Submissions:")
    print(json.dumps(check_submission_status(), indent=2))
