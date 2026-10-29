"""
auth_preflight.py — Smart Auth Preflight & Dynamic Platform Toggling
=====================================================================
Proactively tests credentials for syndication platforms on boot.
If a platform's token is revoked, expired, or unauthenticated (HTTP 401),
it safely auto-disables the platform in memory (config.PLATFORM_ENABLED = False).

Benefits:
  - Zero ❌ in Telegram notifications: Dead accounts never attempt to post.
  - Zero noisy error logs on Render.
  - Auto-reactivates seamlessly when user adds a fresh token and restarts.
"""

from logger import get_logger
import config

logger = get_logger(__name__)


def run_auth_preflight() -> dict:
    """
    Runs authentication preflight checks for external platforms.
    Dynamically disables platforms with expired or invalid tokens.

    Returns:
        dict: Summary of preflight status for each platform.
    """
    status_report = {}

    # 1. Are.na Preflight
    if getattr(config, "ARENA_ENABLED", False):
        try:
            from arena_uploader import verify_arena_token
            if not verify_arena_token():
                logger.warning(
                    "[Preflight] Are.na access token is invalid or expired (HTTP 401). "
                    "Auto-disabling ARENA_ENABLED = False to eliminate failure alerts. "
                    "To re-enable Are.na, add a fresh ARENA_ACCESS_TOKEN in .env or Render."
                )
                config.ARENA_ENABLED = False
                status_report["arena"] = "auto_disabled_401"
            else:
                logger.info("[Preflight] Are.na authentication verified [OK]")
                status_report["arena"] = "verified"
        except Exception as e:
            logger.warning(f"[Preflight] Are.na check error: {e}")
            status_report["arena"] = f"error: {e}"
    else:
        status_report["arena"] = "disabled_by_config"

    # 2. Pixelfed Preflight
    if getattr(config, "PIXELFED_ENABLED", False):
        try:
            from pixelfed_uploader import verify_pixelfed_token
            if not verify_pixelfed_token():
                logger.warning(
                    "[Preflight] Pixelfed access token is invalid, pending, or unauthenticated (HTTP 401). "
                    "Auto-disabling PIXELFED_ENABLED = False to eliminate failure alerts. "
                    "To re-enable Pixelfed, add an approved PIXELFED_ACCESS_TOKEN in .env or Render."
                )
                config.PIXELFED_ENABLED = False
                status_report["pixelfed"] = "auto_disabled_401"
            else:
                logger.info("[Preflight] Pixelfed authentication verified [OK]")
                status_report["pixelfed"] = "verified"
        except Exception as e:
            logger.warning(f"[Preflight] Pixelfed check error: {e}")
            status_report["pixelfed"] = f"error: {e}"
    else:
        status_report["pixelfed"] = "disabled_by_config"

    return status_report


if __name__ == "__main__":
    print("Running Smart Auth Preflight...")
    results = run_auth_preflight()
    print("Preflight Results:", results)
    print("ARENA_ENABLED:", getattr(config, "ARENA_ENABLED", False))
    print("PIXELFED_ENABLED:", getattr(config, "PIXELFED_ENABLED", False))

