"""
pinterest_analytics.py — Self-Hosted Analytics Feedback Loop
=============================================================
Since we use Make.com (no Pinterest API token), analytics are tracked
using our OWN server data — giving a better funnel picture than Pinterest's
API anyway:

  Pinterest user clicks pin link
        ↓
  Bridge page /p/<code> loaded  ← TRACKED here (bridge_visits table)
        ↓
  User clicks "Shop on Amazon"  ← TRACKED in link_clicks table (already exists)
        ↓
  Amazon product page

This gives us a FULL funnel:
  Impressions (Pinterest) → Bridge Visits → Amazon Clicks → (Revenue)

Without a Pinterest API token we can't get impressions, but we CAN:
  1. Track bridge page visits (who visited /p/<code>)
  2. Track click-through rate (bridge visit → Amazon click)
  3. Identify which anime/title drives the most traffic
  4. Track time-of-day patterns (when do people click?)
  5. Detect Pinterest referrers (confirms traffic is from Pinterest)
  6. Surface top-performing content so you post more of what works

Architecture (from graphify):
  keep_alive.py /p/<code>  → record_bridge_visit()  [this module]
  keep_alive.py /r/<code>  → record_link_click()    [database.py, already exists]
  telegram_bot.py          → /pinanalytics, /topseo commands
"""

import datetime
from logger import get_logger

logger = get_logger(__name__)


# ── Database setup ─────────────────────────────────────────────────────────────

def _ensure_analytics_table() -> None:
    """Create bridge_visits table if not already present."""
    try:
        from database import _get_conn
        with _get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS bridge_visits (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    code        TEXT NOT NULL,
                    anime_name  TEXT,
                    title       TEXT,
                    user_agent  TEXT,
                    referrer    TEXT,
                    is_pinterest INTEGER DEFAULT 0,  -- 1 if referrer is pinterest.com
                    visited_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bridge_visits_code
                ON bridge_visits (code)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_bridge_visits_time
                ON bridge_visits (visited_at)
            """)
            conn.commit()
    except Exception as e:
        logger.error(f"[Analytics] Table setup failed: {e}")


def record_bridge_visit(code: str, anime_name: str, title: str,
                        user_agent: str = "", referrer: str = "") -> None:
    """
    Record a visit to the /p/<code> bridge landing page.
    Called by keep_alive.py BEFORE the existing record_link_click (which tracks Amazon clicks).
    This separates PAGE VIEWS from actual AMAZON CLICKS.
    """
    _ensure_analytics_table()
    is_pinterest = 1 if "pinterest" in (referrer or "").lower() else 0
    try:
        from database import _get_conn
        with _get_conn() as conn:
            conn.execute("""
                INSERT INTO bridge_visits
                    (code, anime_name, title, user_agent, referrer, is_pinterest)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                code,
                anime_name[:200] if anime_name else "",
                title[:200]      if title      else "",
                user_agent[:255] if user_agent else "",
                referrer[:255]   if referrer   else "",
                is_pinterest,
            ))
            conn.commit()
    except Exception as e:
        logger.error(f"[Analytics] record_bridge_visit failed: {e}")


# ── Analytics Read Functions ───────────────────────────────────────────────────

