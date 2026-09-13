import sqlite3
import os
from logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.db")


def _get_conn():
    """Returns a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """Creates tables if they don't exist yet."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_posts (
                post_id TEXT PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS uploaded_files (
                filename   TEXT PRIMARY KEY,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                title      TEXT,
                anime_name TEXT,
                image_url  TEXT
            )
        """)
        # ── Backlog queue table ─────────────────────────────────────────────
        # Stores images waiting to be posted (priority: new > backlog)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pin_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id     TEXT UNIQUE,
                image_path  TEXT,
                title       TEXT,
                description TEXT,
                link        TEXT,
                anime_name  TEXT,
                image_url   TEXT,
                board_id    TEXT DEFAULT '',   -- Pinterest board ID for multi-board routing
                priority    INTEGER DEFAULT 0,   -- 1=new, 0=backlog
                scheduled_date TEXT,             -- YYYY-MM-DD when to post
                retry_count INTEGER DEFAULT 0,   -- auto-retry: drop after 3 fails
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Bot Metadata / State Store ──────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_metadata (
                key         TEXT PRIMARY KEY,
                value       TEXT,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add new columns if upgrading from older schema
        for col in ["anime_name TEXT", "image_url TEXT", "board_id TEXT DEFAULT ''",
                    "retry_count INTEGER DEFAULT 0"]:
            try:
                conn.execute(f"ALTER TABLE uploaded_files ADD COLUMN {col}")
            except Exception:
                pass
        # Add board_id and retry_count to pin_queue if upgrading from older schema
        for col in ["board_id TEXT DEFAULT ''", "retry_count INTEGER DEFAULT 0"]:
            try:
                conn.execute(f"ALTER TABLE pin_queue ADD COLUMN {col}")
            except Exception:
                pass

        # ── Affiliate Link Tracking Tables ──────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT UNIQUE,
                target_url  TEXT,
                anime_name  TEXT,
                title       TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS link_clicks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT,
                anime_name  TEXT,
                title       TEXT,
                user_agent  TEXT,
                referrer    TEXT,
                clicked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Stock Uploads Tracking Table ─────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_uploads (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                platform    TEXT,
                filename    TEXT,
                title       TEXT,
                status      TEXT DEFAULT 'success',
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Are.na Cross-Post Tracking ────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS arena_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                block_id    INTEGER,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Performance indexes (makes scheduler 100x faster) ───────────────
        # Scheduler queries pin_queue every 30s — 2,880 times/day — needs an index
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_sched
            ON pin_queue (scheduled_date, priority DESC, id ASC)
        """)
        # count_posts_today() also runs every 30s — index on uploaded_at
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_uploads_date
            ON uploaded_files (uploaded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracked_code
            ON tracked_links (code)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_clicks_time
            ON link_clicks (clicked_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_plat_date
            ON stock_uploads (platform, uploaded_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_arena_filename
            ON arena_posts (filename)
        """)

        # ── Tumblr Cross-Post Tracking ────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tumblr_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_id     TEXT,
                blog        TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tumblr_filename
            ON tumblr_posts (filename)
        """)

        # ── Bluesky Cross-Post Tracking ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bluesky_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_uri    TEXT,
                post_cid    TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_bluesky_filename
            ON bluesky_posts (filename)
        """)

        # ── Raindrop.io Cross-Post Tracking ──────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS raindrop_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                drop_id     INTEGER,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_raindrop_filename
            ON raindrop_posts (filename)
        """)
        conn.commit()
    logger.info(f"Database initialized at: {DB_PATH}")


# ── Are.na Cross-Post Tracking ───────────────────────────────────────────────

def is_arena_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Are.na (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM arena_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_arena_posted(filename: str, block_id: int = 0,
                      title: str = "", image_url: str = "") -> None:
    """Records a successful Are.na post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO arena_posts (filename, block_id, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, block_id, title, image_url)
        )
        conn.commit()


# ── Tumblr Cross-Post Tracking ────────────────────────────────────────────────

def is_tumblr_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Tumblr (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM tumblr_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_tumblr_posted(filename: str, post_id: str = "",
                       blog: str = "", image_url: str = "") -> None:
    """Records a successful Tumblr post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO tumblr_posts (filename, post_id, blog, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_id, blog, image_url)
        )
        conn.commit()


def get_arena_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Are.na posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM arena_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM arena_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, image_url, posted_at
            FROM arena_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "image_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }


def get_tumblr_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Tumblr posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM tumblr_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM tumblr_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, post_id, blog, image_url, posted_at
            FROM tumblr_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "post_id": r[1], "blog": r[2], "image_url": r[3], "posted_at": r[4]}
            for r in recent
        ]
    }


