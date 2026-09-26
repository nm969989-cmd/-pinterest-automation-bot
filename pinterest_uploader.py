import os
import time
import requests
from config import PINTEREST_ACCESS_TOKEN, PINTEREST_BOARD_ID, DRY_RUN, MAKE_WEBHOOK_URL
from image_host import upload_image_to_host
from logger import get_logger
from database import is_file_uploaded, mark_file_uploaded
from amazon_search import preflight_validate_destination, is_direct_product_link

logger = get_logger(__name__)

# Duplicate uploads are now tracked in SQLite (see database.py)
# Persistent across restarts — duplicates are prevented even after a crash.

# Module-level board ID cache so we only fetch once per session
_resolved_board_id: str = ""

def get_board_id_dynamically(headers):
    """Fetches the board ID dynamically using the API if the user didn't provide it"""
    try:
        url = "https://api.pinterest.com/v5/boards"
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            boards = res.json().get('items', [])
            for b in boards:
                # Prefer anime-themed board; fall back to first board
                if "anime" in b.get('name', '').lower() or not PINTEREST_BOARD_ID:
                    return b.get('id')
            if boards:
                return boards[0].get('id')  # Fallback to first board
    except Exception as e:
        logger.error(f"Failed to fetch board ID: {e}")
    return PINTEREST_BOARD_ID


