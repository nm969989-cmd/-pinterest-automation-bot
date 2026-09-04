"""
JSONBin.io Sync Module
======================
Persists bot state to JSONBin.io cloud storage so data survives
Render restarts. Uses SQLite locally for speed and syncs to cloud
every 30 minutes and on startup.

Free tier: 10,000 requests/month (~1,450 used = 85% remaining)
"""

import os
import time
import threading
import requests
from logger import get_logger

logger = get_logger(__name__)

JSONBIN_API_KEY = os.getenv("JSONBIN_API_KEY", "")
JSONBIN_BIN_ID  = os.getenv("JSONBIN_BIN_ID", "")

_BASE_URL = "https://api.jsonbin.io/v3/b"
_SYNC_INTERVAL = 30 * 60  # sync every 30 minutes


def _headers() -> dict:
    return {
        "X-Master-Key": JSONBIN_API_KEY,
        "Content-Type": "application/json",
    }


def _is_configured() -> bool:
    return bool(JSONBIN_API_KEY and JSONBIN_BIN_ID)


# ── Read from JSONBin ─────────────────────────────────────────────────────────

def load_cloud_state() -> dict | None:
    """
    Load persisted state from JSONBin on startup.
    Returns dict with keys: processed_posts, uploaded_files, pin_queue
    Returns None if JSONBin not configured or request fails.
    """
    if not _is_configured():
        return None
    try:
        r = requests.get(
            f"{_BASE_URL}/{JSONBIN_BIN_ID}/latest",
            headers=_headers(),
            timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("record", {})
            logger.info(
                f"[JSONBin] Loaded cloud state: "
                f"{len(data.get('processed_posts', []))} posts, "
                f"{len(data.get('uploaded_files', []))} uploads, "
                f"{len(data.get('pin_queue', []))} queued, "
                f"{len(data.get('tracked_links', []))} tracked links"
            )
            return data
        else:
            logger.warning(f"[JSONBin] Load failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        logger.error(f"[JSONBin] Load error: {e}")
    return None


# ── Write to JSONBin ──────────────────────────────────────────────────────────

def save_cloud_state() -> bool:
    """
    Save current SQLite state to JSONBin cloud.
    Called periodically and on shutdown.
    """
    if not _is_configured():
        return False
    try:
        import sqlite3
        from database import DB_PATH

        with sqlite3.connect(DB_PATH) as conn:
            # Processed posts (last 500 only to keep JSON small)
            posts = [
                row[0] for row in conn.execute(
                    "SELECT post_id FROM processed_posts ORDER BY processed_at DESC LIMIT 500"
                ).fetchall()
            ]
            # Uploaded files
            uploads = [
                {"filename": r[0], "title": r[1], "anime_name": r[2],
                 "image_url": r[3], "uploaded_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, title, anime_name, image_url, uploaded_at "
                    "FROM uploaded_files ORDER BY uploaded_at DESC LIMIT 500"
                ).fetchall()
            ]
            # Pin queue
            queue = [
                {"post_id": r[0], "image_path": r[1], "title": r[2],
                 "description": r[3], "link": r[4], "anime_name": r[5],
                 "image_url": r[6], "board_id": r[7], "priority": r[8],
                 "scheduled_date": r[9]}
                for r in conn.execute(
                    "SELECT post_id, image_path, title, description, link, "
                    "anime_name, image_url, board_id, priority, scheduled_date FROM pin_queue"
                ).fetchall()
            ]
            # Bot metadata (jitter, health check dates, etc.)
            metadata = [
                {"key": r[0], "value": r[1]}
                for r in conn.execute(
                    "SELECT key, value FROM bot_metadata"
                ).fetchall()
            ]
            # Tracked links (last 500 links so short redirects survive restarts)
            tracked = [
                {"code": r[0], "target_url": r[1], "anime_name": r[2], "title": r[3], "created_at": r[4]}
                for r in conn.execute(
                    "SELECT code, target_url, anime_name, title, created_at FROM tracked_links ORDER BY created_at DESC LIMIT 500"
                ).fetchall()
            ]

        payload = {
            "processed_posts": posts,
            "uploaded_files":  uploads,
            "pin_queue":       queue,
            "bot_metadata":    metadata,
            "tracked_links":   tracked,
        }

        r = requests.put(
            f"{_BASE_URL}/{JSONBIN_BIN_ID}",
            json=payload,
            headers=_headers(),
            timeout=10
        )
        if r.status_code == 200:
            logger.info(
                f"[JSONBin] Synced: {len(posts)} posts, "
                f"{len(uploads)} uploads, {len(queue)} queued, {len(tracked)} tracked links"
            )
            return True
        else:
            logger.warning(f"[JSONBin] Save failed: {r.status_code} {r.text[:100]}")
    except Exception as e:
        logger.error(f"[JSONBin] Save error: {e}")
    return False


# ── Restore SQLite from JSONBin on startup ────────────────────────────────────

def restore_db_from_cloud():
    """
    On startup, load JSONBin state into SQLite so the bot
    remembers everything even after a Render restart.
    """
    if not _is_configured():
        logger.info("[JSONBin] Not configured — skipping cloud restore.")
        return

    state = load_cloud_state()
    if not state:
        logger.info("[JSONBin] No cloud state found — starting fresh.")
        return

    try:
        import sqlite3
        from database import DB_PATH, init_db
        init_db()  # ensure tables exist

        with sqlite3.connect(DB_PATH) as conn:
            # Restore processed posts
            posts = state.get("processed_posts", [])
            conn.executemany(
                "INSERT OR IGNORE INTO processed_posts (post_id) VALUES (?)",
                [(p,) for p in posts]
            )
            # Restore uploaded files — preserve original uploaded_at timestamp
            uploads = state.get("uploaded_files", [])
            conn.executemany(
                "INSERT OR IGNORE INTO uploaded_files "
                "(filename, title, anime_name, image_url, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                [(u.get("filename",""), u.get("title",""),
                  u.get("anime_name",""), u.get("image_url",""),
                  u.get("uploaded_at") or "2000-01-01 00:00:00")  # preserve original timestamp!
                 for u in uploads]
            )
            # Restore pin queue (including board_id)
            queue = state.get("pin_queue", [])
            conn.executemany(
                "INSERT OR IGNORE INTO pin_queue "
                "(post_id, image_path, title, description, link, "
                "anime_name, image_url, board_id, priority, scheduled_date) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(q.get("post_id",""), q.get("image_path",""),
                  q.get("title",""), q.get("description",""),
                  q.get("link",""), q.get("anime_name",""),
                  q.get("image_url",""), q.get("board_id",""),
                  q.get("priority",0), q.get("scheduled_date",""))
                 for q in queue]
            )
            # Restore bot_metadata (jitter, health check dates, etc.)
            metadata = state.get("bot_metadata", [])
            if metadata:
                conn.executemany(
                    "INSERT OR REPLACE INTO bot_metadata (key, value) VALUES (?, ?)",
                    [(m.get("key",""), m.get("value","")) for m in metadata if m.get("key")]
                )
            # Restore tracked links
            links = state.get("tracked_links", [])
            if links:
                conn.executemany(
                    "INSERT OR IGNORE INTO tracked_links "
                    "(code, target_url, anime_name, title, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(l.get("code",""), l.get("target_url",""),
                      l.get("anime_name",""), l.get("title",""),
                      l.get("created_at") or "2000-01-01 00:00:00")
                     for l in links if l.get("code") and l.get("target_url")]
                )
            conn.commit()


        logger.info(
            f"[JSONBin] Restored from cloud: {len(posts)} posts, "
            f"{len(uploads)} uploads, {len(queue)} queued, {len(links)} tracked links. "
            f"No duplicates will be re-posted!"
        )
    except Exception as e:
        logger.error(f"[JSONBin] Restore error: {e}")


# ── Background sync thread ────────────────────────────────────────────────────

def start_sync_thread():
    """Start background thread that syncs to JSONBin every 30 minutes."""
    if not _is_configured():
        logger.info("[JSONBin] Not configured — cloud sync disabled.")
        return

    def _loop():
        while True:
            time.sleep(_SYNC_INTERVAL)
            save_cloud_state()

    t = threading.Thread(target=_loop, daemon=True, name="JSONBinSync")
    t.start()
    logger.info(f"[JSONBin] Cloud sync started — saves every {_SYNC_INTERVAL//60} min.")
