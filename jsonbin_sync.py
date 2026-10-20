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
        import json
        import sqlite3
        from database import DB_PATH

        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            # Processed posts (last 200 post_ids to keep payload well within 100KB budget)
            posts = [
                row[0] for row in conn.execute(
                    "SELECT post_id FROM processed_posts ORDER BY processed_at DESC LIMIT 200"
                ).fetchall()
            ]
            # Uploaded files (last 150 with fingerprints for persistent deduplication)
            uploads = [
                {"filename": r[0], "title": r[1], "anime_name": r[2],
                 "image_url": r[3], "uploaded_at": r[4],
                 "sha256": r[5] or "", "phash": r[6] or ""}
                for r in conn.execute(
                    "SELECT filename, title, anime_name, image_url, uploaded_at, sha256, phash "
                    "FROM uploaded_files ORDER BY uploaded_at DESC LIMIT 150"
                ).fetchall()
            ]
            # Pin queue (preserve ALL pending queue items - vital state!)
            queue = [
                {"post_id": r[0], "image_path": r[1], "title": r[2],
                 "description": r[3], "link": r[4], "anime_name": r[5],
                 "image_url": r[6], "board_id": r[7], "priority": r[8],
                 "scheduled_date": r[9],
                 "sha256": r[10] or "", "phash": r[11] or ""}
                for r in conn.execute(
                    "SELECT post_id, image_path, title, description, link, "
                    "anime_name, image_url, board_id, priority, scheduled_date, sha256, phash FROM pin_queue"
                ).fetchall()
            ]
            # Bot metadata (jitter, health check dates, etc.)
            metadata = [
                {"key": r[0], "value": r[1]}
                for r in conn.execute(
                    "SELECT key, value FROM bot_metadata"
                ).fetchall()
            ]
            # Tracked links (last 60 links for recent redirect continuity)
            tracked = [
                {"code": r[0], "target_url": r[1], "anime_name": r[2], "title": r[3], "created_at": r[4]}
                for r in conn.execute(
                    "SELECT code, target_url, anime_name, title, created_at FROM tracked_links ORDER BY created_at DESC LIMIT 60"
                ).fetchall()
            ]
            # Secondary platforms (keep last 25 items each for lightweight deduplication)
            arena = [
                {"filename": r[0], "block_id": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, block_id, title, image_url, posted_at FROM arena_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            bluesky = [
                {"filename": r[0], "post_uri": r[1], "post_cid": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_uri, post_cid, image_url, posted_at FROM bluesky_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            raindrop = [
                {"filename": r[0], "drop_id": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, drop_id, title, image_url, posted_at FROM raindrop_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            mastodon = [
                {"filename": r[0], "post_url": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_url, title, image_url, posted_at FROM mastodon_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            pixelfed = [
                {"filename": r[0], "post_url": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_url, title, image_url, posted_at FROM pixelfed_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            freeimage = [
                {"filename": r[0], "post_url": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_url, title, image_url, posted_at FROM freeimage_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            imghippo = [
                {"filename": r[0], "post_url": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_url, title, image_url, posted_at FROM imghippo_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]
            wallhaven = [
                {"filename": r[0], "post_url": r[1], "title": r[2], "image_url": r[3], "posted_at": r[4]}
                for r in conn.execute(
                    "SELECT filename, post_url, title, image_url, posted_at FROM wallhaven_posts ORDER BY id DESC LIMIT 25"
                ).fetchall()
            ]

        payload = {
            "processed_posts": posts,
            "uploaded_files":  uploads,
            "pin_queue":       queue,
            "bot_metadata":    metadata,
            "tracked_links":   tracked,
            "arena_posts":     arena,
            "bluesky_posts":   bluesky,
            "raindrop_posts":  raindrop,
            "mastodon_posts":  mastodon,
            "pixelfed_posts":  pixelfed,
            "freeimage_posts": freeimage,
            "imghippo_posts":  imghippo,
            "wallhaven_posts": wallhaven,
        }

        # ── Budget Enforcer: Guarantee strictly under 90KB (JSONBin free limit = 100KB) ──
        raw_json = json.dumps(payload, separators=(',', ':'))
        payload_bytes = len(raw_json.encode('utf-8'))

        if payload_bytes > 90 * 1024:
            logger.info(f"[JSONBin] Payload size ({payload_bytes / 1024:.1f}KB) exceeds 90KB target. Trimming non-essential logs...")
            # Tier 1 pruning: reduce secondary logs to 5 items each
            for k in ["arena_posts", "bluesky_posts", "raindrop_posts", "mastodon_posts", "pixelfed_posts", "freeimage_posts", "imghippo_posts", "wallhaven_posts"]:
                payload[k] = payload[k][:5]
            payload["uploaded_files"] = payload["uploaded_files"][:30]
            payload["tracked_links"]  = payload["tracked_links"][:30]
            raw_json = json.dumps(payload, separators=(',', ':'))
            payload_bytes = len(raw_json.encode('utf-8'))

            # Tier 2 pruning if still over 90KB
            if payload_bytes > 90 * 1024:
                payload["processed_posts"] = payload["processed_posts"][:100]
                raw_json = json.dumps(payload, separators=(',', ':'))
                payload_bytes = len(raw_json.encode('utf-8'))

        logger.info(f"[JSONBin] Uploading budgeted payload ({payload_bytes / 1024:.1f}KB / 100KB free limit)")

        r = requests.put(
            f"{_BASE_URL}/{JSONBIN_BIN_ID}",
            data=raw_json,
            headers=_headers(),
            timeout=15
        )
        if r.status_code == 200:
            logger.info(
                f"[JSONBin] Synced successfully: {len(payload['processed_posts'])} posts, {len(payload['uploaded_files'])} uploads, "
                f"{len(queue)} queued, {len(payload['tracked_links'])} tracked links ({payload_bytes / 1024:.1f}KB)"
            )
            return True
        else:
            logger.warning(f"[JSONBin] Save failed: {r.status_code} {r.text[:120]}")
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
            # Restore uploaded files — preserve original uploaded_at timestamp and fingerprints
            uploads = state.get("uploaded_files", [])
            conn.executemany(
                "INSERT OR IGNORE INTO uploaded_files "
                "(filename, title, anime_name, image_url, uploaded_at, sha256, phash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(u.get("filename",""), u.get("title",""),
                  u.get("anime_name",""), u.get("image_url",""),
                  u.get("uploaded_at") or "2000-01-01 00:00:00",
                  u.get("sha256", ""), u.get("phash", ""))
                 for u in uploads]
            )
            # Restore pin queue (including board_id and fingerprints)
            queue = state.get("pin_queue", [])
            conn.executemany(
                "INSERT OR IGNORE INTO pin_queue "
                "(post_id, image_path, title, description, link, "
                "anime_name, image_url, board_id, priority, scheduled_date, sha256, phash) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [(q.get("post_id",""), q.get("image_path",""),
                  q.get("title",""), q.get("description",""),
                  q.get("link",""), q.get("anime_name",""),
                  q.get("image_url",""), q.get("board_id",""),
                  q.get("priority",0), q.get("scheduled_date",""),
                  q.get("sha256",""), q.get("phash",""))
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

            # Restore Are.na posts
            arena_posts = state.get("arena_posts", [])
            if arena_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO arena_posts "
                    "(filename, block_id, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(a.get("filename",""), a.get("block_id", 0),
                      a.get("title",""), a.get("image_url",""),
                      a.get("posted_at") or "2000-01-01 00:00:00")
                     for a in arena_posts if a.get("filename")]
                )


            # Restore Bluesky posts
            bluesky_posts = state.get("bluesky_posts", [])
            if bluesky_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO bluesky_posts "
                    "(filename, post_uri, post_cid, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(b.get("filename",""), b.get("post_uri",""),
                      b.get("post_cid",""), b.get("image_url",""),
                      b.get("posted_at") or "2000-01-01 00:00:00")
                     for b in bluesky_posts if b.get("filename")]
                )

            # Restore Raindrop posts
            raindrop_posts = state.get("raindrop_posts", [])
            if raindrop_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO raindrop_posts "
                    "(filename, drop_id, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(r.get("filename",""), r.get("drop_id", 0),
                      r.get("title",""), r.get("image_url",""),
                      r.get("posted_at") or "2000-01-01 00:00:00")
                     for r in raindrop_posts if r.get("filename")]
                )

            # Restore Mastodon posts
            mastodon_posts = state.get("mastodon_posts", [])
            if mastodon_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO mastodon_posts "
                    "(filename, post_url, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(m.get("filename",""), m.get("post_url",""),
                      m.get("title",""), m.get("image_url",""),
                      m.get("posted_at") or "2000-01-01 00:00:00")
                     for m in mastodon_posts if m.get("filename")]
                )

            # Restore Pixelfed posts
            pixelfed_posts = state.get("pixelfed_posts", [])
            if pixelfed_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO pixelfed_posts "
                    "(filename, post_url, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(p.get("filename",""), p.get("post_url",""),
                      p.get("title",""), p.get("image_url",""),
                      p.get("posted_at") or "2000-01-01 00:00:00")
                     for p in pixelfed_posts if p.get("filename")]
                )

            # Restore Freeimage posts
            freeimage_posts = state.get("freeimage_posts", [])
            if freeimage_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO freeimage_posts "
                    "(filename, post_url, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(f.get("filename",""), f.get("post_url",""),
                      f.get("title",""), f.get("image_url",""),
                      f.get("posted_at") or "2000-01-01 00:00:00")
                     for f in freeimage_posts if f.get("filename")]
                )

            # Restore Imghippo posts
            imghippo_posts = state.get("imghippo_posts", [])
            if imghippo_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO imghippo_posts "
                    "(filename, post_url, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(h.get("filename",""), h.get("post_url",""),
                      h.get("title",""), h.get("image_url",""),
                      h.get("posted_at") or "2000-01-01 00:00:00")
                     for h in imghippo_posts if h.get("filename")]
                )

            # Restore Wallhaven posts
            wallhaven_posts = state.get("wallhaven_posts", [])
            if wallhaven_posts:
                conn.executemany(
                    "INSERT OR IGNORE INTO wallhaven_posts "
                    "(filename, post_url, title, image_url, posted_at) VALUES (?, ?, ?, ?, ?)",
                    [(w.get("filename",""), w.get("post_url",""),
                      w.get("title",""), w.get("image_url",""),
                      w.get("posted_at") or "2000-01-01 00:00:00")
                     for w in wallhaven_posts if w.get("filename")]
                )
            conn.commit()

        # \u2500\u2500 Auto-purge stale Telegram CDN entries restored from cloud snapshot \u2500\u2500\u2500
        # The cloud snapshot may be up to 30 min old. If /fixqueue cleaned the DB
        # between the last cloud sync and this restart, the restore above would
        # bring those deleted CDN entries back ("zombie" entries). We immediately
        # scan and delete them so they never reach the scheduler.
        purged_count = 0
        purged_titles = []
        try:
            with sqlite3.connect(DB_PATH) as conn2:
                stale = conn2.execute(
                    "SELECT id, title FROM pin_queue "
                    "WHERE image_url LIKE '%telesco.pe%' OR image_url LIKE '%/t.me/%'"
                ).fetchall()
                for row_id, title in stale:
                    conn2.execute("DELETE FROM pin_queue WHERE id=?", (row_id,))
                    purged_count += 1
                    purged_titles.append(title or "untitled")
                conn2.commit()
        except Exception as purge_err:
            logger.warning(f"[JSONBin] Auto-purge scan error (non-critical): {purge_err}")

        if purged_count > 0:
            logger.warning(
                f"[JSONBin] AUTO-PURGED {purged_count} stale Telegram CDN entry(ies) "
                f"that were restored from cloud snapshot: "
                + ", ".join(f"'{t[:30]}'" for t in purged_titles)
            )
            # Immediately push the cleaned state back to cloud so next restart is clean
            try:
                from jsonbin_sync import save_cloud_state
                save_cloud_state()
                logger.info("[JSONBin] Auto-pushed cleaned state back to cloud after CDN purge.")
            except Exception as resync_err:
                logger.warning(f"[JSONBin] Re-sync after auto-purge failed: {resync_err}")
        # \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

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