def get_bluesky_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Bluesky posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM bluesky_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM bluesky_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, post_uri, post_cid, image_url, posted_at
            FROM bluesky_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "post_uri": r[1], "post_cid": r[2], "image_url": r[3], "posted_at": r[4]}
            for r in recent
        ]
    }


def is_bluesky_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Bluesky (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM bluesky_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_bluesky_posted(filename: str, post_uri: str = "", post_cid: str = "",
                        image_url: str = "") -> None:
    """Records a successful Bluesky post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO bluesky_posts (filename, post_uri, post_cid, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_uri, post_cid, image_url)
        )
        conn.commit()


# ── Raindrop.io Cross-Post Tracking ──────────────────────────────────────────

def is_raindrop_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Raindrop.io (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM raindrop_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_raindrop_posted(filename: str, drop_id: int = 0,
                         title: str = "", image_url: str = "") -> None:
    """Records a successful Raindrop.io post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO raindrop_posts (filename, drop_id, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, drop_id, title, image_url)
        )
        conn.commit()


def get_raindrop_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Raindrop.io posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM raindrop_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM raindrop_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, image_url, posted_at
            FROM raindrop_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "image_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }


def get_multi_platform_stats(today_str: str = None) -> dict:
    """Returns an aggregated snapshot of all platforms (Pinterest, Are.na, Tumblr, Bluesky, Raindrop)."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    
    pins_today = get_today_uploads(today_str)
    all_time_pins = get_all_time_stats()
    arena_stats = get_arena_stats(today_str)
    tumblr_stats = get_tumblr_stats(today_str)
    bluesky_stats = get_bluesky_stats(today_str)
    raindrop_stats = get_raindrop_stats(today_str)

    return {
        "date": today_str,
        "pinterest": {
            "today": len(pins_today),
            "total": all_time_pins.get("total", 0),
            "pins": pins_today,
        },
        "arena": arena_stats,
        "tumblr": tumblr_stats,
        "bluesky": bluesky_stats,
        "raindrop": raindrop_stats,
    }



# ── Processed Posts (Telegram) ──────────────────────────────────────────────

def is_post_processed(post_id: str) -> bool:
    """Returns True if this Telegram post ID was already handled."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_posts WHERE post_id = ?", (post_id,)
        ).fetchone()
    return row is not None


def mark_post_processed(post_id: str):
    """Marks a Telegram post ID as processed so it won't be re-downloaded."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_posts (post_id) VALUES (?)", (post_id,)
        )
        conn.commit()


def get_processed_post_count() -> int:
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM processed_posts").fetchone()[0]

# Alias used by telegram_listener
get_processed_count = get_processed_post_count

def get_oldest_seen_post_numeric_id() -> int:
    """Returns the smallest numeric post ID seen, for backlog pagination."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT post_id FROM processed_posts"
        ).fetchall()
    # post_id format: "ChannelName/1234" — extract the number
    ids = []
    for (pid,) in rows:
        try:
            ids.append(int(pid.split("/")[-1]))
        except Exception:
            pass
    return min(ids) if ids else 0


# ── Uploaded Files (Pinterest) ───────────────────────────────────────────────

def is_file_uploaded(filename: str) -> bool:
    """Returns True if this file was already uploaded to Pinterest."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM uploaded_files WHERE filename = ?", (filename,)
        ).fetchone()
    return row is not None


def is_image_url_uploaded(image_url: str) -> bool:
    """Double-check: returns True if this image URL was already posted."""
    if not image_url:
        return False
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM uploaded_files WHERE image_url = ?", (image_url,)
        ).fetchone()
    return row is not None


def mark_file_uploaded(filename: str, title: str = "", anime_name: str = "", image_url: str = ""):
    """Records that a file has been successfully uploaded to Pinterest."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO uploaded_files (filename, title, anime_name, image_url) VALUES (?, ?, ?, ?)",
            (filename, title, anime_name, image_url),
        )
        conn.commit()


