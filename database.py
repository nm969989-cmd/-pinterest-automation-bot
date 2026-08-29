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
                priority    INTEGER DEFAULT 0,   -- 1=new, 0=backlog
                scheduled_date TEXT,             -- YYYY-MM-DD when to post
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Add new columns if upgrading from older schema
        for col in ["anime_name TEXT", "image_url TEXT"]:
            try:
                conn.execute(f"ALTER TABLE uploaded_files ADD COLUMN {col}")
            except Exception:
                pass
        conn.commit()
    logger.info(f"Database initialized at: {DB_PATH}")


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


def get_today_uploads() -> list:
    """Returns all pins uploaded today as list of dicts."""
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT title, anime_name, image_url, uploaded_at
            FROM uploaded_files
            WHERE date(uploaded_at) = date('now')
            ORDER BY uploaded_at DESC
        """).fetchall()
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
                priority: int = 0, scheduled_date: str = "") -> bool:
    """
    Add a pin to the persistent queue.
    priority=1 → new image (posted before backlog)
    priority=0 → backlog image
    Returns True if added, False if already in queue.
    """
    with _get_conn() as conn:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO pin_queue
                    (post_id, image_path, title, description, link, anime_name,
                     image_url, priority, scheduled_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (post_id, image_path, title, description, link, anime_name,
                  image_url, priority, scheduled_date))
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
                   anime_name, image_url, priority
            FROM pin_queue
            WHERE scheduled_date <= ?
            ORDER BY priority DESC, id ASC
            LIMIT 1
        """, (today_str,)).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "post_id": row[1], "image_path": row[2],
        "title": row[3], "description": row[4], "link": row[5],
        "anime_name": row[6], "image_url": row[7], "priority": row[8]
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
    """Count how many pins were actually uploaded to Pinterest today."""
    with _get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM uploaded_files WHERE date(uploaded_at) = ?",
            (today_str,)
        ).fetchone()[0]


# Initialize on import
init_db()
