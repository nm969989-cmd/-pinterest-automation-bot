import os
import time
import requests
from config import PINTEREST_ACCESS_TOKEN, PINTEREST_BOARD_ID, DRY_RUN, MAKE_WEBHOOK_URL
from image_host import upload_image_to_host
from logger import get_logger
from database import is_file_uploaded, mark_file_uploaded
from amazon_search import preflight_validate_destination, is_direct_product_link

logger = get_logger(__name__)

# ── Pin verification after Make.com webhook ───────────────────────────────────
# How long (seconds) to wait after Make.com accepts before checking Pinterest.
# Make.com typically takes 5-20 s to forward and Pinterest to process.
_VERIFY_WAIT_SECS = 20
_VERIFY_RETRIES   = 5   # poll attempts after initial wait
_VERIFY_INTERVAL  = 10  # seconds between polling attempts

# Duplicate uploads are now tracked in SQLite (see database.py)
# Persistent across restarts — duplicates are prevented even after a crash.

# Module-level board ID cache so we only fetch once per session
_resolved_board_id: str = ""

# ── Make.com health tracking ──────────────────────────────────────────────────
# Counts consecutive Make.com 400 failures across posting slots.
# Resets to 0 on any success. Triggers a Telegram admin alert after 2 failures
# so the user knows to activate the scenario — before a full day is missed.
_make_consecutive_failures: int = 0
_make_alert_sent: bool = False  # Only send one alert per "down" period


def check_make_health() -> bool:
    """Probe the Make.com webhook with an empty OPTIONS/HEAD check.

    Since Make.com webhooks don't support HEAD, we send a minimal POST and
    check the response code. Returns True if the webhook is healthy (2xx),
    False if it's down (400 = inactive/queue full, other = error).
    Sends a Telegram admin alert if unhealthy.
    Called periodically by the scheduler (every 6 h) so problems surface
    BEFORE a posting slot is missed.
    """
    global _make_alert_sent
    if not MAKE_WEBHOOK_URL:
        return True  # Not using Make.com — nothing to check
    try:
        res = requests.post(MAKE_WEBHOOK_URL, json={"_health_check": True}, timeout=10)
        if res.status_code in (200, 201, 204):
            if _make_alert_sent:
                # Recovered — send a "back online" notification
                try:
                    from telegram_bot import notify_admin
                    notify_admin("✅ *Make.com Webhook Recovered*\n\nThe scenario is active again and accepting pins.")
                except Exception:
                    pass
            _make_alert_sent = False
            logger.info("[Make.com] Health check ✅ — webhook is active.")
            return True
        else:
            logger.warning(
                f"[Make.com] Health check ❌ — HTTP {res.status_code}: {res.text[:80]}. "
                f"Scenario may be INACTIVE. Go to make.com and activate it."
            )
            if not _make_alert_sent:
                _make_alert_sent = True
                try:
                    from telegram_bot import notify_admin
                    notify_admin(
                        "🚨 *Make.com Scenario is DOWN*\n\n"
                        f"Health check failed: HTTP {res.status_code} — scenario is likely *Inactive*.\n\n"
                        "📋 *Fix now (30 seconds):*\n"
                        "1. Open [make.com](https://make.com)\n"
                        "2. Click your *Integration Webhooks, Pinterest* scenario\n"
                        "3. Toggle the switch to **Active**\n"
                        "4. Click *Delete old data* when prompted\n\n"
                        "⚠️ Pins are currently NOT posting until you fix this."
                    )
                except Exception:
                    pass
            return False
    except Exception as e:
        logger.warning(f"[Make.com] Health check failed with exception: {e}")
        return False

