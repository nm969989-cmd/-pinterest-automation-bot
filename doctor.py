"""
doctor.py - Automated 3-Day Health & System Diagnostic Engine
Runs thorough diagnostics on Render resources, SQLite DB, Webhook, Amazon Tag, and Queue.
Auto-fires every 3 days at 10:00 AM IST and on-demand via /doctor Telegram command.
"""

import os
import sys
import time
import datetime
from logger import get_logger

logger = get_logger(__name__)

def run_full_system_diagnostic() -> dict:
    """
    Runs a comprehensive 8-point diagnostic check across the entire bot stack.
    Returns a structured dictionary of results and warnings.
    """
    import config
    from database import get_3day_stats, _get_conn
    from crash_protection import get_memory_mb, get_disk_usage_mb
    import pa_api
    from circuit_breaker import is_cooling_down, get_all_cooldowns

    warnings = []
    
    # 1. Memory Usage (Render 512MB limit)
    mem_mb = round(get_memory_mb(), 1)
    mem_status = "🟢 Safe"
    if mem_mb > 420:
        mem_status = "🔴 Critical"
        warnings.append(f"High memory usage: {mem_mb} MB (Limit: 512MB)")
    elif mem_mb > 350:
        mem_status = "🟡 Moderate"
        warnings.append(f"Elevated memory usage: {mem_mb} MB")

    # 2. Disk Usage
    disk_mb = round(get_disk_usage_mb("downloads") + get_disk_usage_mb("processed"), 1)
    disk_status = "🟢 Clean"
    if disk_mb > 250:
        disk_status = "🟡 Warning"
        warnings.append(f"Disk temp files size: {disk_mb} MB")

    # 3. Database Health & Integrity
    db_ok = False
    try:
        with _get_conn() as conn:
            check_res = conn.execute("PRAGMA quick_check;").fetchone()
            db_ok = check_res and check_res[0] == "ok"
    except Exception as e:
        warnings.append(f"Database error: {e}")
    db_status = "🟢 Healthy (Integrity OK)" if db_ok else "🔴 DB Error"

    # 4. 3-Day Activity & Queue Stats
    stats = get_3day_stats()
    queue_counts = stats["queue"]
    days_buffer = round((queue_counts["new"] + queue_counts["backlog"]) / max(1, config.MAX_POSTS_PER_DAY), 1)

    if queue_counts["total"] == 0:
        warnings.append("Queue is empty. Bot needs new images from channels.")

    if stats["failed_retries"] > 0:
        warnings.append(f"{stats['failed_retries']} pin(s) experienced upload retries.")

    # 5. Make.com Webhook & Board Status
    webhook_set = bool(config.MAKE_WEBHOOK_URL and config.MAKE_WEBHOOK_URL.startswith("http"))
    webhook_status = "🟢 Connected & Active" if webhook_set else "🟡 Using Direct API / Dry-run"

    board_id = config.PINTEREST_BOARD_ID or config.BOARD_ID_GENERAL
    if board_id:
        board_status = f"🟢 Configured ({board_id})"
    elif webhook_set:
        board_status = "🟡 Uses Make.com Default Board (PINTEREST_BOARD_ID empty)"
    else:
        board_status = "🔴 Missing Board ID"
        warnings.append("No Pinterest Board ID configured. Pins may fail to upload.")

    # 6. Amazon Affiliate & PA-API Status
    tag = config.AMAZON_AFFILIATE_TAG or "Not set"
    pa_api_active = pa_api.is_available()
    amazon_status = f"🟢 Active ({tag})" if tag else "🔴 Missing Tag"
    pa_api_status = "🟢 Official API Active" if pa_api_active else "⚪ HTML Scraper (Standard)"

    # 7. Click Tracking Status
    click_track_active = bool(config.APP_BASE_URL)
    tracker_status = "🟢 Tracking Active" if click_track_active else "⚪ Direct Amazon Links"

    # 8. Are.na Cross-Post Status
    arena_status = "⚪ Disabled"
    try:
        if getattr(config, "ARENA_ENABLED", False):
            from circuit_breaker import is_cooling_down
            cooling, c_reason, c_rem = is_cooling_down("arena")
            if cooling:
                arena_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
            elif config.ARENA_ACCESS_TOKEN:
                from arena_uploader import verify_arena_token, get_arena_channel_info
                token_ok = verify_arena_token()
                if token_ok:
                    info = get_arena_channel_info()
                    if info:
                        used = info.get("length", 0)
                        remaining = max(0, 200 - used)
                        arena_status = f"🟢 Active ({used}/200 blocks, {remaining} free)"
                        if remaining < 20:
                            warnings.append(f"Are.na block quota low: only {remaining} blocks left (Guest plan, 200 max). Upgrade to Premium at $7/mo for unlimited.")
                    else:
                        arena_status = "🟡 Token OK but channel not found"
                        warnings.append("Are.na channel not found — check ARENA_CHANNEL_SLUG in .env")
                else:
                    arena_status = "🔴 Token Invalid"
                    warnings.append("Are.na token is invalid — check ARENA_ACCESS_TOKEN in .env")
            else:
                arena_status = "🔴 Token Missing"
                warnings.append("ARENA_ENABLED=true but ARENA_ACCESS_TOKEN is empty in .env")
    except Exception as _arena_e:
        arena_status = f"🟡 Check failed ({_arena_e})"


    # 10. Bluesky Cross-Post Status
    bluesky_status = "⚪ Disabled"
    try:
        cooling, c_reason, c_rem = is_cooling_down("bluesky")
        if cooling:
            bluesky_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
        elif config.BLUESKY_ENABLED and config.BLUESKY_HANDLE and config.BLUESKY_APP_PASSWORD:
            from bluesky_uploader import verify_bluesky_credentials, get_bluesky_profile_info
            bsky_ok = verify_bluesky_credentials()
            if bsky_ok:
                binfo = get_bluesky_profile_info()
                if binfo:
                    bluesky_status = f"🟢 Active (@{binfo['handle']}, {binfo['posts']:,} posts)"
                else:
                    bluesky_status = "🟡 Session OK — profile info unavailable"
            else:
                bluesky_status = "🔴 Credentials Invalid"
                warnings.append("Bluesky login failed — check BLUESKY_HANDLE / BLUESKY_APP_PASSWORD in .env")
        elif config.BLUESKY_ENABLED and (not config.BLUESKY_HANDLE or not config.BLUESKY_APP_PASSWORD):
            bluesky_status = "🔴 Credentials Missing"
            warnings.append("BLUESKY_ENABLED=true but BLUESKY credentials are missing in .env")
    except Exception as _bsky_e:
        bluesky_status = f"🟡 Check failed ({_bsky_e})"

    # 11. Raindrop.io Cross-Post Status
    raindrop_status = "⚪ Disabled"
    try:
        cooling, c_reason, c_rem = is_cooling_down("raindrop")
        if cooling:
            raindrop_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
        elif config.RAINDROP_ENABLED and config.RAINDROP_ACCESS_TOKEN:
            from raindrop_uploader import verify_raindrop_token, get_raindrop_collection_info
            drop_ok = verify_raindrop_token()
            if drop_ok:
                rinfo = get_raindrop_collection_info()
                cnt = rinfo.get("count", 0) if rinfo else "?"
                raindrop_status = f"🟢 Active ({cnt} bookmarks, Collection {config.RAINDROP_COLLECTION_ID})"
            else:
                raindrop_status = "🔴 Token Invalid"
                warnings.append("Raindrop token is invalid — check RAINDROP_ACCESS_TOKEN in .env")
        elif config.RAINDROP_ENABLED and not config.RAINDROP_ACCESS_TOKEN:
            raindrop_status = "🔴 Token Missing"
            warnings.append("RAINDROP_ENABLED=true but RAINDROP_ACCESS_TOKEN is missing in .env")
    except Exception as _drop_e:
        raindrop_status = f"🟡 Check failed ({_drop_e})"

    # 12. Mastodon Cross-Post Status
    mastodon_status = "⚪ Disabled"
    try:
        cooling, c_reason, c_rem = is_cooling_down("mastodon")
        if cooling:
            mastodon_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
        elif config.MASTODON_ENABLED and config.MASTODON_ACCESS_TOKEN:
            from mastodon_uploader import verify_mastodon_token, get_mastodon_profile_info
            masto_ok = verify_mastodon_token()
            if masto_ok:
                minfo = get_mastodon_profile_info()
                statuses = minfo.get("statuses_count", 0) if minfo else "?"
                mastodon_status = f"🟢 Active (@{minfo.get('username', 'user')}, {statuses} toots)"
            else:
                mastodon_status = "🔴 Token Invalid"
                warnings.append("Mastodon token is invalid — check MASTODON_ACCESS_TOKEN in .env")
        elif config.MASTODON_ENABLED and not config.MASTODON_ACCESS_TOKEN:
            mastodon_status = "🔴 Token Missing"
            warnings.append("MASTODON_ENABLED=true but MASTODON_ACCESS_TOKEN is missing in .env")
    except Exception as _masto_e:
        mastodon_status = f"🟡 Check failed ({_masto_e})"


    # 14. Pixelfed Cross-Post Status
    pixelfed_status = "⚪ Disabled"
    try:
        if getattr(config, "PIXELFED_ENABLED", False):
            from circuit_breaker import is_cooling_down
            cooling, c_reason, c_rem = is_cooling_down("pixelfed")
            if cooling:
                pixelfed_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
            elif not config.PIXELFED_ACCESS_TOKEN:
                pixelfed_status = "🔴 Token Missing"
                warnings.append("PIXELFED_ENABLED=true but PIXELFED_ACCESS_TOKEN is missing in .env")
            else:
                from pixelfed_uploader import get_pixelfed_status, get_pixelfed_profile_info
                status = get_pixelfed_status()
                if status.get("authenticated"):
                    pinfo = get_pixelfed_profile_info()
                    statuses = pinfo.get("statuses_count", 0) if pinfo else "?"
                    pixelfed_status = f"🟢 Active (@{pinfo.get('username', 'user')}, {statuses} posts)"
                elif status.get("status_code") == 401 or status.get("state") == "unauthenticated":
                    pixelfed_status = "🔴 401 Unauthorized"
                    warnings.append("Pixelfed token is invalid or expired (HTTP 401) — check PIXELFED_ACCESS_TOKEN in .env")
                else:
                    pixelfed_status = f"🔴 {status.get('message', 'Token Invalid')}"
                    warnings.append(f"Pixelfed verification failed: {status.get('message')}")
    except Exception as _pix_e:
        pixelfed_status = f"🟡 Check failed ({_pix_e})"

    # 15. Freeimage.host Cross-Post Status
    freeimage_status = "⚪ Disabled"
    try:
        if getattr(config, "FREEIMAGE_ENABLED", False):
            from circuit_breaker import is_cooling_down
            cooling, c_reason, c_rem = is_cooling_down("freeimage")
            if cooling:
                freeimage_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
            else:
                from freeimage_uploader import verify_freeimage_token
                fi_ok = verify_freeimage_token()
                if fi_ok:
                    freeimage_status = "🟢 Active (API Key Verified)"
                else:
                    freeimage_status = "🟡 Reachable"
    except Exception as _fi_e:
        freeimage_status = f"🟡 Check failed ({_fi_e})"

    # 16. Wallhaven.cc Cross-Post Status
    wallhaven_status = "⚪ Disabled"
    try:
        if getattr(config, "WALLHAVEN_ENABLED", False):
            from wallhaven_uploader import verify_wallhaven_token
            wh_ok = verify_wallhaven_token()
            wh_user = getattr(config, "WALLHAVEN_USERNAME", "animizing")
            wallhaven_status = f"🟢 Active (@{wh_user})" if wh_ok else "🟡 Reachable"
    except Exception as _wh_e:
        wallhaven_status = f"🟡 Check failed ({_wh_e})"

    # 17. Imghippo Cross-Post Status
    imghippo_status = "⚪ Disabled"
    try:
        if getattr(config, "IMGHIPPO_ENABLED", False):
            from circuit_breaker import is_cooling_down
            cooling, c_reason, c_rem = is_cooling_down("imghippo")
            if cooling:
                imghippo_status = f"🟡 Cooldown ({c_rem}h left: {c_reason})"
            else:
                from imghippo_uploader import verify_imghippo_token
                hippo_ok = verify_imghippo_token()
                if hippo_ok:
                    imghippo_status = "🟢 Active (API Key Verified)"
                else:
                    imghippo_status = "🟡 Reachable"
    except Exception as _hip_e:
        imghippo_status = f"🟡 Check failed ({_hip_e})"

    # 18. DeviantArt Cross-Post Status
    deviantart_status = "⚪ Disabled"
    try:
        if getattr(config, "DEVIANTART_ENABLED", False):
            from deviantart_uploader import verify_deviantart_token
            da_ok = verify_deviantart_token()
            deviantart_status = "🟢 Active (@muthelyrics)" if da_ok else "🔴 Token Error"
    except Exception as _da_e:
        deviantart_status = f"🟡 Check failed ({_da_e})"

    # 19. ImgBB Cross-Post Status
    imgbb_status = "⚪ Disabled"
    try:
        if getattr(config, "IMGBB_ENABLED", False):
            from imgbb_uploader import verify_imgbb_token, is_imgbb_maintenance
            ibb_ok = verify_imgbb_token()
            if ibb_ok:
                imgbb_status = "🟢 Active (@muthelyrics)"
            elif is_imgbb_maintenance():
                imgbb_status = "🟡 Maintenance (ImgBB down for maintenance)"
            else:
                imgbb_status = "🔴 Token Error"
                warnings.append("ImgBB API key is invalid — check IMGBB_API_KEY in .env")
    except Exception as _ibb_e:
        imgbb_status = f"🟡 Check failed ({_ibb_e})"

    # 20. ImageKit.io Cross-Post Status
    imagekit_status = "⚪ Disabled"
    try:
        if getattr(config, "IMAGEKIT_ENABLED", False):
            from imagekit_uploader import verify_imagekit_token
            ik_ok = verify_imagekit_token()
            imagekit_status = "🟢 Active (Mumbai CDN Connected)" if ik_ok else "🔴 Key/Token Error"
    except Exception as _ik_e:
        imagekit_status = f"🟡 Check failed ({_ik_e})"

    # Watermark Cleaner & Inpainter Status (Zero Stock Rejections — lazy-checked to save 90MB RAM on Render)
    watermark_status = "⚪ Disabled"
    try:
        if getattr(config, "WATERMARK_CLEANER_ENABLED", True):
            import importlib.util
            import importlib.metadata
            if importlib.util.find_spec("cv2") is not None:
                try:
                    cv_ver = importlib.metadata.version("opencv-python-headless")
                except Exception:
                    cv_ver = "4.x"
                watermark_status = f"🟢 Active (OpenCV {cv_ver} Telea Inpainter)"
            else:
                watermark_status = "🟡 OpenCV not installed"
    except Exception as _wm_e:
        watermark_status = f"🟡 Check failed ({_wm_e})"

    all_cooldowns = get_all_cooldowns()

    # Overall Health Verdict
    if any("🔴" in w or "Critical" in w or "DB Error" in w for w in warnings):
        overall_badge = "🔴 ATTENTION NEEDED"
    elif len(warnings) > 0:
        overall_badge = "🟡 HEALTHY (With Minor Notes)"
    else:
        overall_badge = "🟢 100% HEALTHY & OPERATIONAL"

    # Calculate next scheduled run date
    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    next_check_date = (now_ist + datetime.timedelta(days=3)).strftime("%d %b %Y")

    return {
        "timestamp_ist": now_ist.strftime("%d %b %Y, %I:%M %p"),
        "next_check_date": next_check_date,
        "overall_badge": overall_badge,
        "warnings": warnings,
        "mem_mb": mem_mb,
        "mem_status": mem_status,
        "disk_mb": disk_mb,
        "disk_status": disk_status,
        "db_status": db_status,
        "stats": stats,
        "days_buffer": days_buffer,
        "webhook_status": webhook_status,
        "board_status": board_status,
        "amazon_status": amazon_status,
        "pa_api_status": pa_api_status,
        "tracker_status": tracker_status,
        "arena_status": arena_status,
        "bluesky_status": bluesky_status,
        "raindrop_status": raindrop_status,
        "mastodon_status": mastodon_status,
        "pixelfed_status": pixelfed_status,
        "freeimage_status": freeimage_status,
        "wallhaven_status": wallhaven_status,
        "imghippo_status": imghippo_status,
        "deviantart_status": deviantart_status,
        "imgbb_status": imgbb_status,
        "imagekit_status": imagekit_status,
        "watermark_status": watermark_status,
        "monitored_channels": len(config.TELEGRAM_CHANNELS),
        "all_cooldowns": all_cooldowns,
    }