def get_today_uploads(today_str: str = None) -> list:
    """Returns all pins uploaded today (IST) as list of dicts.
    Uses IST-adjusted date (same as count_posts_today) to avoid UTC/IST mismatch.
    Accepts optional today_str (IST date 'YYYY-MM-DD') or auto-computes it.
    """
    if not today_str:
        # Compute IST date inline to avoid circular imports
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT title, anime_name, image_url, uploaded_at
            FROM uploaded_files
            WHERE date(uploaded_at, '+5 hours', '+30 minutes') = ?
            ORDER BY uploaded_at DESC
        """, (today_str,)).fetchall()
    return [
        {"title": r[0], "anime": r[1], "image_url": r[2], "uploaded_at": r[3]}
        for r in rows
    ]


def get_all_time_stats() -> dict:
    """Returns all-time upload statistics."""
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]
        today = conn.execute(
            "SELECT COUNT(*) FROM uploaded_files WHERE date(uploaded_at) = date('now')"
        ).fetchone()[0]
        top = conn.execute("""
            SELECT anime_name, COUNT(*) as cnt FROM uploaded_files
            WHERE anime_name IS NOT NULL AND anime_name != ''
            GROUP BY anime_name ORDER BY cnt DESC LIMIT 5
        """).fetchall()
    return {"total": total, "today": today, "top_anime": top}


def get_uploaded_count() -> int:
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]


# ── Pin Queue (multi-day backlog) ────────────────────────────────────────────

def enqueue_pin(post_id: str, image_path: str, title: str, description: str,
                link: str, anime_name: str, image_url: str = "",
                board_id: str = "",
                priority: int = 0, scheduled_date: str = "") -> bool:
    """
    Add a pin to the persistent queue.
    priority=1 → new image (posted before backlog)
    priority=0 → backlog image
    Returns True if added, False if already in queue.
    """
    try:
        from amazon_search import preflight_validate_destination
        link = preflight_validate_destination(link, anime_name=anime_name, title=title)
    except Exception:
        pass

    with _get_conn() as conn:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO pin_queue
                    (post_id, image_path, title, description, link, anime_name,
                     image_url, board_id, priority, scheduled_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (post_id, image_path, title, description, link, anime_name,
                  image_url, board_id, priority, scheduled_date))
            conn.commit()
            return conn.execute(
                "SELECT changes()"
            ).fetchone()[0] > 0
        except Exception as e:
            logger.error(f"[DB] enqueue_pin error: {e}")
            return False


def get_next_queued_pin(today_str: str) -> dict | None:
    """
    Fetch the next pin to post.
    Priority: new images (priority=1) first, then backlog (priority=0).
    Only returns pins scheduled for today or earlier.
    """
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT id, post_id, image_path, title, description, link,
                   anime_name, image_url, priority, board_id
            FROM pin_queue
            WHERE scheduled_date <= ?
            ORDER BY priority DESC, id ASC
            LIMIT 1
        """, (today_str,)).fetchone()
    if not row:
        return None
    link_val = row[5]
    try:
        from amazon_search import preflight_validate_destination
        link_val = preflight_validate_destination(link_val, anime_name=row[6], title=row[3])
    except Exception:
        pass

    return {
        "id": row[0], "post_id": row[1], "image_path": row[2],
        "title": row[3], "description": row[4], "link": link_val,
        "anime_name": row[6], "image_url": row[7], "priority": row[8],
        "board_id": row[9] or "",
    }


