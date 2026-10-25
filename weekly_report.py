"""
weekly_report.py — Weekly Sunday Performance Digest
====================================================
Auto-sends a comprehensive weekly performance report every Sunday at
WEEKLY_REPORT_TIME IST (default: 09:00 AM).

New .env keys:
  WEEKLY_REPORT_ENABLED=true
  WEEKLY_REPORT_TIME=09:00       # IST HH:MM
  WEEKLY_REPORT_DAY=6            # 6=Sunday (Python weekday: Mon=0, Sun=6)
"""

import datetime
from logger import get_logger
import config

logger = get_logger(__name__)


def _ist_now() -> datetime.datetime:
    """Returns current datetime in IST (UTC+5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def build_weekly_report() -> str:
    """Assembles the full weekly Markdown digest."""
    now = _ist_now()
    week_start = (now - datetime.timedelta(days=7)).strftime("%d %b")
    week_end = now.strftime("%d %b %Y")

    lines = [
        "📆 *Weekly Performance Report*",
        f"*{week_start} → {week_end}*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    cutoff_7d = (now - datetime.timedelta(days=7)).strftime("%Y-%m-%d")

    # 1. Posting stats
    try:
        from database import _get_conn

        with _get_conn() as conn:
            pins_posted = conn.execute("""
                SELECT COUNT(*) FROM uploaded_files
                WHERE date(uploaded_at, '+5 hours', '+30 minutes') >= ?
            """, (cutoff_7d,)).fetchone()[0]
            organic_count = conn.execute("""
                SELECT COUNT(*) FROM uploaded_files
                WHERE date(uploaded_at, '+5 hours', '+30 minutes') >= ?
                  AND is_organic = 1
            """, (cutoff_7d,)).fetchone()[0]
        monetized_count = max(0, pins_posted - organic_count)
        lines += [
            "",
            "📌 *Pinterest Posting*",
            f"  • Pins Posted: *{pins_posted}* (🌿 {organic_count} organic / 💎 {monetized_count} monetized)",
        ]
    except Exception as e:
        logger.warning(f"[WeeklyReport] Posting stats error: {e}")

    # 2. Traffic & Funnel
    top_pins = []
    try:
        from pinterest_analytics import get_analytics_summary, get_top_pins

        analytics = get_analytics_summary(days=7)
        top_pins = get_top_pins(days=7, limit=5)

        lines += [
            "",
            "🌐 *Traffic Funnel (7 Days)*",
            f"  • Bridge Visits: *{analytics.get('bridge_visits', 0)}*",
            f"  • From Pinterest: *{analytics.get('pinterest_visits', 0)}* ({analytics.get('pint_rate', 0)}%)",
            f"  • Affiliate Clicks: *{analytics.get('amazon_clicks', 0)}* ({analytics.get('ctr', 0)}% CTR)",
        ]
    except Exception as e:
        logger.warning(f"[WeeklyReport] Analytics error: {e}")

    # 3. Revenue
    try:
        from database import get_revenue_summary

        rev_7d = get_revenue_summary(days=7)
        lines += [
            "",
            "💰 *Revenue Estimate (7 Days)*",
            f"  • Clicks: *{rev_7d.get('total_clicks', 0)}* | Est: *₹{rev_7d.get('total_revenue', 0.0):,.2f}*",
        ]
        if rev_7d.get("by_anime"):
            top = rev_7d["by_anime"][0]
            lines.append(f"  • Top Earner: *{top['anime']}* (₹{top['revenue']:,.2f})")
    except Exception as e:
        logger.warning(f"[WeeklyReport] Revenue error: {e}")

    # 4. Top 5 pins this week
    if top_pins:
        lines += ["", "🏆 *Top Pins This Week*"]
        for i, pin in enumerate(top_pins[:5], start=1):
            ctr_str = f"{pin.get('ctr', 0)}% CTR"
            lines.append(
                f"  {i}. *{pin.get('anime', 'Anime')}* — {pin.get('visits', 0)}v / {pin.get('clicks', 0)}c ({ctr_str})\n"
                f"     _{pin.get('title', '')[:55]}_"
            )

    # 5. A/B test summary
    try:
        from database import _get_conn

        with _get_conn() as conn:
            ab_decided = conn.execute("""
                SELECT COUNT(DISTINCT test_id) FROM ab_tests
                WHERE status IN ('winner', 'loser')
                  AND date(decided_at) >= ?
            """, (cutoff_7d,)).fetchone()[0]
        if ab_decided:
            lines += ["", f"🔬 *A/B Tests Decided This Week: {ab_decided}*"]
    except Exception:
        pass

    # 6. Ghost pin count
    try:
        from database import get_uploaded_files_with_zero_visits

        ghosts = get_uploaded_files_with_zero_visits(days_old=7, limit=50)
        lines += [
            "",
            f"👻 *Ghost Pins (0 visits in 7d)*: *{len(ghosts)}*",
        ]
        if ghosts:
            lines.append("  _Run /revive to re-queue them with fresh titles._")
    except Exception:
        pass

    # 7. Health
    try:
        from crash_protection import get_memory_mb

        mem = round(get_memory_mb(), 1)
        lines += ["", "🛡️ *System Health*", f"  • RAM Usage: *{mem} MB*"]
    except Exception:
        pass

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "💡 _Use /revenue, /radar, /queue, or /ab for details._",
    ]

    return "\n".join(lines)


_last_sent_week_key = ""


def send_weekly_report(force: bool = False) -> bool:
    """Send the weekly report. Deduplication via bot_metadata."""
    global _last_sent_week_key
    if not getattr(config, "WEEKLY_REPORT_ENABLED", True):
        return False

    now = _ist_now()
    week_key = f"weekly_report_{now.strftime('%Y-W%W')}"

    from database import get_metadata, set_metadata

    if not force:
        if _last_sent_week_key == week_key or get_metadata(week_key, "") == "sent":
            _last_sent_week_key = week_key
            logger.debug(f"[WeeklyReport] Already sent for {week_key}")
            return False

    try:
        report = build_weekly_report()
        from telegram_bot import notify_admin

        notify_admin(report, parse_mode="Markdown")
        set_metadata(week_key, "sent")
        _last_sent_week_key = week_key
        logger.info(f"[WeeklyReport] Weekly report successfully sent for {week_key}")
        return True
    except Exception as e:
        logger.error(f"[WeeklyReport] Failed to send report: {e}", exc_info=True)
        return False


def check_and_send_weekly_report() -> bool:
    """Called from scheduler loop every 30s. Triggers on Sunday at WEEKLY_REPORT_TIME IST."""
    global _last_sent_week_key
    if not getattr(config, "WEEKLY_REPORT_ENABLED", True):
        return False

    now = _ist_now()
    target_day = int(getattr(config, "WEEKLY_REPORT_DAY", 6))  # 6=Sunday
    target_time = getattr(config, "WEEKLY_REPORT_TIME", "09:00").strip()

    week_key = f"weekly_report_{now.strftime('%Y-W%W')}"
    if _last_sent_week_key == week_key:
        return False

    if now.weekday() == target_day and now.strftime("%H:%M") >= target_time:
        return send_weekly_report(force=False)
    return False
