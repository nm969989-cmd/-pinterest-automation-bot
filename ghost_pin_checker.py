"""
ghost_pin_checker.py — Public "Ghost Pin" Inspector

Checks whether a Pinterest pin is actually visible to the public (other accounts,
unauthenticated visitors). A pin can show in your own account but be INVISIBLE to
everyone else when Pinterest's algorithm:
  1. Flags it as an affiliate-link spam pin
  2. Detects a duplicate image fingerprint across Pinterest
  3. Puts it in a content-moderation queue (new accounts)
  4. The board is set to "Secret"

Detection method:
  - Fetches the pin URL as an anonymous/unauthenticated HTTP client (no session cookies).
  - If Pinterest returns 200 + pin title in HTML → pin is PUBLIC ✅
  - If Pinterest redirects to /login, returns 404, or omits the pin title → GHOSTED ⚠️
  - Uses a neutral User-Agent so Pinterest doesn't block the check itself.

Called automatically by pinterest_uploader.py after pin verification.
Also available as a Telegram command: /verify_public
"""

import re
import time
import requests
from logger import get_logger

logger = get_logger(__name__)

# Seconds to wait before checking (let Pinterest finish indexing)
_GHOST_CHECK_WAIT   = 30
_GHOST_CHECK_TRIES  = 3
_GHOST_CHECK_DELAY  = 20  # between retries

# Pinterest always embeds the pin title in <title> and og:title meta tags
_TITLE_RE   = re.compile(r'<title[^>]*>([^<]{5,})</title>', re.IGNORECASE)
_OG_TITLE_RE = re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']{3,})["\']', re.IGNORECASE)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def check_pin_public(pin_url: str, expected_title: str = "", wait: bool = True) -> dict:
    """
    Unauthenticated public visibility check for a Pinterest pin.

    Args:
        pin_url:        Full Pinterest pin URL, e.g. https://www.pinterest.com/pin/123456/
        expected_title: Title text to confirm it's the right pin (optional but recommended).
        wait:           If True, waits _GHOST_CHECK_WAIT seconds before first attempt
                        (let Pinterest finish indexing before we check).

    Returns a dict:
        {
          "visible":  bool,        # True = public and visible
          "status":   str,         # "public" | "ghosted" | "login_wall" | "not_found" | "error"
          "message":  str,         # Human-readable explanation
          "pin_url":  str,
          "attempts": int,
        }
    """
    result = {
        "visible": False,
        "status":  "error",
        "message": "Check not started",
        "pin_url": pin_url,
        "attempts": 0,
    }

    if not pin_url or "pinterest" not in pin_url:
        result["message"] = "Invalid Pinterest URL"
        return result

    if wait:
        logger.info(
            f"[GhostCheck] Waiting {_GHOST_CHECK_WAIT}s before public visibility check..."
        )
        time.sleep(_GHOST_CHECK_WAIT)

    for attempt in range(1, _GHOST_CHECK_TRIES + 1):
        result["attempts"] = attempt
        try:
            resp = requests.get(
                pin_url,
                headers=_HEADERS,
                timeout=20,
                allow_redirects=True,
            )
            final_url = resp.url.lower()

            # Redirect to login page → Pinterest blocked anonymous access → GHOSTED
            if "/login" in final_url or "/auth/login" in final_url:
                logger.warning(
                    f"[GhostCheck] Attempt {attempt}/{_GHOST_CHECK_TRIES}: "
                    f"Redirected to login page → pin is NOT visible to the public."
                )
                result.update({
                    "visible": False,
                    "status":  "login_wall",
                    "message": (
                        "⚠️ Pin is NOT visible publicly. Pinterest is redirecting to login — "
                        "board may be Secret or the pin was removed/shadowbanned."
                    ),
                })
                # No point retrying a login wall — it won't change
                return result

            if resp.status_code == 404:
                logger.warning(
                    f"[GhostCheck] Attempt {attempt}/{_GHOST_CHECK_TRIES}: "
                    f"404 Not Found — pin may still be indexing or was deleted."
                )
                result.update({
                    "visible": False,
                    "status":  "not_found",
                    "message": (
                        "⚠️ Pin returned 404. It may still be indexing (check again in a few minutes) "
                        "or was not created on Pinterest."
                    ),
                })

            elif resp.status_code == 200:
                html = resp.text

                # Confirm pin title is present in the HTML (positive signal)
                title_match = _TITLE_RE.search(html) or _OG_TITLE_RE.search(html)
                page_title  = title_match.group(1).strip() if title_match else ""

                # Pinterest login page title is always "Log in" / "Pinterest"
                login_titles = {"log in", "pinterest – log in", "sign up", "login"}
                if page_title.lower() in login_titles or "log in" in page_title.lower():
                    logger.warning(
                        f"[GhostCheck] Attempt {attempt}: Page title is login page title "
                        f"({page_title!r}) — ghost pin confirmed."
                    )
                    result.update({
                        "visible": False,
                        "status":  "login_wall",
                        "message": (
                            "⚠️ Ghost pin detected. Page returned 200 but shows the login page "
                            "— Pinterest is requiring login to view this pin (shadowbanned or Secret board)."
                        ),
                    })
                    return result

                # Optional: confirm the expected title appears somewhere in the page
                title_confirmed = True
                if expected_title:
                    title_lower = expected_title.lower()[:50]
                    html_lower  = html.lower()
                    title_confirmed = title_lower[:30] in html_lower

                if page_title and title_confirmed:
                    logger.info(
                        f"[GhostCheck] ✅ Pin is PUBLIC and visible! "
                        f"Page title: {page_title!r} | URL: {pin_url}"
                    )
                    result.update({
                        "visible": True,
                        "status":  "public",
                        "message": (
                            f"✅ Pin is PUBLIC and visible to everyone. "
                            f"Page title confirmed: \"{page_title[:60]}\""
                        ),
                    })
                    return result
                elif not page_title:
                    logger.warning(
                        f"[GhostCheck] Attempt {attempt}: 200 OK but no title found in HTML — "
                        f"may be a JS-rendered ghost or still indexing."
                    )
                    result.update({
                        "visible": False,
                        "status":  "ghosted",
                        "message": (
                            "⚠️ Possible ghost pin. Page returned 200 but pin title was not "
                            "found in HTML — Pinterest may be hiding it or it's still indexing."
                        ),
                    })
                else:
                    logger.info(
                        f"[GhostCheck] Attempt {attempt}: Pin is public (page title: {page_title!r}) "
                        f"but expected title not matched — may be a different pin or title was truncated."
                    )
                    result.update({
                        "visible": True,
                        "status":  "public",
                        "message": (
                            f"✅ Pin page is public. Note: Expected title not exactly matched "
                            f"(Pinterest may have truncated it). Page title: \"{page_title[:60]}\""
                        ),
                    })
                    return result
            else:
                logger.warning(
                    f"[GhostCheck] Attempt {attempt}: HTTP {resp.status_code} for {pin_url}"
                )
                result.update({
                    "visible": False,
                    "status":  "error",
                    "message": f"HTTP {resp.status_code} — unexpected response from Pinterest.",
                })

        except Exception as e:
            logger.warning(f"[GhostCheck] Attempt {attempt} exception: {e}")
            result.update({
                "visible": False,
                "status":  "error",
                "message": f"Network error during check: {e}",
            })

        if attempt < _GHOST_CHECK_TRIES:
            time.sleep(_GHOST_CHECK_DELAY)

    return result