def remove_queued_pin(pin_id: int):
    """Remove a pin from the queue after it has been posted."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM pin_queue WHERE id = ?", (pin_id,))
        conn.commit()


def get_queue_counts() -> dict:
    """Returns counts of new vs backlog pins in queue."""
    with _get_conn() as conn:
        new_count = conn.execute(
            "SELECT COUNT(*) FROM pin_queue WHERE priority = 1"
        ).fetchone()[0]
        backlog_count = conn.execute(
            "SELECT COUNT(*) FROM pin_queue WHERE priority = 0"
        ).fetchone()[0]
    return {"new": new_count, "backlog": backlog_count, "total": new_count + backlog_count}


def count_posts_today(today_str: str) -> int:
    """Count how many pins were actually uploaded to Pinterest today (IST).
    Uses IST-adjusted date so midnight-5:30 AM UTC posts count for the correct IST day."""
    with _get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM uploaded_files "
            "WHERE date(uploaded_at, '+5 hours', '+30 minutes') = ?",
            (today_str,)
        ).fetchone()[0]


def count_posts_on_utc_date(utc_date_str: str) -> int:
    """Count how many pins were uploaded on a specific UTC calendar date.
    Used by startup recovery to compare UTC slots vs UTC posts without
    IST/UTC cross-midnight timezone mismatch.
    utc_date_str: 'YYYY-MM-DD' in UTC.
    """
    with _get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM uploaded_files "
            "WHERE date(uploaded_at) = ?",
            (utc_date_str,)
        ).fetchone()[0]


def increment_retry_count(pin_id: int) -> int:
    """
    Increments retry_count for a queued pin after a failed upload attempt.
    Returns the new retry_count value.
    Used by scheduler to decide whether to keep retrying or drop the pin.
    """
    with _get_conn() as conn:
        conn.execute(
            "UPDATE pin_queue SET retry_count = retry_count + 1 WHERE id = ?",
            (pin_id,)
        )
        conn.commit()
        new_count = conn.execute(
            "SELECT retry_count FROM pin_queue WHERE id = ?", (pin_id,)
        ).fetchone()
    return new_count[0] if new_count else 0


def update_pin_image_path(pin_id: int, new_image_path: str):
    """
    Updates the image_path for a queued pin after the file has been
    re-downloaded (e.g. after Render's ephemeral FS wiped it on restart).
    """
    with _get_conn() as conn:
        conn.execute(
            "UPDATE pin_queue SET image_path = ? WHERE id = ?",
            (new_image_path, pin_id),
        )
        conn.commit()


def get_weekly_stats() -> dict:
    """
    Returns posting statistics for the last 7 days.
    Used by the /analytics Telegram command.
    Returns:
      - daily_counts: list of {date, count} for last 7 days
      - top_anime: top 5 anime by pin count this week
      - total_week: total pins this week
      - total_all_time: all-time total pins
      - failed_retries: pins currently stuck in retry queue (retry_count > 0)
    """
    with _get_conn() as conn:
        # Daily counts for last 7 days
        daily = conn.execute("""
            SELECT date(uploaded_at) as day, COUNT(*) as cnt
            FROM uploaded_files
            WHERE uploaded_at >= date('now', '-6 days')
            GROUP BY day
            ORDER BY day ASC
        """).fetchall()

        # Top 5 anime this week
        top_anime = conn.execute("""
            SELECT anime_name, COUNT(*) as cnt
            FROM uploaded_files
            WHERE uploaded_at >= date('now', '-6 days')
              AND anime_name IS NOT NULL AND anime_name != ''
            GROUP BY anime_name
            ORDER BY cnt DESC
            LIMIT 5
        """).fetchall()

        # Total this week
        total_week = conn.execute("""
            SELECT COUNT(*) FROM uploaded_files
            WHERE uploaded_at >= date('now', '-6 days')
        """).fetchone()[0]

        # All-time total
        total_all = conn.execute(
            "SELECT COUNT(*) FROM uploaded_files"
        ).fetchone()[0]

        # Pins stuck in retry loop
        failed = conn.execute(
            "SELECT COUNT(*) FROM pin_queue WHERE retry_count > 0"
        ).fetchone()[0]

    return {
        "daily_counts": [{"date": r[0], "count": r[1]} for r in daily],
        "top_anime":    [(r[0], r[1]) for r in top_anime],
        "total_week":   total_week,
        "total_all_time": total_all,
        "failed_retries": failed,
    }


# ── Queue management helpers (used by Telegram bot commands) ─────────────────

def clear_pin_queue() -> int:
    """
    Deletes ALL rows from pin_queue.
    Used by the Telegram /clearqueue command.
    Returns number of rows deleted.
    """
    with _get_conn() as conn:
        conn.execute("DELETE FROM pin_queue")
        deleted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    logger.info(f"[DB] pin_queue cleared: {deleted} pins removed.")
    return deleted


def get_queue_detail() -> list[dict]:
    """
    Returns a per-date breakdown of the pin queue for the /queue Telegram command.
    Each entry: {scheduled_date, new_count, backlog_count}
    Sorted by date ascending.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT
                scheduled_date,
                SUM(CASE WHEN priority = 1 THEN 1 ELSE 0 END) AS new_count,
                SUM(CASE WHEN priority = 0 THEN 1 ELSE 0 END) AS backlog_count
            FROM pin_queue
            GROUP BY scheduled_date
            ORDER BY scheduled_date ASC
        """).fetchall()
    return [
        {"date": r[0], "new": r[1], "backlog": r[2]}
        for r in rows
    ]


def get_upcoming_queued_pins(limit: int = 5) -> list[dict]:
    """
    Returns the next `limit` pins in line to be posted, ordered by priority DESC, id ASC.
    Used by /queue to show the titles and anime names of upcoming pins.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, anime_name, priority, scheduled_date
            FROM pin_queue
            ORDER BY priority DESC, id ASC
            LIMIT ?
        """, (limit,)).fetchall()
    return [
        {
            "id": r[0],
            "title": r[1],
            "anime_name": r[2],
            "priority": r[3],
            "scheduled_date": r[4]
        }
        for r in rows
    ]



def pop_next_pin_for_immediate_post() -> dict | None:
    """
    Fetches and REMOVES the next highest-priority pin from the queue,
    regardless of its scheduled_date. Used by /post_now Telegram command.
    Returns pin dict or None if queue is empty.
    """
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT id, post_id, image_path, title, description, link,
                   anime_name, image_url, priority, board_id
            FROM pin_queue
            ORDER BY priority DESC, id ASC
            LIMIT 1
        """).fetchone()
        if not row:
            return None
        # Remove from queue immediately (optimistic — upload may still fail)
        conn.execute("DELETE FROM pin_queue WHERE id = ?", (row[0],))
        conn.commit()
    link_val = row[5]
    try:
        from amazon_search import preflight_validate_destination
        link_val = preflight_validate_destination(link_val, anime_name=row[6], title=row[3])
    except Exception:
        pass

    return {
        "id": row[0], "post_id": row[1], "image_path": row[2],
        "title": row[3], "description": row[4], "link": link_val,
        "anime_name": row[6], "image_url": row[7], "priority": row[8],
        "board_id": row[9] or "",
    }