def format_health_report(diag: dict, is_scheduled: bool = False) -> str:
    """
    Builds a beautifully styled Telegram markdown report card.
    """
    import config
    header_title = "🏥 3-Day Automated System Health Report" if is_scheduled else "🏥 System Health Diagnostics (/doctor)"
    stats = diag["stats"]

    warnings_block = ""
    if diag["warnings"]:
        warnings_block = "\n⚠️ System Notes:\n" + "\n".join(f"  • {w}" for w in diag["warnings"]) + "\n"

    est_revenue = f"₹{stats['est_rev_min']} - ₹{stats['est_rev_max']}"

    # Circuit breaker summary
    all_cooldowns = diag.get("all_cooldowns", {})
    if all_cooldowns:
        cb_lines = [f"  • {p.capitalize()}: 🟡 Cooldown Active ({info['remaining_hours']}h left — {info['reason']})" for p, info in all_cooldowns.items()]
        cb_section = "🛡️ Platform Circuit Breakers (Cooldown Active):\n" + "\n".join(cb_lines) + "\n\n"
    else:
        cb_section = "🛡️ Platform Circuit Breakers:\n  • Status: 🟢 All Clear (All 7 platforms active & protected)\n\n"

    # Dynamically build integrations section showing only active/enabled services
    integration_items = [
        f"  • Make.com Webhook   : {diag['webhook_status']}",
        f"  • Watermark Cleaner  : {diag.get('watermark_status', 'N/A')}",
    ]
    if getattr(config, "BLUESKY_ENABLED", False):
        integration_items.append(f"  • Bluesky Cross-Post : {diag.get('bluesky_status', 'N/A')}")
    if getattr(config, "RAINDROP_ENABLED", False):
        integration_items.append(f"  • Raindrop.io        : {diag.get('raindrop_status', 'N/A')}")
    if getattr(config, "MASTODON_ENABLED", False):
        integration_items.append(f"  • Mastodon           : {diag.get('mastodon_status', 'N/A')}")
    if getattr(config, "FREEIMAGE_ENABLED", False):
        integration_items.append(f"  • Freeimage.host     : {diag.get('freeimage_status', 'N/A')}")
    if getattr(config, "WALLHAVEN_ENABLED", False):
        integration_items.append(f"  • Wallhaven.cc       : {diag.get('wallhaven_status', '🟢 Active')}")
    if getattr(config, "ARENA_ENABLED", False):
        integration_items.append(f"  • Are.na Cross-Post  : {diag.get('arena_status', 'N/A')}")
    if getattr(config, "PIXELFED_ENABLED", False):
        integration_items.append(f"  • Pixelfed           : {diag.get('pixelfed_status', 'N/A')}")
    if getattr(config, "IMGHIPPO_ENABLED", False):
        integration_items.append(f"  • Imghippo           : {diag.get('imghippo_status', 'N/A')}")
    if getattr(config, "DEVIANTART_ENABLED", False):
        integration_items.append(f"  • DeviantArt         : {diag.get('deviantart_status', '🟢 Active')}")
    if getattr(config, "IMGBB_ENABLED", False):
        integration_items.append(f"  • ImgBB              : {diag.get('imgbb_status', '🟢 Active')}")
    if getattr(config, "IMAGEKIT_ENABLED", False):
        integration_items.append(f"  • ImageKit.io        : {diag.get('imagekit_status', '🟢 Active')}")

    integration_items.append(f"  • Monitored Channels : {diag['monitored_channels']} channel(s)")
    integrations_block = "\n".join(integration_items)

    report = (
        f"{header_title}\n"
        f"{'═' * 34}\n"
        f"🗓️ Date: {diag['timestamp_ist']} IST\n"
        f"⏳ Next Auto-Check: {diag['next_check_date']}\n\n"
        f"Status: {diag['overall_badge']}\n"
        f"{warnings_block}\n"
        f"📊 Server Resources (Render):\n"
        f"  • Memory Usage   : {diag['mem_mb']} MB / 512 MB ({diag['mem_status']})\n"
        f"  • Image Storage  : {diag['disk_mb']} MB ({diag['disk_status']})\n"
        f"  • SQLite Database: {diag['db_status']}\n\n"
        f"📌 Pinterest & Queue Health:\n"
        f"  • Board Routing            : {diag.get('board_status', 'N/A')}\n"
        f"  • Pins Posted (Last 3 Days): {stats['pins_3d']} pins\n"
        f"  • All-Time Total Pins      : {stats['total_pins']} pins\n"
        f"  • Queue Remaining          : {stats['queue']['total']} pins (~{diag['days_buffer']} days buffer)\n"
        f"  • Failed Upload Retries    : {stats['failed_retries']}\n\n"
        f"💰 Affiliate & Earnings Health:\n"
        f"  • Amazon Store Tag : {diag['amazon_status']}\n"
        f"  • Product Engine   : {diag['pa_api_status']}\n"
        f"  • Click Tracker    : {diag['tracker_status']}\n"
        f"  • Clicks (3 Days)  : {stats['clicks_3d']} clicks\n"
        f"  • Est. 3-Day Rev   : {est_revenue}\n\n"
        f"{cb_section}"
        f"🌐 Active Integrations & Channels:\n"
        f"{integrations_block}\n\n"
        f"💡 Tip: Type /doctor anytime to run an instant check on demand."
    )
    return report


