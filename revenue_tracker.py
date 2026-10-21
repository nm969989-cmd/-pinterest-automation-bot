"""
revenue_tracker.py — Affiliate Revenue Estimator & Dashboard
=============================================================
Estimates affiliate revenue from link_clicks data.
No Amazon API credentials needed — uses configurable revenue-per-click rate.

Revenue model:
  Est. Revenue = clicks × REVENUE_PER_CLICK_INR
  Default: ₹25 per click (~5% commission on avg ₹500 order)

New .env keys:
  REVENUE_PER_CLICK_INR=25.0      # INR earned per affiliate click
  REVENUE_TRACKING_ENABLED=true
"""

import datetime
from logger import get_logger
import config

logger = get_logger(__name__)

REVENUE_PER_CLICK = float(getattr(config, "REVENUE_PER_CLICK_INR", 25.0))


def _ist_today() -> str:
    """Returns today's date string in IST timezone (UTC+5:30)."""
    return (
        datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    ).strftime("%Y-%m-%d")


def refresh_revenue_log(date_str: str = None) -> int:
    """
    Recomputes revenue from link_clicks and saves to revenue_log table.
    Called daily from scheduler or on-demand via /revenue command.
    Returns total clicks processed.
    """
    if not getattr(config, "REVENUE_TRACKING_ENABLED", True):
        return 0

    date_str = date_str or _ist_today()
    try:
        from database import _get_conn

        with _get_conn() as conn:
            # Aggregate clicks by anime for the given IST date
            rows = conn.execute("""
                SELECT anime_name, COUNT(*) as clicks
                FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') = ?
                  AND anime_name IS NOT NULL AND anime_name != ''
                GROUP BY anime_name
            """, (date_str,)).fetchall()

            total_clicks = 0
            for anime_name, clicks in rows:
                est_revenue = round(clicks * REVENUE_PER_CLICK, 2)
                # Atomic upsert using UNIQUE(date_ist, anime_name)
                conn.execute("""
                    INSERT INTO revenue_log (date_ist, anime_name, clicks, est_revenue)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(date_ist, anime_name) DO UPDATE SET
                        clicks = excluded.clicks,
                        est_revenue = excluded.est_revenue
                """, (date_str, anime_name, clicks, est_revenue))
                total_clicks += clicks

            conn.commit()

        logger.debug(f"[Revenue] Refreshed revenue_log for {date_str}: {total_clicks} clicks")
        return total_clicks
    except Exception as e:
        logger.error(f"[Revenue] refresh_revenue_log failed: {e}")
        return 0


def build_revenue_dashboard(days: int = 30) -> str:
    """Builds the Telegram Markdown message for /revenue command."""
    from database import get_revenue_summary

    refresh_revenue_log()

    data_7d = get_revenue_summary(days=7)
    data_30d = get_revenue_summary(days=days)

    today_str = _ist_today()
    try:
        from database import _get_conn

        with _get_conn() as conn:
            today_row = conn.execute("""
                SELECT COALESCE(SUM(clicks), 0), COALESCE(SUM(est_revenue), 0)
                FROM revenue_log WHERE date_ist = ?
            """, (today_str,)).fetchone()
        today_clicks = today_row[0] if today_row else 0
        today_rev = round(today_row[1], 2) if today_row else 0.0
    except Exception:
        today_clicks, today_rev = 0, 0.0

    lines = [
        "💰 *Affiliate Revenue Dashboard*",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 *Today* ({today_str})",
        f"  • Clicks: *{today_clicks}* | Est. Revenue: *₹{today_rev:,.2f}*",
        "",
        "📈 *7-Day Summary*",
        f"  • Total Clicks: *{data_7d['total_clicks']}*",
        f"  • Est. Revenue: *₹{data_7d['total_revenue']:,.2f}*",
        f"  • Avg/Day: *₹{round(data_7d['total_revenue'] / 7, 2):,.2f}*",
        "",
        f"📊 *{days}-Day Summary*",
        f"  • Total Clicks: *{data_30d['total_clicks']}*",
        f"  • Est. Revenue: *₹{data_30d['total_revenue']:,.2f}*",
        f"  • Avg/Day: *₹{round(data_30d['total_revenue'] / max(1, days), 2):,.2f}*",
        "",
        f"🏆 *Top Earning Anime ({days}d)*",
    ]

    top_list = data_30d.get("by_anime", [])[:5]
    if top_list:
        for i, item in enumerate(top_list, start=1):
            lines.append(
                f"  {i}. *{item['anime']}* — {item['clicks']} clicks | ₹{item['revenue']:,.2f}"
            )
    else:
        lines.append("  _No anime clicks recorded yet._")

    lines.extend([
        "",
        f"_Rate: ₹{REVENUE_PER_CLICK}/click (estimated commission)._",
        "_Use `/revenue 7` for weekly view, `/revenue 30` for monthly._",
    ])
    return "\n".join(lines)