# ── Click Tracking & Affiliate Analytics ──────────────────────────────────────

def create_tracked_link(target_url: str, anime_name: str = "", title: str = "") -> str:
    """
    Stores a destination URL and returns a short code.
    If target_url is a direct product (/dp/ASIN), embeds the ASIN (e.g. 'dp_B0CHR8R1L3')
    making the link stateless and 100% resilient across server restarts.
    """
    import hashlib, time, random, re

    asin_match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', target_url)
    if asin_match:
        code = f"dp_{asin_match.group(1)}"
    else:
        seed = f"{target_url}_{anime_name}_{time.time()}_{random.random()}"
        code = hashlib.md5(seed.encode()).hexdigest()[:6]

    with _get_conn() as conn:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO tracked_links (code, target_url, anime_name, title)
                VALUES (?, ?, ?, ?)
            """, (code, target_url, anime_name, title))
            conn.commit()
        except Exception as e:
            logger.error(f"[DB] Error creating tracked link: {e}")
    return code


def record_link_click(code: str, user_agent: str = "", referrer: str = "") -> tuple[str, str, str]:
    """
    Records a click for the given link code and returns (target_url, anime_name, title).
    If code not found in DB:
      1. Reconstructs direct product URL if code contains ASIN (stateless recovery).
      2. Otherwise falls back to top-rated anime figures & posters category.
    """
    import re
    from config import AMAZON_AFFILIATE_TAG
    tag = AMAZON_AFFILIATE_TAG or "animeasthet06-21"

    with _get_conn() as conn:
        row = conn.execute("""
            SELECT target_url, anime_name, title FROM tracked_links WHERE code = ?
        """, (code,)).fetchone()

        if row:
            target_url, anime_name, title = row[0], row[1] or "Anime", row[2] or "Anime Merch"
            conn.execute("""
                INSERT INTO link_clicks (code, anime_name, title, user_agent, referrer)
                VALUES (?, ?, ?, ?, ?)
            """, (code, anime_name, title, user_agent[:255], referrer[:255]))
            conn.commit()
            return target_url, anime_name, title

        # Stateless self-healing recovery: reconstruct direct product URL from ASIN code
        asin = None
        if code.startswith("dp_") and len(code) == 13 and code[3:].isalnum():
            asin = code[3:]
        elif len(code) == 10 and code.isalnum() and code.isupper():
            asin = code

        if asin:
            direct_product_url = (
                f"https://www.amazon.in/dp/{asin}"
                f"?tag={tag}&linkCode=ogi&th=1&psc=1"
            )
            logger.info(f"[DB] Reconstructed stateless product link for ASIN: {asin}")
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO tracked_links (code, target_url, anime_name, title)
                    VALUES (?, ?, ?, ?)
                """, (code, direct_product_url, "Anime", "Anime Product"))
                conn.execute("""
                    INSERT INTO link_clicks (code, anime_name, title, user_agent, referrer)
                    VALUES (?, ?, ?, ?, ?)
                """, (code, "Anime", "Anime Product", user_agent[:255], referrer[:255]))
                conn.commit()
            except Exception:
                pass
            return direct_product_url, "Anime", "Anime Product"

        # Best-in-class fallback: clean anime merchandise search (NO broken category node)
        fallback_url = (
            f"https://www.amazon.in/s?k=anime+merchandise+poster+figure"
            f"&tag={tag}&sort=review-rank"
        )
        return fallback_url, "Anime", "Anime Merch"