def check_and_run_scheduled_health_check(app_ref, loop_ref, admin_chat_id: str):
    """
    Checks if 3 days have passed since the last automated health check.
    If so, executes the diagnostic and sends the report to admin Telegram.
    """
    from database import get_metadata, set_metadata
    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")

    last_run_str = get_metadata("last_3day_health_check_date", "")

    should_run = False
    if not last_run_str:
        # First time running — initialize and run
        should_run = True
    else:
        try:
            last_date = datetime.datetime.strptime(last_run_str, "%Y-%m-%d").date()
            days_passed = (now_ist.date() - last_date).days
            if days_passed >= 3:
                should_run = True
        except Exception:
            should_run = True

    if should_run and app_ref and loop_ref and admin_chat_id:
        try:
            logger.info(f"[Doctor] Firing 3-Day Automated Health Check for admin {admin_chat_id}...")
            diag = run_full_system_diagnostic()
            report_text = format_health_report(diag, is_scheduled=True)

            async def _send():
                try:
                    await app_ref.bot.send_message(chat_id=admin_chat_id, text=report_text)
                    logger.info("[Doctor] 3-Day Automated Health Check sent successfully.")
                except Exception as e:
                    logger.error(f"[Doctor] Failed to send health report: {e}")

            import asyncio
            asyncio.run_coroutine_threadsafe(_send(), loop_ref)
            set_metadata("last_3day_health_check_date", today_str)

        except Exception as e:
            logger.error(f"[Doctor] Error running scheduled health check: {e}")