def get_analytics_summary(days: int = 7) -> dict:
    """
    Full funnel summary for the last N days.
    Returns bridge visits, Amazon clicks, CTR, Pinterest referral rate.
    """
    _ensure_analytics_table()
    try:
        from database import _get_conn
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")

        with _get_conn() as conn:
            # Bridge page visits (our landing page)
            bv = conn.execute("""
                SELECT COUNT(*), COUNT(CASE WHEN is_pinterest=1 THEN 1 END)
                FROM bridge_visits WHERE visited_at >= ?
            """, (cutoff,)).fetchone()
            bridge_total    = bv[0] or 0
            pinterest_visits= bv[1] or 0

            # Amazon clicks (already in link_clicks table)
            ac = conn.execute("""
                SELECT COUNT(*) FROM link_clicks WHERE clicked_at >= ?
            """, (cutoff,)).fetchone()
            amazon_clicks = ac[0] or 0

            # Top anime by bridge visits
            top_anime = conn.execute("""
                SELECT anime_name, COUNT(*) as visits,
                       COUNT(CASE WHEN is_pinterest=1 THEN 1 END) as from_pint
                FROM bridge_visits
                WHERE visited_at >= ? AND anime_name IS NOT NULL AND anime_name != ''
                GROUP BY anime_name
                ORDER BY visits DESC
                LIMIT 5
            """, (cutoff,)).fetchall()

            # Today's counts (IST)
            today_ist = (
                datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
            ).strftime("%Y-%m-%d")
            today_bv = conn.execute("""
                SELECT COUNT(*) FROM bridge_visits
                WHERE date(visited_at, '+5 hours', '+30 minutes') = ?
            """, (today_ist,)).fetchone()[0]
            today_clicks = conn.execute("""
                SELECT COUNT(*) FROM link_clicks
                WHERE date(clicked_at, '+5 hours', '+30 minutes') = ?
            """, (today_ist,)).fetchone()[0]

        # CTR = people who actually clicked Amazon after visiting bridge page
        ctr = round(amazon_clicks / max(1, bridge_total) * 100, 1)
        pint_rate = round(pinterest_visits / max(1, bridge_total) * 100, 1)

        return {
            "days":            days,
            "bridge_visits":   bridge_total,
            "amazon_clicks":   amazon_clicks,
            "pinterest_visits":pinterest_visits,
            "ctr":             ctr,
            "pint_rate":       pint_rate,
            "today_visits":    today_bv,
            "today_clicks":    today_clicks,
            "top_anime":       [(r[0], r[1], r[2]) for r in top_anime],
        }
    except Exception as e:
        logger.error(f"[Analytics] get_analytics_summary failed: {e}")
        return {
            "days": days, "bridge_visits": 0, "amazon_clicks": 0,
            "pinterest_visits": 0, "ctr": 0.0, "pint_rate": 0.0,
            "today_visits": 0, "today_clicks": 0, "top_anime": [],
        }


def get_top_pins(days: int = 7, metric: str = "visits", limit: int = 5) -> list[dict]:
    """
    Top-performing pins by bridge page visits or Amazon clicks.
    metric: 'visits' | 'clicks' | 'ctr'
    """
    _ensure_analytics_table()
    try:
        from database import _get_conn
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")

        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT bv.code, bv.anime_name, bv.title,
                       COUNT(bv.id)                                 as visits,
                       COUNT(CASE WHEN bv.is_pinterest=1 THEN 1 END) as pint_visits,
                       COUNT(lc.id)                                 as clicks
                FROM bridge_visits bv
                LEFT JOIN link_clicks lc ON bv.code = lc.code
                    AND lc.clicked_at >= ?
                WHERE bv.visited_at >= ?
                  AND bv.anime_name IS NOT NULL
                GROUP BY bv.code
                ORDER BY visits DESC
                LIMIT ?
            """, (cutoff, cutoff, limit)).fetchall()

        return [
            {
                "code":          r[0],
                "anime":         r[1],
                "title":         r[2],
                "visits":        r[3] or 0,
                "pint_visits":   r[4] or 0,
                "clicks":        r[5] or 0,
                "ctr":           round((r[5] or 0) / max(1, r[3]) * 100, 1),
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[Analytics] get_top_pins failed: {e}")
        return []


def get_hourly_pattern(days: int = 7) -> list[tuple]:
    """Returns (hour_IST, visit_count) for the last N days — tells you when your audience is active."""
    _ensure_analytics_table()
    try:
        from database import _get_conn
        cutoff = (
            datetime.datetime.utcnow() - datetime.timedelta(days=days)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT
                    CAST(strftime('%H', visited_at, '+5 hours', '+30 minutes') AS INTEGER) as hour_ist,
                    COUNT(*) as visits
                FROM bridge_visits
                WHERE visited_at >= ?
                GROUP BY hour_ist
                ORDER BY visits DESC
                LIMIT 5
            """, (cutoff,)).fetchall()
        return [(r[0], r[1]) for r in rows]
    except Exception as e:
        logger.error(f"[Analytics] get_hourly_pattern failed: {e}")
        return []


def run_analytics_pull(notify_admin: bool = True) -> dict:
    """
    'Pull' for self-hosted analytics — just ensures tables exist and
    returns the current summary. No external API needed.
    """
    _ensure_analytics_table()
    summary = get_analytics_summary(days=7)
    logger.info(f"[Analytics] Self-hosted analytics summary: {summary}")
    return {
        "pins_checked": summary["bridge_visits"],
        "days_stored":  summary["days"],
        "errors":       0,
        "summary":      summary,
    }