def _resolve_board_id(board_id_override: str = "") -> str:
    """
    Resolves the Pinterest board ID to use for a pin.
    Priority order:
      1. Caller-supplied board_id_override (from board_router)
      2. PINTEREST_BOARD_ID from .env
      3. Dynamically fetched via Pinterest API (cached for the session)
    Logs clearly which source was used.
    """
    global _resolved_board_id

    # 1. Caller override (from genre board router)
    if board_id_override and board_id_override.strip().isdigit():
        logger.info(f"[Pinterest] Using genre-routed board_id: {board_id_override}")
        return board_id_override.strip()

    # 2. .env PINTEREST_BOARD_ID
    if PINTEREST_BOARD_ID and PINTEREST_BOARD_ID.strip().isdigit():
        logger.info(f"[Pinterest] Using PINTEREST_BOARD_ID from .env: {PINTEREST_BOARD_ID}")
        return PINTEREST_BOARD_ID.strip()

    # 3. Already fetched this session
    if _resolved_board_id:
        logger.info(f"[Pinterest] Using cached dynamically-fetched board_id: {_resolved_board_id}")
        return _resolved_board_id

    # 4. Fetch dynamically via Pinterest API
    if PINTEREST_ACCESS_TOKEN:
        headers = {
            "Authorization": f"Bearer {PINTEREST_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        fetched = get_board_id_dynamically(headers)
        if fetched:
            _resolved_board_id = str(fetched)
            logger.info(f"[Pinterest] Auto-fetched board_id from API: {_resolved_board_id} (cached for session)")
            return _resolved_board_id

    logger.warning(
        "[Pinterest] PINTEREST_BOARD_ID is empty and dynamic fetch failed. "
        "Make.com will use its default board. Set PINTEREST_BOARD_ID in .env to fix this."
    )
    return ""

def _verify_public_image_url(url: str, timeout: int = 15) -> bool:
    """
    Pre-flight check: Pinterest/Make can only fetch images that are publicly
    reachable. A Cloudinary/Catbox URL that 404s/blocks HEAD requests produces
    exactly the reported symptom: Telegram says 'Live' but nothing (or a blank)
    appears on the Pinterest Created tab.
    Returns True if the URL looks fetchable, False otherwise.
    """
    if not url or not url.startswith("https://"):
        logger.error(f"[Pinterest] Refusing to send non-HTTPS image_url to Make: {url!r}")
        return False
    # Try HEAD first (cheap), fall back to ranged GET (some hosts block HEAD).
    try:
        head = requests.head(url, timeout=timeout, allow_redirects=True)
        ctype = (head.headers.get("Content-Type", "") or "").lower()
        if head.status_code == 200 and ("image" in ctype or "octet" in ctype or ctype == ""):
            return True
        logger.warning(
            f"[Pinterest] image_url HEAD check: {head.status_code} "
            f"ctype={ctype or '?'} url={url[:100]} — trying ranged GET"
        )
    except Exception as e:
        logger.warning(f"[Pinterest] image_url HEAD check failed ({e}) — trying ranged GET")
    try:
        g = requests.get(url, timeout=timeout, stream=True,
                         headers={"Range": "bytes=0-1023", "User-Agent": "Mozilla/5.0"})
        ctype = (g.headers.get("Content-Type", "") or "").lower()
        if g.status_code in (200, 206) and ("image" in ctype or "octet" in ctype):
            g.close()
            return True
        logger.error(
            f"[Pinterest] image_url NOT publicly fetchable: "
            f"GET {g.status_code} ctype={ctype or '?'} url={url[:120]}"
        )
        g.close()
    except Exception as e:
        logger.error(f"[Pinterest] image_url NOT reachable: {e} url={url[:120]}")
    return False


def upload_via_make_webhook(image_path: str, title: str, description: str, link: str,
                            anime_name: str = "", board_id: str = "",
                            alt_text: str = "") -> str | bool:
    """
    Posts a pin via Make.com Custom Webhook -> Pinterest: Create a Pin module.
    RETURN CONTRACT (fixes false "Live on Pinterest!"):
      str   = hosted image_url on HTTP 2xx. The pin is NOT confirmed on
              Pinterest yet — the Make scenario may still fail.
              upload_to_pinterest() maps this to "queued" (never "live").
      False = failed. Do not claim live.
    Retries up to 3 times with exponential backoff on failure.
    board_id is passed in the payload so Make.com can route to the correct board.
    alt_text is passed for Pinterest visual search SEO (Pinterest supports it).
    """
    if not MAKE_WEBHOOK_URL:
        logger.error("[Make.com] MAKE_WEBHOOK_URL is not set in .env")
        return False

    # Step 1: Upload image to a public host to get a URL
    image_url = upload_image_to_host(image_path)
    if not image_url:
        logger.error("[Make.com] Could not get public image URL, aborting.")
        return False
    if not _verify_public_image_url(image_url):
        logger.error(f"[Make] image_url unreachable, aborting pin '{title}': {image_url[:120]}")
        return False

    # Step 2: POST to Make.com webhook with retry (3 attempts)
    # ── MANDATORY PRE-FLIGHT GATEKEEPER: Zero-404 Destination Verification ──
    pinterest_link = preflight_validate_destination(link, anime_name=anime_name, title=title)
    logger.info(f"[Make.com] Pre-flight destination verified: {pinterest_link[:80]}")

    # Resolve the board ID — auto-fetch if PINTEREST_BOARD_ID is empty
    resolved_board = _resolve_board_id(board_id)

    payload = {
        "title":       title[:100],
        "description": description[:500],
        "link":        pinterest_link,
        "image_url":   image_url,
        "board_id":    resolved_board,
        "alt_text":    alt_text[:500] if alt_text else "",
    }
    delays = [0, 5, 15]  # seconds between attempts
    for attempt, delay in enumerate(delays, 1):
        if delay:
            time.sleep(delay)
        try:
            res = requests.post(MAKE_WEBHOOK_URL, json=payload, timeout=20)
            if res.status_code in (200, 201, 204):
                logger.info(
                    f"[Make.com] Webhook accepted (HTTP {res.status_code}), pin queued for creation: '{title}'"
                    + (f" (attempt {attempt})" if attempt > 1 else "")
                )
                return image_url  # Return URL so caller can store it
            else:
                logger.warning(
                    f"[Make.com] Attempt {attempt}/3 failed: "
                    f"HTTP {res.status_code} {res.text[:100]}"
                )
        except Exception as e:
            logger.warning(f"[Make.com] Attempt {attempt}/3 exception: {e}")

    logger.error(f"[Make.com] All 3 attempts failed for '{title}'. Pin will retry next slot.")
    return False



def upload_to_pinterest(image_path, title, description, link, anime_name="",
                        board_id="", alt_text=""):
    """
    Master upload function.
    TRI-STATE RETURN: "live" = real pin on Pinterest (direct API),
    "queued" = Make webhook accepted (NOT yet on Pinterest), False = failed.
    Routes to Make.com webhook (instant public pins) if MAKE_WEBHOOK_URL is set,
    otherwise falls back to the official Pinterest API v5.
    board_id overrides PINTEREST_BOARD_ID for multi-board routing.
    alt_text is used for Pinterest visual search SEO (passed to both routes).
    When DRY_RUN=true in .env, logs the pin data instead of uploading.
    """
    filename = os.path.basename(image_path)

    # ── Duplicate check ───────────────────────────────────────────────────────
    if is_file_uploaded(filename):
        logger.info(f"Duplicate detected (persistent), skipping: {filename}")
        return False

    # ── Dry Run Mode ──────────────────────────────────────────────────────────
    if DRY_RUN:
        logger.info("[DRY RUN] ----------------------------------------")
        logger.info("[DRY RUN] Would upload pin:")
        logger.info(f"[DRY RUN]   Image    : {image_path}")
        logger.info(f"[DRY RUN]   Title    : {title}")
        logger.info(f"[DRY RUN]   Link     : {link}")
        logger.info(f"[DRY RUN]   Alt Text : {alt_text[:60]}..." if alt_text else "[DRY RUN]   Alt Text : (none)")
        logger.info(f"[DRY RUN]   Desc     : {description[:100]}...")
        if MAKE_WEBHOOK_URL:
            logger.info("[DRY RUN]   Method   : Make.com Webhook (instant public pins)")
        else:
            logger.info("[DRY RUN]   Method   : Pinterest API v5")
        logger.info("[DRY RUN] ----------------------------------------")
        mark_file_uploaded(filename, title, anime_name)  # Still track so no duplicates
        return True  # Return success so scheduler doesn't re-queue

    # ── Route: Make.com Webhook (preferred — no API approval needed) ──────────
    if MAKE_WEBHOOK_URL:
        image_url = upload_via_make_webhook(
            image_path, title, description, link,
            anime_name=anime_name, board_id=board_id, alt_text=alt_text
        )
        if image_url:
            mark_file_uploaded(filename, title, anime_name, image_url if isinstance(image_url, str) else "")
            return "queued"
        return False

    # ── Route: Official Pinterest API v5 (fallback) ───────────────────────────
    if not PINTEREST_ACCESS_TOKEN:
        logger.error("Pinterest credentials missing. Set PINTEREST_ACCESS_TOKEN or MAKE_WEBHOOK_URL.")
        return False

    try:
        logger.info(f"Uploading pin: '{title}'")

        # Pinterest API v5 endpoint for creating pins
        url = "https://api.pinterest.com/v5/pins"
        
        headers = {
            "Authorization": f"Bearer {PINTEREST_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        # Use board_id from router first, then fall back to config/dynamic lookup
        actual_board_id = board_id or PINTEREST_BOARD_ID
        if not actual_board_id or not actual_board_id.isdigit():
            actual_board_id = get_board_id_dynamically(headers)
            if not actual_board_id:
                logger.error("Could not determine Board ID.")
                return False
        
        # Step 1: We need to host the image somewhere public, or upload it as media.
        # Pinterest API v5 typically requires the image to be publicly accessible via URL
        # OR uploaded via their media upload endpoint first.
        # Since we are downloading from Telegram to local disk, we must use the media endpoint.
        
        # --- Media Upload Process ---
        # 1. Register media upload
        media_url = "https://api.pinterest.com/v5/media"
        media_data = {"media_type": "image"}
        media_res = requests.post(media_url, headers=headers, json=media_data)
        
        if media_res.status_code not in (200, 201):
            logger.error(f"Failed to register media: {media_res.text}")
            return False
            
        media_info = media_res.json()
        upload_id = media_info.get("media_id")
        upload_url = media_info.get("upload_url")
        upload_params = media_info.get("upload_parameters", {})
        
        # 2. Upload file to AWS S3 (via the pre-signed URL provided by Pinterest)
        with open(image_path, 'rb') as f:
            files = {'file': f}
            # upload_params contains necessary S3 form fields
            s3_res = requests.post(upload_url, data=upload_params, files=files)
            
        if s3_res.status_code not in (200, 204):
            logger.error(f"Failed to upload to S3: {s3_res.text}")
            return False
            
        # 3. Wait for processing (can take a few seconds)
        status_url = f"{media_url}/{upload_id}"
        max_retries = 5
        media_ready = False
        
        for _ in range(max_retries):
            status_res = requests.get(status_url, headers=headers)
            if status_res.status_code == 200:
                status = status_res.json().get("status")
                if status == "succeeded":
                    media_ready = True
                    break
                elif status == "failed":
                    logger.error("Media processing failed by Pinterest.")
                    return False
            time.sleep(2)
            
        if not media_ready:
            logger.error("Media processing timed out.")
            return False
            
        # ── MANDATORY PRE-FLIGHT GATEKEEPER: Zero-404 Destination Verification ──
        pinterest_link = preflight_validate_destination(link, anime_name=anime_name, title=title)
        logger.info(f"[Pinterest API] Pre-flight destination verified: {pinterest_link[:80]}")

        # --- Pin Creation ---
        pin_data = {
            "board_id": actual_board_id,
            "media_source": {
                "source_type": "media_id",
                "media_id": upload_id
            },
            "title":       title[:100],
            "description": description[:500],
            "link":        pinterest_link,
            "alt_text":    alt_text[:500] if alt_text else "",
        }
        
        res = requests.post(url, headers=headers, json=pin_data)
        
        if res.status_code in (200, 201):
            logger.info(f"Successfully uploaded pin: {title}")
            mark_file_uploaded(filename, title)  # Persist to SQLite
            return "live"
        else:
            logger.error(f"Failed to create pin: {res.text}")
            return False
            
    except Exception as e:
        logger.error(f"Error in Pinterest upload: {str(e)}")
        return False
