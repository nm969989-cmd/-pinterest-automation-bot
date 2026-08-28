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
                filename TEXT PRIMARY KEY,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                title TEXT
            )
        """)
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


# ── Uploaded Files (Pinterest) ───────────────────────────────────────────────

def is_file_uploaded(filename: str) -> bool:
    """Returns True if this file was already uploaded to Pinterest."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM uploaded_files WHERE filename = ?", (filename,)
        ).fetchone()
    return row is not None


def mark_file_uploaded(filename: str, title: str = ""):
    """Records that a file has been successfully uploaded to Pinterest."""
    with _get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO uploaded_files (filename, title) VALUES (?, ?)",
            (filename, title),
        )
        conn.commit()


def get_uploaded_count() -> int:
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]


# Initialize on import
init_db()