def count_clicks_today() -> int:
    """Returns number of affiliate link clicks received today (IST)."""
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM link_clicks
            WHERE date(clicked_at, '+5 hours', '+30 minutes') = date('now', '+5 hours', '+30 minutes')
        """).fetchone()
        return row[0] if row else 0


def get_click_stats() -> dict:
    """
    Returns full click analytics and conversion estimates for Telegram commands.
    """
    with _get_conn() as conn:
        today_clicks = conn.execute("""
            SELECT COUNT(*) FROM link_clicks
            WHERE date(clicked_at, '+5 hours', '+30 minutes') = date('now', '+5 hours', '+30 minutes')
        """).fetchone()[0]

        week_clicks = conn.execute("""
            SELECT COUNT(*) FROM link_clicks
            WHERE clicked_at >= date('now', '-6 days')
        """).fetchone()[0]

        total_clicks = conn.execute("""
            SELECT COUNT(*) FROM link_clicks
        """).fetchone()[0]

        top_anime = conn.execute("""
            SELECT anime_name, COUNT(*) as cnt
            FROM link_clicks
            WHERE anime_name IS NOT NULL AND anime_name != ''
            GROUP BY anime_name
            ORDER BY cnt DESC
            LIMIT 5
        """).fetchall()

        top_pins = conn.execute("""
            SELECT title, anime_name, COUNT(*) as cnt
            FROM link_clicks
            WHERE title IS NOT NULL AND title != ''
            GROUP BY title
            ORDER BY cnt DESC
            LIMIT 3
        """).fetchall()

    # Calculate estimated conversions based on standard e-commerce metrics (2-4% CR, ₹35-80 avg commission)
    est_orders_min = max(0, int(week_clicks * 0.02))
    est_orders_max = max(1 if week_clicks >= 5 else 0, int(week_clicks * 0.05))
    est_revenue_min = est_orders_min * 35
    est_revenue_max = est_orders_max * 95

    return {
        "today": today_clicks,
        "week": week_clicks,
        "total": total_clicks,
        "top_anime": [(r[0], r[1]) for r in top_anime],
        "top_pins": [(r[0], r[1], r[2]) for r in top_pins],
        "est_orders_min": est_orders_min,
        "est_orders_max": est_orders_max,
        "est_revenue_min": est_revenue_min,
        "est_revenue_max": est_revenue_max,
    }


# ── Metadata & 3-Day Health Check Helpers ─────────────────────────────────────

def get_metadata(key: str, default: str = "") -> str:
    """Retrieve a persistent setting or state string by key."""
    with _get_conn() as conn:
        row = conn.execute("SELECT value FROM bot_metadata WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default


def set_metadata(key: str, value: str):
    """Store or update a persistent setting or state string."""
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO bot_metadata (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, str(value)))
        conn.commit()


def get_3day_stats() -> dict:
    """Returns analytics for the 3-day health diagnostic report."""
    with _get_conn() as conn:
        pins_3d = conn.execute("""
            SELECT COUNT(*) FROM uploaded_files
            WHERE uploaded_at >= datetime('now', '-3 days')
        """).fetchone()[0]

        clicks_3d = conn.execute("""
            SELECT COUNT(*) FROM link_clicks
            WHERE clicked_at >= datetime('now', '-3 days')
        """).fetchone()[0]

        total_pins = conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]
        total_clicks = conn.execute("SELECT COUNT(*) FROM link_clicks").fetchone()[0]

        failed_retries = conn.execute(
            "SELECT COUNT(*) FROM pin_queue WHERE retry_count > 0"
        ).fetchone()[0]

    q = get_queue_counts()

    est_orders = max(0, int(clicks_3d * 0.03))
    est_rev_min = est_orders * 40
    est_rev_max = max(est_orders * 110, 0 if clicks_3d == 0 else 60)

    return {
        "pins_3d": pins_3d,
        "clicks_3d": clicks_3d,
        "total_pins": total_pins,
        "total_clicks": total_clicks,
        "queue": q,
        "failed_retries": failed_retries,
        "est_orders": est_orders,
        "est_rev_min": est_rev_min,
        "est_rev_max": est_rev_max,
    }


# ── Self-Healing Link Helpers ─────────────────────────────────────────────────

def get_all_tracked_links(limit: int = 100) -> list[dict]:
    """
    Returns list of all tracked affiliate links for health auditing.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, code, target_url, anime_name, title, created_at
            FROM tracked_links
            ORDER BY id DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [
            {
                "id": r[0],
                "code": r[1],
                "target_url": r[2],
                "anime_name": r[3] or "Anime",
                "title": r[4] or "Anime Merch",
                "created_at": r[5],
            }
            for r in rows
        ]


