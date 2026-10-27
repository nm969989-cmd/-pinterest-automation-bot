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
_last_sync_ts: float = 0.0
_sync_lock = threading.Lock()


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

def build_state_payload() -> tuple[dict, int]:
    """
    Constructs the budgeted state payload dictionary and calculates its byte size.
    Enforces <90KB target (well below JSONBin's 100KB limit).
    Returns: (payload_dict, payload_bytes)
    """
    import json
    from database import DB_PATH, _get_conn

    with _get_conn() as conn:
        # Processed posts (last 200 post_ids to keep payload well within 100KB budget)
        posts = [
            row[0] for row in conn.execute(
                "SELECT post_id FROM processed_posts ORDER BY processed_at DESC LIMIT 200"
            ).fetchall()
        ]
        # Uploaded files (last 150 with fingerprints for persistent deduplication and organic trust status)
        uploads = [
            {"filename": r[0], "title": r[1], "anime_name": r[2],
             "image_url": r[3], "uploaded_at": r[4],
             "sha256": r[5] or "", "phash": r[6] or "",
             "is_organic": r[7] or 0}
            for r in conn.execute(
                "SELECT filename, title, anime_name, image_url, uploaded_at, sha256, phash, is_organic "
                "FROM uploaded_files ORDER BY uploaded_at DESC LIMIT 150"
            ).fetchall()
        ]
        # Pin queue (preserve ALL pending queue items - vital state!)
        queue = [
            {"post_id": r[0], "image_path": r[1], "title": r[2],
             "description": r[3], "link": r[4], "anime_name": r[5],
             "image_url": r[6], "board_id": r[7], "priority": r[8],
             "scheduled_date": r[9],
             "sha256": r[10] or "", "phash": r[11] or "",
             "is_organic": r[12] or 0}
            for r in conn.execute(
                "SELECT post_id, image_path, title, description, link, "
                "anime_name, image_url, board_id, priority, scheduled_date, sha256, phash, is_organic FROM pin_queue"
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
            {
                "code": r[0],
                "target_url": r[1],
                "anime_name": r[2],
                "title": r[3],
                "created_at": r[4],
                "is_healed": r[5] or 0,
                "heal_count": r[6] or 0,
                "image_url": r[7] or "",
            }
            for r in conn.execute(
                "SELECT code, target_url, anime_name, title, created_at, is_healed, heal_count, image_url "
                "FROM tracked_links ORDER BY created_at DESC LIMIT 60"
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
        # Analytics, Revenue, A/B Testing, Evolved Keywords & Logs (compact slices for Render Free Tier persistence)
        link_clicks = [
            {"code": r[0], "anime_name": r[1], "title": r[2], "merchant": r[3] or "amazon", "clicked_at": r[4]}
            for r in conn.execute(
                "SELECT code, anime_name, title, merchant, clicked_at FROM link_clicks ORDER BY id DESC LIMIT 40"
            ).fetchall()
        ]
        bridge_visits = [
            {"code": r[0], "anime_name": r[1], "title": r[2], "is_pinterest": r[3] or 0, "visited_at": r[4]}
            for r in conn.execute(
                "SELECT code, anime_name, title, is_pinterest, visited_at FROM bridge_visits ORDER BY id DESC LIMIT 40"
            ).fetchall()
        ]
        ab_tests = [
            {
                "test_id": r[0], "variant": r[1], "post_id": r[2], "anime_name": r[3],
                "title": r[4], "image_url": r[5], "bridge_code": r[6], "visits": r[7] or 0,
                "clicks": r[8] or 0, "status": r[9] or "running", "posted_at": r[10],
                "decided_at": r[11], "created_at": r[12],
            }
            for r in conn.execute(
                "SELECT test_id, variant, post_id, anime_name, title, image_url, bridge_code, "
                "visits, clicks, status, posted_at, decided_at, created_at FROM ab_tests ORDER BY id DESC LIMIT 24"
            ).fetchall()
        ]
        revenue_log = [
            {"date_ist": r[0], "anime_name": r[1], "clicks": r[2] or 0, "est_revenue": r[3] or 0.0, "created_at": r[4]}
            for r in conn.execute(
                "SELECT date_ist, anime_name, clicks, est_revenue, created_at FROM revenue_log ORDER BY id DESC LIMIT 30"
            ).fetchall()
        ]
        evolved_keywords = [
            {
                "keyword": r[0], "wins": r[1] or 0, "losses": r[2] or 0,
                "total_clicks": r[3] or 0, "total_visits": r[4] or 0,
                "score": r[5] if r[5] is not None else 1.0, "last_updated": r[6],
            }
            for r in conn.execute(
                "SELECT keyword, wins, losses, total_clicks, total_visits, score, last_updated "
                "FROM evolved_keywords ORDER BY score DESC LIMIT 40"
            ).fetchall()
        ]
        stock_uploads = [
            {"platform": r[0], "filename": r[1], "title": r[2], "status": r[3] or "success", "uploaded_at": r[4]}
            for r in conn.execute(
                "SELECT platform, filename, title, status, uploaded_at FROM stock_uploads ORDER BY id DESC LIMIT 25"
            ).fetchall()
        ]
        dead_letter_queue = [
            {
                "post_id": r[0], "image_path": r[1], "title": r[2], "description": r[3],
                "link": r[4], "anime_name": r[5], "image_url": r[6], "fail_reason": r[7],
                "retry_count": r[8] or 0, "failed_at": r[9],
            }
            for r in conn.execute(
                "SELECT post_id, image_path, title, description, link, anime_name, image_url, "
                "fail_reason, retry_count, failed_at FROM dead_letter_queue ORDER BY id DESC LIMIT 10"
            ).fetchall()
        ]
        ghost_revive_log = [
            {
                "original_post_id": r[0], "new_post_id": r[1], "anime_name": r[2],
                "original_title": r[3], "new_title": r[4], "image_url": r[5], "revived_at": r[6],
            }
            for r in conn.execute(
                "SELECT original_post_id, new_post_id, anime_name, original_title, new_title, "
                "image_url, revived_at FROM ghost_revive_log ORDER BY id DESC LIMIT 15"
            ).fetchall()
        ]
        backlink_ledger = [
            {
                "filename": r[0], "anime_name": r[1], "platform": r[2],
                "target_url": r[3], "anchor_type": r[4] or "pinterest", "created_at": r[5],
            }
            for r in conn.execute(
                "SELECT filename, anime_name, platform, target_url, anchor_type, created_at "
                "FROM backlink_ledger ORDER BY id DESC LIMIT 30"
            ).fetchall()
        ]
        viral_surge_alerts = [
            {
                "code": r[0], "anime_name": r[1], "visits_count": r[2] or 0,
                "clicks_count": r[3] or 0, "surge_score": r[4] or 0.0, "alerted_at": r[5],
            }
            for r in conn.execute(
                "SELECT code, anime_name, visits_count, clicks_count, surge_score, alerted_at "
                "FROM viral_surge_alerts ORDER BY id DESC LIMIT 15"
            ).fetchall()
        ]

    payload = {
        "processed_posts":    posts,
        "uploaded_files":     uploads,
        "pin_queue":          queue,
        "bot_metadata":       metadata,
        "tracked_links":      tracked,
        "arena_posts":        arena,
        "bluesky_posts":      bluesky,
        "raindrop_posts":     raindrop,
        "mastodon_posts":     mastodon,
        "pixelfed_posts":     pixelfed,
        "freeimage_posts":    freeimage,
        "imghippo_posts":     imghippo,
        "wallhaven_posts":    wallhaven,
        "link_clicks":        link_clicks,
        "bridge_visits":      bridge_visits,
        "ab_tests":           ab_tests,
        "revenue_log":        revenue_log,
        "evolved_keywords":   evolved_keywords,
        "stock_uploads":      stock_uploads,
        "dead_letter_queue":  dead_letter_queue,
        "ghost_revive_log":   ghost_revive_log,
        "backlink_ledger":    backlink_ledger,
        "viral_surge_alerts": viral_surge_alerts,
    }

    # ── Budget Enforcer: Guarantee strictly under 90KB (JSONBin free limit = 100KB) ──
    raw_json = json.dumps(payload, separators=(',', ':'))
    payload_bytes = len(raw_json.encode('utf-8'))

    if payload_bytes > 90 * 1024:
        logger.info(f"[JSONBin] Payload size ({payload_bytes / 1024:.1f}KB) exceeds 90KB target. Trimming non-essential logs...")
        # Tier 1 pruning: reduce secondary logs to 5 items each
        for k in [
            "arena_posts", "bluesky_posts", "raindrop_posts", "mastodon_posts",
            "pixelfed_posts", "freeimage_posts", "imghippo_posts", "wallhaven_posts",
            "link_clicks", "bridge_visits", "stock_uploads", "dead_letter_queue",
            "ghost_revive_log", "backlink_ledger", "viral_surge_alerts",
        ]:
            payload[k] = payload[k][:5]
        payload["uploaded_files"] = payload["uploaded_files"][:30]
        payload["tracked_links"]  = payload["tracked_links"][:30]
        raw_json = json.dumps(payload, separators=(',', ':'))
        payload_bytes = len(raw_json.encode('utf-8'))

        # Tier 2 pruning if still over 90KB
        if payload_bytes > 90 * 1024:
            payload["processed_posts"] = payload["processed_posts"][:100]
            payload["evolved_keywords"] = payload["evolved_keywords"][:15]
            payload["ab_tests"] = payload["ab_tests"][:10]
            payload["revenue_log"] = payload["revenue_log"][:10]
            raw_json = json.dumps(payload, separators=(',', ':'))
            payload_bytes = len(raw_json.encode('utf-8'))

    return payload, payload_bytes


def save_cloud_state() -> bool:
    """
    Save current SQLite state to JSONBin cloud.
    Called periodically and on shutdown.
    """
    global _last_sync_ts
    if not _is_configured():
        return False
    with _sync_lock:
        try:
            import json
            payload, payload_bytes = build_state_payload()
            raw_json = json.dumps(payload, separators=(',', ':'))

            logger.info(f"[JSONBin] Uploading budgeted payload ({payload_bytes / 1024:.1f}KB / 100KB free limit)")

            r = requests.put(
                f"{_BASE_URL}/{JSONBIN_BIN_ID}",
                data=raw_json,
                headers=_headers(),
                timeout=15
            )
            if r.status_code == 200:
                _last_sync_ts = time.time()
                logger.info(
                    f"[JSONBin] Synced successfully: {len(payload['processed_posts'])} posts, {len(payload['uploaded_files'])} uploads, "
                    f"{len(payload.get('pin_queue', []))} queued, {len(payload['tracked_links'])} tracked links ({payload_bytes / 1024:.1f}KB)"
                )
                return True
            else:
                logger.warning(f"[JSONBin] Save failed: {r.status_code} {r.text[:120]}")
        except Exception as e:
            logger.error(f"[JSONBin] Save error: {e}")
    return False


def trigger_cloud_sync_now(min_interval_sec: int = 60) -> bool:
    """
    Triggers an immediate cloud sync with rate-limit debouncing (default 60s cooldown)
    so frequent link heals or state updates never hit JSONBin's free tier rate limits.
    """
    if not _is_configured():
        return False
    if time.time() - _last_sync_ts < min_interval_sec:
        logger.debug("[JSONBin] Skipping immediate sync (debounced within cooldown window).")
        return True
    return save_cloud_state()


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
        from database import DB_PATH, init_db, _get_conn
        init_db()  # ensure tables exist

        with _get_conn() as conn:
            # Restore processed posts
            posts = state.get("processed_posts", [])
            conn.executemany(
                "INSERT OR IGNORE INTO processed_posts (post_id) VALUES (?)",
                [(p,) for p in posts]
            )
            # Restore uploaded files — preserve original uploaded_at timestamp, fingerprints, and is_organic flag
            uploads = state.get("uploaded_files", [])
            conn.executemany(
                "INSERT OR IGNORE INTO uploaded_files "
                "(filename, title, anime_name, image_url, uploaded_at, sha256, phash, is_organic) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(u.get("filename",""), u.get("title",""),
                  u.get("anime_name",""), u.get("image_url",""),
                  u.get("uploaded_at") or "2000-01-01 00:00:00",
                  u.get("sha256", ""), u.get("phash", ""),
                  u.get("is_organic", 0))
                 for u in uploads]
            )
            # Restore pin queue (including board_id, fingerprints, and is_organic flag)
            queue = state.get("pin_queue", [])
            conn.executemany(
                "INSERT OR IGNORE INTO pin_queue "
                "(post_id, image_path, title, description, link, "
                "anime_name, image_url, board_id, priority, scheduled_date, sha256, phash, is_organic) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(q.get("post_id",""), q.get("image_path",""),
                  q.get("title",""), q.get("description",""),
                  q.get("link",""), q.get("anime_name",""),
                  q.get("image_url",""), q.get("board_id",""),
                  q.get("priority",0), q.get("scheduled_date",""),
                  q.get("sha256",""), q.get("phash",""),
                  q.get("is_organic", 0))
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
                    "INSERT INTO tracked_links "
                    "(code, target_url, anime_name, title, created_at, is_healed, heal_count, image_url) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(code) DO UPDATE SET "
                    "target_url = excluded.target_url, "
                    "is_healed = excluded.is_healed, "
                    "heal_count = excluded.heal_count, "
                    "image_url = COALESCE(NULLIF(excluded.image_url, ''), tracked_links.image_url)",
                    [(l.get("code",""), l.get("target_url",""),
                      l.get("anime_name",""), l.get("title",""),
                      l.get("created_at") or "2000-01-01 00:00:00",
                      l.get("is_healed", 0),
                      l.get("heal_count", 0),
                      l.get("image_url", ""))
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

            # Restore A/B tests (UNIQUE on test_id, variant)
            ab_tests = state.get("ab_tests", [])
            if ab_tests:
                conn.executemany(
                    "INSERT OR REPLACE INTO ab_tests "
                    "(test_id, variant, post_id, anime_name, title, image_url, bridge_code, "
                    "visits, clicks, status, posted_at, decided_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(a.get("test_id",""), a.get("variant","A"), a.get("post_id",""),
                      a.get("anime_name",""), a.get("title",""), a.get("image_url",""),
                      a.get("bridge_code",""), a.get("visits",0), a.get("clicks",0),
                      a.get("status","running"), a.get("posted_at"), a.get("decided_at"),
                      a.get("created_at") or "2000-01-01 00:00:00")
                     for a in ab_tests if a.get("test_id")]
                )

            # Restore Revenue Log (UNIQUE on date_ist, anime_name)
            revenue_log = state.get("revenue_log", [])
            if revenue_log:
                conn.executemany(
                    "INSERT OR REPLACE INTO revenue_log "
                    "(date_ist, anime_name, clicks, est_revenue, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(r.get("date_ist",""), r.get("anime_name",""),
                      r.get("clicks",0), r.get("est_revenue",0.0),
                      r.get("created_at") or "2000-01-01 00:00:00")
                     for r in revenue_log if r.get("date_ist")]
                )

            # Restore Evolved Keywords (PRIMARY KEY on keyword)
            evolved_keywords = state.get("evolved_keywords", [])
            if evolved_keywords:
                conn.executemany(
                    "INSERT OR REPLACE INTO evolved_keywords "
                    "(keyword, wins, losses, total_clicks, total_visits, score, last_updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(k.get("keyword",""), k.get("wins",0), k.get("losses",0),
                      k.get("total_clicks",0), k.get("total_visits",0),
                      k.get("score",1.0), k.get("last_updated") or "2000-01-01 00:00:00")
                     for k in evolved_keywords if k.get("keyword")]
                )

            # Restore append-only log tables when local DB is fresh (e.g., after Render restart)
            if state.get("link_clicks") and conn.execute("SELECT COUNT(*) FROM link_clicks").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO link_clicks (code, anime_name, title, merchant, clicked_at) VALUES (?, ?, ?, ?, ?)",
                    [(c.get("code",""), c.get("anime_name",""), c.get("title",""),
                      c.get("merchant","amazon"), c.get("clicked_at") or "2000-01-01 00:00:00")
                     for c in state["link_clicks"] if c.get("code")]
                )

            if state.get("bridge_visits") and conn.execute("SELECT COUNT(*) FROM bridge_visits").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO bridge_visits (code, anime_name, title, is_pinterest, visited_at) VALUES (?, ?, ?, ?, ?)",
                    [(v.get("code",""), v.get("anime_name",""), v.get("title",""),
                      v.get("is_pinterest",0), v.get("visited_at") or "2000-01-01 00:00:00")
                     for v in state["bridge_visits"] if v.get("code")]
                )

            if state.get("stock_uploads") and conn.execute("SELECT COUNT(*) FROM stock_uploads").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO stock_uploads (platform, filename, title, status, uploaded_at) VALUES (?, ?, ?, ?, ?)",
                    [(s.get("platform",""), s.get("filename",""), s.get("title",""),
                      s.get("status","success"), s.get("uploaded_at") or "2000-01-01 00:00:00")
                     for s in state["stock_uploads"] if s.get("platform")]
                )

            if state.get("dead_letter_queue") and conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO dead_letter_queue "
                    "(post_id, image_path, title, description, link, anime_name, image_url, fail_reason, retry_count, failed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(d.get("post_id",""), d.get("image_path",""), d.get("title",""),
                      d.get("description",""), d.get("link",""), d.get("anime_name",""),
                      d.get("image_url",""), d.get("fail_reason",""),
                      d.get("retry_count",0), d.get("failed_at") or "2000-01-01 00:00:00")
                     for d in state["dead_letter_queue"]]
                )

            if state.get("ghost_revive_log") and conn.execute("SELECT COUNT(*) FROM ghost_revive_log").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO ghost_revive_log "
                    "(original_post_id, new_post_id, anime_name, original_title, new_title, image_url, revived_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [(g.get("original_post_id",""), g.get("new_post_id",""), g.get("anime_name",""),
                      g.get("original_title",""), g.get("new_title",""), g.get("image_url",""),
                      g.get("revived_at") or "2000-01-01 00:00:00")
                     for g in state["ghost_revive_log"]]
                )

            if state.get("backlink_ledger") and conn.execute("SELECT COUNT(*) FROM backlink_ledger").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO backlink_ledger "
                    "(filename, anime_name, platform, target_url, anchor_type, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(b.get("filename",""), b.get("anime_name",""), b.get("platform",""),
                      b.get("target_url",""), b.get("anchor_type","pinterest"),
                      b.get("created_at") or "2000-01-01 00:00:00")
                     for b in state["backlink_ledger"] if b.get("platform")]
                )

            if state.get("viral_surge_alerts") and conn.execute("SELECT COUNT(*) FROM viral_surge_alerts").fetchone()[0] == 0:
                conn.executemany(
                    "INSERT INTO viral_surge_alerts "
                    "(code, anime_name, visits_count, clicks_count, surge_score, alerted_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(v.get("code",""), v.get("anime_name",""), v.get("visits_count",0),
                      v.get("clicks_count",0), v.get("surge_score",0.0),
                      v.get("alerted_at") or "2000-01-01 00:00:00")
                     for v in state["viral_surge_alerts"] if v.get("code")]
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
            with _get_conn() as conn2:
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