def get_board_id_dynamically(headers):
    """Fetches the board ID dynamically using the API if the user didn't provide it"""
    try:
        url = "https://api.pinterest.com/v5/boards"
        res = requests.get(url, headers=headers, timeout=15)
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

    # 4. Fetch dynamically via Pinterest API only if token is provided
    if PINTEREST_ACCESS_TOKEN and PINTEREST_ACCESS_TOKEN.strip():
        headers = {
            "Authorization": f"Bearer {PINTEREST_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        }
        fetched = get_board_id_dynamically(headers)
        if fetched:
            _resolved_board_id = str(fetched)
            logger.info(f"[Pinterest] Auto-fetched board_id from API: {_resolved_board_id} (cached for session)")
            return _resolved_board_id

    if MAKE_WEBHOOK_URL:
        logger.info("[Pinterest] Using Make.com scenario default board.")
    else:
        logger.warning(
            "[Pinterest] PINTEREST_BOARD_ID is empty. "
            "Set PINTEREST_BOARD_ID in .env to specify a board."
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
        if g.status_code in (200, 206) and ("image" in ctype or "octet" in ctype or ctype == ""):
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


def verify_pin_created(title: str, board_id: str = "", wait_secs: int = _VERIFY_WAIT_SECS) -> bool:
    """
    Polls Pinterest API to confirm a pin with the given title was actually created
    on the board after Make.com accepted the webhook.

    Returns True if the pin is confirmed live on Pinterest.
    Returns False if not found within the retry window (pin may still be processing).

    Requires PINTEREST_ACCESS_TOKEN in .env to work. If not set, returns False
    (can't verify — caller should treat as "queued", not "live").
    """
    if not PINTEREST_ACCESS_TOKEN or not PINTEREST_ACCESS_TOKEN.strip():
        logger.debug("[PinVerify] PINTEREST_ACCESS_TOKEN not set — skipping API verification (using Make.com).")
        return True

    resolved_board = board_id or PINTEREST_BOARD_ID
    if not resolved_board:
        logger.debug("[PinVerify] No board_id available — skipping API verification (using Make.com default board).")
        return True

    headers = {
        "Authorization": f"Bearer {PINTEREST_ACCESS_TOKEN}",
        "Content-Type":  "application/json",
    }

    logger.info(
        f"[PinVerify] Waiting {wait_secs}s for Make.com to forward pin to Pinterest..."
    )
    time.sleep(wait_secs)

    title_lower = title.strip().lower()[:80]  # Pinterest truncates titles to 100 chars

    for attempt in range(1, _VERIFY_RETRIES + 1):
        try:
            url = (
                f"https://api.pinterest.com/v5/boards/{resolved_board}/pins"
                f"?page_size=10&sort_by=created_at"
            )
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                pins = res.json().get("items", [])
                for pin in pins:
                    pin_title = (pin.get("title") or "").strip().lower()
                    # Match on first 60 chars to handle minor truncation differences
                    if pin_title[:60] == title_lower[:60] or title_lower[:60] in pin_title:
                        pin_id  = pin.get("id", "?")
                        pin_url = f"https://www.pinterest.com/pin/{pin_id}/"
                        logger.info(
                            f"[PinVerify] ✅ Pin CONFIRMED LIVE on Pinterest! "
                            f"id={pin_id} url={pin_url}"
                        )
                        return True
                logger.info(
                    f"[PinVerify] Attempt {attempt}/{_VERIFY_RETRIES}: pin not yet visible "
                    f"(checked {len(pins)} recent pins)."
                )
            elif res.status_code == 401:
                logger.error(
                    "[PinVerify] ❌ Pinterest API returned 401 Unauthorized. "
                    "Your PINTEREST_ACCESS_TOKEN may be expired. "
                    "Renew it at developers.pinterest.com."
                )
                return False
            elif res.status_code == 403:
                logger.error(
                    "[PinVerify] ❌ Pinterest API returned 403 Forbidden. "
                    "Token may lack 'boards:read' or 'pins:read' scopes."
                )
                return False
            else:
                logger.warning(
                    f"[PinVerify] Attempt {attempt}/{_VERIFY_RETRIES}: "
                    f"Pinterest API returned HTTP {res.status_code}: {res.text[:80]}"
                )
        except Exception as e:
            logger.warning(f"[PinVerify] Attempt {attempt}/{_VERIFY_RETRIES} exception: {e}")

        if attempt < _VERIFY_RETRIES:
            time.sleep(_VERIFY_INTERVAL)

    logger.warning(
        f"[PinVerify] ⚠️ Pin NOT confirmed after {_VERIFY_RETRIES} attempts. "
        f"Possible causes: (1) Make.com scenario inactive/failed, "
        f"(2) Pinterest access token expired, "
        f"(3) Pin is in moderation queue (new account). "
        f"Check make.com History tab and your Pinterest 'Created' tab manually."
    )
    return False


def upload_via_make_webhook(image_path: str, title: str, description: str, link: str,
                            anime_name: str = "", board_id: str = "",
                            alt_text: str = "") -> str | bool:
    """
    Posts a pin via Make.com Custom Webhook -> Pinterest: Create a Pin module.
    RETURN CONTRACT (tri-state for accurate status reporting):
      (image_url, True)  = webhook accepted AND pin confirmed live on Pinterest.
      (image_url, False) = webhook accepted but pin NOT confirmed (queued/pending).
      False              = webhook failed entirely. Do not claim live.
    Retries up to 3 times with exponential backoff on failure.
    board_id is passed in the payload so Make.com can route to the correct board.
    alt_text is passed for Pinterest visual search SEO (Pinterest supports it).
    After Make.com accepts, verify_pin_created() polls Pinterest API to confirm
    the pin is actually visible — fixing the invisible pin bug.
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
        "title":           title[:100],
        "description":     description[:500],
        "link":            pinterest_link,
        "destination_url": pinterest_link,
        "image_url":       image_url,
        "url":             image_url,           # Required by Make.com Pinterest "Create a Pin" module
        "media_url":       image_url,
        "photo_url":       image_url,
        "image":           image_url,
        "alt_text":        alt_text[:500] if alt_text else "",
    }
    # Only send board_id if non-empty, so Make.com uses its configured default board
    if resolved_board:
        payload["board_id"] = resolved_board

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
                # ── Verify pin on Pinterest only if developer API token is configured ──
                if PINTEREST_ACCESS_TOKEN and PINTEREST_ACCESS_TOKEN.strip():
                    resolved_board = _resolve_board_id(board_id)
                    pin_confirmed = verify_pin_created(title, board_id=resolved_board)
                    if pin_confirmed:
                        logger.info(f"[Make.com] Pin CONFIRMED LIVE on Pinterest: '{title}'")
                        try:
                            import threading as _threading
                            from ghost_pin_checker import run_ghost_check_and_notify
                            _ghost_thread = _threading.Thread(
                                target=run_ghost_check_and_notify,
                                args=(f"https://www.pinterest.com/search/pins/?q={title[:30]}", title),
                                daemon=True,
                                name="GhostPinCheck",
                            )
                            _ghost_thread.start()
                        except Exception as _ge:
                            logger.debug(f"[Make.com] Ghost check launch failed (non-critical): {_ge}")
                    else:
                        logger.warning(
                            f"[Make.com] Pin not yet visible via Pinterest API. "
                            f"Check make.com → History tab if the scenario encountered an error."
                        )
                else:
                    # Make.com webhook mode (no Pinterest developer API token needed)
                    logger.info(f"[Make.com] Pin submitted successfully to Make.com: '{title}'")
                    pin_confirmed = True

                return (image_url, pin_confirmed)
            elif res.status_code == 400:
                # HTTP 400 = scenario is INACTIVE or task queue is full
                logger.warning(
                    f"[Make.com] Attempt {attempt}/3 failed: HTTP 400 — "
                    f"scenario is likely INACTIVE or queue is full. "
                    f"Go to make.com and activate your Pinterest scenario. "
                    f"Response: {res.text[:100]}"
                )
            else:
                logger.warning(
                    f"[Make.com] Attempt {attempt}/3 failed: "
                    f"HTTP {res.status_code} {res.text[:100]}"
                )
        except Exception as e:
            logger.warning(f"[Make.com] Attempt {attempt}/3 exception: {e}")

    logger.error(f"[Make.com] All 3 attempts failed for '{title}'. Pin will retry next slot.")
    # Increment persistent failure counter so health checker knows we're degraded
    global _make_consecutive_failures, _make_alert_sent
    _make_consecutive_failures += 1
    if _make_consecutive_failures >= 2 and not _make_alert_sent:
        _make_alert_sent = True
        try:
            from telegram_bot import notify_admin
            notify_admin(
                f"🚨 *Make.com Down — {_make_consecutive_failures} Consecutive Failures*\n\n"
                "Pins are not posting. Scenario is likely *Inactive*.\n\n"
                "📋 *Fix (30 seconds):*\n"
                "1. Open [make.com](https://make.com)\n"
                "2. Click your *Integration Webhooks, Pinterest* scenario\n"
                "3. Toggle to **Active**\n"
                "4. Click *Delete old data*\n\n"
                "Bot will retry automatically on next slot."
            )
        except Exception:
            pass
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
        result = upload_via_make_webhook(
            image_path, title, description, link,
            anime_name=anime_name, board_id=board_id, alt_text=alt_text
        )
        if result is not False:
            # result is (image_url, pin_confirmed) tuple
            image_url, pin_confirmed = result
            # Success — reset failure counter (must use global, not local variable)
            global _make_consecutive_failures, _make_alert_sent
            _make_consecutive_failures = 0
            _make_alert_sent = False
            mark_file_uploaded(filename, title, anime_name, image_url if isinstance(image_url, str) else "")
            # Return "live" only when verified by Pinterest API, else "queued"
            return "live" if pin_confirmed else "queued"

        # Make.com failed all 3 attempts — notify admin and try direct Pinterest API
        logger.warning(
            f"[Pinterest] Make.com failed for '{title}'. "
            f"{'Falling back to Pinterest API v5.' if PINTEREST_ACCESS_TOKEN else 'No PINTEREST_ACCESS_TOKEN — pin will retry next slot.'}"
        )
        try:
            from telegram_bot import notify_admin
            notify_admin(
                "⚠️ *Make.com Webhook Down*\n\n"
                "Make.com rejected the last pin upload (HTTP 400 — scenario may be *Inactive* or queue is full).\n\n"
                "📋 *Action Required:*\n"
                "1. Go to [make.com](https://make.com) → your Pinterest scenario\n"
                "2. Toggle it to **Active** if it's paused\n"
                "3. Clear the queue if 50+ records are waiting\n\n"
                f"Pin affected: *{title[:60]}*\n"
                + ("✅ Retrying via direct Pinterest API v5..." if PINTEREST_ACCESS_TOKEN else "❌ No PINTEREST_ACCESS_TOKEN set — pin will retry next slot.")
            )
        except Exception:
            pass

        if not PINTEREST_ACCESS_TOKEN:
            return False
        # else: fall through to direct Pinterest API v5 below


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
        media_res = requests.post(media_url, headers=headers, json=media_data, timeout=30)
        
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
            s3_res = requests.post(upload_url, data=upload_params, files=files, timeout=60)
            
        if s3_res.status_code not in (200, 204):
            logger.error(f"Failed to upload to S3: {s3_res.text}")
            return False
            
        # 3. Wait for processing (can take a few seconds)
        status_url = f"{media_url}/{upload_id}"
        max_retries = 5
        media_ready = False
        
        for _ in range(max_retries):
            status_res = requests.get(status_url, headers=headers, timeout=15)
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
        
        res = requests.post(url, headers=headers, json=pin_data, timeout=30)
        
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
