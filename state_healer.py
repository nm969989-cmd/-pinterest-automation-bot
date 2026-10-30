"""
state_healer.py — One-shot self-healing of dedup history + queue (runs inside the live bot)
===========================================================================================
Runs ON the Render instance so the live SQLite DB itself is corrected and then
pushed to JSONBin (an external cleanup can never stick because the live instance
re-pushes its own DB on every sync/shutdown).

Steps:
  1. Pull every Raindrop bookmark (== every pin ever posted) and download its cover.
  2. Store real sha256 + dHash for each into uploaded_files (visual-duplicate memory).
  3. Fingerprint queued images and purge anything already posted / duplicated in queue.
  4. Mark Telegram post IDs <= newest-seen as processed so backlog never re-scrapes them.
  5. Sync to cloud.
"""

import os
import re
import tempfile
import requests
from logger import get_logger

logger = get_logger(__name__)

_CHANNEL = "Anime_Naruto_Art_AOT"


def _fetch_raindrop_items() -> list:
    from raindrop_uploader import _get_headers
    items = []
    for page in range(0, 20):
        res = requests.get(
            f"https://api.raindrop.io/rest/v1/raindrops/0?perpage=50&page={page}",
            headers=_get_headers(), timeout=20,
        ).json()
        batch = res.get("items", [])
        if not batch:
            break
        items.extend(batch)
    return items


def _fingerprint_url(url: str):
    """Download an image URL to a temp file and return (sha256, dhash) or ("", "")."""
    from image_dedup import get_image_fingerprints
    tmp = None
    try:
        r = requests.get(url, timeout=25)
        if r.status_code != 200 or not r.content:
            return "", ""
        fd, tmp = tempfile.mkstemp(suffix=".img")
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        return get_image_fingerprints(tmp)
    except Exception as e:
        logger.debug(f"[Healer] fingerprint failed for {url[:60]}: {e}")
        return "", ""
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def heal_state(progress=None) -> dict:
    """Run the full heal. `progress` is an optional callable(str)."""
    from database import (_get_conn, purge_already_posted_queue_items,
                          clean_duplicate_queue_items, set_metadata)

    def say(msg):
        logger.info(f"[Healer] {msg}")
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    stats = {"history_added": 0, "queue_fingerprinted": 0, "queue_purged": 0, "marked_processed": 0}

    # 1+2. Raindrop history -> fingerprinted uploaded_files
    items = _fetch_raindrop_items()
    say(f"Fetched {len(items)} Raindrop bookmarks. Fingerprinting covers...")
    for it in items:
        cover = (it.get("cover") or "").strip()
        if not cover:
            continue
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT phash FROM uploaded_files WHERE image_url = ? LIMIT 1", (cover,)
            ).fetchone()
        if row and row[0]:
            continue
        sha, ph = _fingerprint_url(cover)
        title = (it.get("title") or "")[:80]
        created = (it.get("created") or "2026-10-01T00:00:00Z")[:19].replace("T", " ")
        fn = f"raindrop_hist_{cover.split('/')[-1]}"
        with _get_conn() as conn:
            if row:
                conn.execute(
                    "UPDATE uploaded_files SET sha256 = ?, phash = ? WHERE image_url = ?",
                    (sha, ph, cover),
                )
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO uploaded_files "
                    "(filename, title, anime_name, image_url, uploaded_at, sha256, phash, is_organic) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                    (fn, title, "Anime", cover, created, sha, ph),
                )
            conn.commit()
        stats["history_added"] += 1

    # 3. Fingerprint queued items from their stored URL, then purge dupes
    with _get_conn() as conn:
        q = conn.execute(
            "SELECT id, image_url, sha256, phash FROM pin_queue"
        ).fetchall()
    for qid, url, sha, ph in q:
        if (not ph or not sha) and url and url.startswith("http"):
            nsha, nph = _fingerprint_url(url)
            if nph or nsha:
                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE pin_queue SET sha256 = COALESCE(NULLIF(sha256,''), ?), "
                        "phash = COALESCE(NULLIF(phash,''), ?) WHERE id = ?",
                        (nsha, nph, qid),
                    )
                    conn.commit()
                stats["queue_fingerprinted"] += 1
    purged = purge_already_posted_queue_items() + clean_duplicate_queue_items()
    stats["queue_purged"] = purged
    say(f"Queue cleaned: {purged} duplicate/already-posted item(s) removed.")

    # 4. Mark known Telegram IDs as processed
    try:
        r = requests.get(f"https://t.me/s/{_CHANNEL}", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        ids = [int(x) for x in re.findall(rf"data-post=.{_CHANNEL}/([0-9]+)", r.text)]
        newest = max(ids) if ids else 0
    except Exception:
        newest = 0
    if newest:
        # Everything already in the queue or posted is processed; mark ids below the
        # queue's lowest unposted id range conservatively: only ids that are NOT queued.
        with _get_conn() as conn:
            queued_ids = {
                (p or "").rsplit("_", 1)[-1] for (p,) in conn.execute("SELECT post_id FROM pin_queue")
            }
            for n in range(3050, newest + 1):
                if str(n) in queued_ids:
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO processed_posts (post_id) VALUES (?)",
                    (f"{_CHANNEL}/{n}",),
                )
                stats["marked_processed"] += 1
            conn.commit()

    # 5. Sync
    try:
        from jsonbin_sync import save_cloud_state
        stats["cloud_synced"] = bool(save_cloud_state())
    except Exception as e:
        logger.warning(f"[Healer] cloud sync failed: {e}")
        stats["cloud_synced"] = False
    return stats