def update_tracked_link_target(code: str, new_target_url: str) -> bool:
    """
    Updates the target destination URL for a given tracked short link code.
    Used by the Self-Healing Link Engine to repair dead 404 links.
    """
    with _get_conn() as conn:
        cursor = conn.execute("""
            UPDATE tracked_links
            SET target_url = ?
            WHERE code = ?
        """, (new_target_url, code))
        conn.commit()
        return cursor.rowcount > 0


def get_tracked_target_url(link_or_code: str) -> str:
    """
    Returns the destination Amazon URL for a given tracking URL or code.
    If not a tracked redirect, returns link_or_code as-is.
    """
    if not link_or_code:
        return ""
    code = link_or_code.split("/r/")[-1].strip() if "/r/" in link_or_code else link_or_code.strip()
    with _get_conn() as conn:
        row = conn.execute("SELECT target_url FROM tracked_links WHERE code = ?", (code,)).fetchone()
        return row[0] if row else link_or_code


# ── Multi-Stock Upload Tracking ──────────────────────────────────────────────

def mark_stock_uploaded(platform: str, filename: str, title: str = "", status: str = "success"):
    """Record an upload attempt/success for a stock photography platform."""
    with _get_conn() as conn:
        conn.execute("""
            INSERT INTO stock_uploads (platform, filename, title, status)
            VALUES (?, ?, ?, ?)
        """, (platform.lower().strip(), filename, title, status))
        conn.commit()


def is_stock_uploaded(platform: str, filename: str) -> bool:
    """Check if a file was already successfully uploaded to a given platform."""
    if not filename:
        return False
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT 1 FROM stock_uploads
            WHERE platform = ? AND filename = ? AND status = 'success'
        """, (platform.lower().strip(), filename)).fetchone()
        return row is not None


def count_stock_posts_today(platform: str, today_str: str = "") -> int:
    """Count how many items were uploaded to a given platform today (IST)."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) FROM stock_uploads
            WHERE platform = ? AND status = 'success'
              AND date(uploaded_at, '+5 hours', '+30 minutes') = ?
        """, (platform.lower().strip(), today_str)).fetchone()
        return row[0] if row else 0


def get_stock_stats_today(today_str: str = "") -> dict:
    """Returns today's upload count for each stock platform."""
    platforms = ["shutterstock", "adobe", "freepik", "depositphotos", "dreamstime", "123rf"]
    return {p: count_stock_posts_today(p, today_str) for p in platforms}


# Initialize on import
init_db()



