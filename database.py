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
        # Add new columns if upgrading from older schema
        try:
            conn.execute("ALTER TABLE uploaded_files ADD COLUMN anime_name TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE uploaded_files ADD COLUMN image_url TEXT")
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


# ── Uploaded Files (Pinterest) ───────────────────────────────────────────────

def is_file_uploaded(filename: str) -> bool:
    """Returns True if this file was already uploaded to Pinterest."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM uploaded_files WHERE filename = ?", (filename,)
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
        # Most posted anime
        top = conn.execute("""
            SELECT anime_name, COUNT(*) as cnt FROM uploaded_files
            WHERE anime_name IS NOT NULL AND anime_name != ''
            GROUP BY anime_name ORDER BY cnt DESC LIMIT 5
        """).fetchall()
    return {"total": total, "today": today, "top_anime": top}


def get_uploaded_count() -> int:
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]


# Initialize on import
init_db()
