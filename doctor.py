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
    Runs a comprehensive 7-point diagnostic check across the entire bot stack.
    Returns a structured dictionary of results and warnings.
    """
    import config
    from database import get_3day_stats, _get_conn
    from crash_protection import get_memory_mb, get_disk_usage_mb
    import pa_api

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
        "monitored_channels": len(config.TELEGRAM_CHANNELS),
    }


def format_health_report(diag: dict, is_scheduled: bool = False) -> str:
    """
    Builds a beautifully styled Telegram markdown report card.
    """
    header_title = "🏥 3-Day Automated System Health Report" if is_scheduled else "🏥 System Health Diagnostics (/doctor)"
    stats = diag["stats"]

    warnings_block = ""
    if diag["warnings"]:
        warnings_block = "\n⚠️ System Notes:\n" + "\n".join(f"  • {w}" for w in diag["warnings"]) + "\n"

    est_revenue = f"₹{stats['est_rev_min']} - ₹{stats['est_rev_max']}"

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
        f"🌐 Integrations & Webhooks:\n"
        f"  • Make.com Webhook : {diag['webhook_status']}\n"
        f"  • Monitored Channels: {diag['monitored_channels']} channel(s)\n\n"
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