def check_pin_by_id(pin_id: str, expected_title: str = "") -> dict:
    """Convenience wrapper — builds the URL from a pin ID."""
    url = f"https://www.pinterest.com/pin/{pin_id}/"
    return check_pin_public(url, expected_title=expected_title)


def run_ghost_check_and_notify(pin_url: str, title: str, board_url: str = "") -> bool:
    """
    Full pipeline: check public visibility and notify admin via Telegram.
    Returns True if pin is publicly visible, False if ghosted/hidden.
    Called by pinterest_uploader after a pin is confirmed created.
    """
    logger.info(f"[GhostCheck] Starting public visibility check for: {pin_url}")
    result = check_pin_public(pin_url, expected_title=title)

    if result["visible"]:
        logger.info(f"[GhostCheck] ✅ Pin is publicly visible: {pin_url}")
        return True

    # Pin is ghosted — notify admin with actionable steps
    status_emoji = {
        "login_wall": "🔒",
        "not_found":  "❓",
        "ghosted":    "👻",
        "error":      "⚠️",
    }.get(result["status"], "⚠️")

    try:
        from telegram_bot import notify_admin
        notify_admin(
            f"{status_emoji} *Ghost Pin Detected!*\n\n"
            f"Pin: *{title[:60]}*\n"
            f"Status: `{result['status']}`\n"
            f"Detail: {result['message']}\n\n"
            f"🔗 [View Pin]({pin_url})"
            + (f" | [View Board]({board_url})" if board_url else "") +
            f"\n\n"
            f"🛠️ *Possible Fixes:*\n"
            f"1. Check board is set to **Public** (not Secret)\n"
            f"2. Replace direct Amazon affiliate link with your bridge page `/p/<code>` URL\n"
            f"3. Make sure the image has a **2:3 aspect ratio** (Pinterest favors vertical)\n"
            f"4. Wait 24–72h — new accounts have delayed public indexing\n"
            f"5. Pin the same image manually from your browser to reset the filter"
        )
    except Exception as e:
        logger.warning(f"[GhostCheck] Failed to send Telegram ghost notification: {e}")

    return False
