"""
circuit_breaker.py — Automated Platform Circuit Breaker & Smart Cooldown Engine
==============================================================================
Protects all platform accounts against cascading API bans, rate limits, and
account suspensions.

When an external platform returns HTTP 429 (Too Many Requests), HTTP 403 (Forbidden),
or encounters repeated failure spikes:
  1. Trips the circuit breaker for that specific platform.
  2. Enforces a protective cooldown (default 6 hours, configurable).
  3. Pauses outbound posting to that single platform while letting all other
     healthy platforms continue uninterrupted.
  4. Automatically resumes posting once the cooldown expires, or upon manual reset.
  5. State is persisted in SQLite bot_metadata (survives restarts/redeploys).
"""

import time
import datetime
from logger import get_logger

logger = get_logger(__name__)

# Default cooldown duration in hours when an API trips the breaker
DEFAULT_COOLDOWN_HOURS = 6.0


def _get_key(platform: str) -> str:
    return f"circuit_breaker_{platform.lower().strip()}"


def is_cooling_down(platform: str) -> tuple[bool, str, float]:
    """
    Checks if a platform is currently in a protective cooldown.

    Returns:
        (is_active: bool, reason: str, remaining_hours: float)
    """
    try:
        from database import get_metadata
        key = _get_key(platform)
        val = get_metadata(key, "").strip()
        if not val:
            return False, "", 0.0

        # Stored format: "<expiry_timestamp>|<reason>"
        parts = val.split("|", 1)
        if len(parts) < 2:
            return False, "", 0.0

        expiry_ts = float(parts[0])
        reason = parts[1]
        now = time.time()

        if now < expiry_ts:
            remaining_secs = expiry_ts - now
            remaining_hours = round(remaining_secs / 3600.0, 1)
            return True, reason, remaining_hours
        else:
            # Cooldown expired — clear it automatically
            clear_breaker(platform)
            logger.info(f"[CircuitBreaker] Cooldown expired for '{platform}'. Platform resumed automatically.")
            return False, "", 0.0

    except Exception as e:
        logger.debug(f"[CircuitBreaker] Lookup error for '{platform}': {e}")
        return False, "", 0.0


def trip_breaker(platform: str, reason: str, cooldown_hours: float = DEFAULT_COOLDOWN_HOURS) -> bool:
    """
    Trips the circuit breaker for a platform, pausing it for the specified duration.
    Persists to SQLite bot_metadata and sends an alert to Telegram admin.
    """
    try:
        from database import set_metadata
        plat = platform.lower().strip()
        expiry_ts = time.time() + (cooldown_hours * 3600)
        reason_clean = reason.replace("|", " - ")[:120]
        val = f"{expiry_ts}|{reason_clean}"

        set_metadata(_get_key(plat), val)

        ist_resume = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30) + datetime.timedelta(hours=cooldown_hours)
        resume_str = ist_resume.strftime("%I:%M %p IST")

        logger.warning(
            f"[CircuitBreaker] Tripped for '{plat.upper()}' for {cooldown_hours}h! "
            f"Reason: {reason_clean}. Resumes at: {resume_str}. Pausing requests to protect account."
        )

        # Notify admin on Telegram
        try:
            from telegram_bot import notify_admin
            notify_admin(
                f"🛡️ *Platform Circuit Breaker Activated*\n\n"
                f"• *Platform*: `{plat.upper()}`\n"
                f"• *Reason*: `{reason_clean}`\n"
                f"• *Action*: Pausing uploads for *{cooldown_hours} hours* to protect your account against bans.\n"
                f"• *Auto-Resumes*: `{resume_str}`\n\n"
                f"_Other platforms will continue posting normally._"
            )
        except Exception:
            pass

        return True

    except Exception as e:
        logger.error(f"[CircuitBreaker] Failed to trip breaker for '{platform}': {e}")
        return False


def clear_breaker(platform: str) -> bool:
    """Clears the circuit breaker cooldown for a platform, restoring immediate posting."""
    try:
        from database import set_metadata
        plat = platform.lower().strip()
        set_metadata(_get_key(plat), "")
        logger.info(f"[CircuitBreaker] Cleared circuit breaker for '{plat}'.")
        return True
    except Exception as e:
        logger.debug(f"[CircuitBreaker] Failed to clear breaker for '{platform}': {e}")
        return False


def get_all_cooldowns() -> dict:
    """
    Returns a dictionary of all currently active platform cooldowns.
    e.g. {'bluesky': {'reason': 'HTTP 429', 'remaining_hours': 4.2}}
    """
    active = {}
    known_platforms = [
        "pinterest", "arena", "bluesky", "mastodon",
        "pixelfed", "raindrop", "freeimage", "imghippo"
    ]
    for p in known_platforms:
        cooling, reason, rem = is_cooling_down(p)
        if cooling:
            active[p] = {
                "reason": reason,
                "remaining_hours": rem,
            }
    return active


if __name__ == "__main__":
    print("Testing Circuit Breaker...")
    trip_breaker("test_platform", "HTTP 429 Rate Limit Exceeded", cooldown_hours=2.0)
    cooling, r, rem = is_cooling_down("test_platform")
    print(f"Is cooling down: {cooling} (Remaining: {rem}h, Reason: {r})")
    all_cool = get_all_cooldowns()
    print(f"All active: {all_cool}")
    clear_breaker("test_platform")
    cooling_after, _, _ = is_cooling_down("test_platform")
    print(f"After clear: {cooling_after}")
