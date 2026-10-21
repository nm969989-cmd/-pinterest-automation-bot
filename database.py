import sqlite3
import os
import datetime
from logger import get_logger

logger = get_logger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_state.db")


def _get_conn():
    """Returns a SQLite connection with WAL mode for concurrency."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
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
                image_url  TEXT,
                sha256     TEXT,
                phash      TEXT,
                is_organic INTEGER DEFAULT 0
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
                sha256      TEXT,
                phash       TEXT,
                is_organic  INTEGER DEFAULT 0,   -- 1=organic trust pin (no affiliate link), 0=monetized
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
                    "retry_count INTEGER DEFAULT 0", "sha256 TEXT", "phash TEXT",
                    "is_organic INTEGER DEFAULT 0"]:
            try:
                conn.execute(f"ALTER TABLE uploaded_files ADD COLUMN {col}")
            except Exception:
                pass
        # Add board_id, retry_count, sha256, phash, is_organic to pin_queue if upgrading from older schema
        for col in ["board_id TEXT DEFAULT ''", "retry_count INTEGER DEFAULT 0",
                    "sha256 TEXT", "phash TEXT", "is_organic INTEGER DEFAULT 0"]:
            try:
                conn.execute(f"ALTER TABLE pin_queue ADD COLUMN {col}")
            except Exception:
                pass

        # ── Dead-Letter Queue (pins that exhausted all retries) ─────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dead_letter_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id     TEXT,
                image_path  TEXT,
                title       TEXT,
                description TEXT,
                link        TEXT,
                anime_name  TEXT,
                image_url   TEXT,
                fail_reason TEXT,          -- last error / reason for dropping
                retry_count INTEGER DEFAULT 0,
                failed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Affiliate Link Tracking Tables ──────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_links (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT UNIQUE,
                target_url      TEXT,
                anime_name      TEXT,
                title           TEXT,
                image_url       TEXT DEFAULT '',
                last_checked_at TIMESTAMP,
                is_healed       INTEGER DEFAULT 0,
                heal_count      INTEGER DEFAULT 0,
                created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                merchant    TEXT DEFAULT 'amazon',
                clicked_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Add columns to tracked_links if upgrading from older schema
        for col in ["image_url TEXT DEFAULT ''", "last_checked_at TIMESTAMP",
                    "is_healed INTEGER DEFAULT 0", "heal_count INTEGER DEFAULT 0"]:
            try:
                conn.execute(f"ALTER TABLE tracked_links ADD COLUMN {col}")
            except Exception:
                pass

        # Add merchant to link_clicks if upgrading from older schema
        try:
            conn.execute("ALTER TABLE link_clicks ADD COLUMN merchant TEXT DEFAULT 'amazon'")
        except Exception:
            pass

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

        # ── Backlink SEO Waterfall Ledger ────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backlink_ledger (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT,
                anime_name  TEXT,
                platform    TEXT,
                target_url  TEXT,
                anchor_type TEXT DEFAULT 'pinterest',
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # ── Viral Pin Surge Radar Alerts ─────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS viral_surge_alerts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                code         TEXT NOT NULL,
                anime_name   TEXT,
                visits_count INTEGER DEFAULT 0,
                clicks_count INTEGER DEFAULT 0,
                surge_score  REAL DEFAULT 0.0,
                alerted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_surge_code_time
            ON viral_surge_alerts (code, alerted_at)
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
            CREATE INDEX IF NOT EXISTS idx_uploads_sha256
            ON uploaded_files (sha256)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_uploads_phash
            ON uploaded_files (phash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_sha256
            ON pin_queue (sha256)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_queue_phash
            ON pin_queue (phash)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracked_code
            ON tracked_links (code)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracked_healed
            ON tracked_links (is_healed)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tracked_checked
            ON tracked_links (last_checked_at)
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

        # ── Mastodon Cross-Post Tracking ──────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mastodon_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_url    TEXT,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mastodon_filename
            ON mastodon_posts (filename)
        """)

        # ── Pixelfed Cross-Post Tracking ─────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pixelfed_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_url    TEXT,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pixelfed_filename
            ON pixelfed_posts (filename)
        """)

        # ── Freeimage.host Cross-Post Tracking ──────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS freeimage_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_url    TEXT,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_freeimage_filename
            ON freeimage_posts (filename)
        """)

        # ── Imghippo Cross-Post Tracking ─────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS imghippo_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_url    TEXT,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_imghippo_filename
            ON imghippo_posts (filename)
        """)

        # ── Wallhaven.cc Cross-Post Tracking ─────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallhaven_posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT UNIQUE,
                post_url    TEXT,
                title       TEXT,
                image_url   TEXT,
                posted_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_wallhaven_filename
            ON wallhaven_posts (filename)
        """)

        # ── Bridge Visits Tracking (Analytics & Viral Radar) ─────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bridge_visits (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL,
                anime_name  TEXT,
                title       TEXT,
                user_agent  TEXT,
                referrer    TEXT,
                is_pinterest INTEGER DEFAULT 0,
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

        # ── A/B Title Test Tracking ──────────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ab_tests (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                test_id      TEXT,
                variant      TEXT DEFAULT 'A',
                post_id      TEXT,
                anime_name   TEXT,
                title        TEXT,
                image_url    TEXT,
                bridge_code  TEXT,
                visits       INTEGER DEFAULT 0,
                clicks       INTEGER DEFAULT 0,
                status       TEXT DEFAULT 'running',
                posted_at    TIMESTAMP,
                decided_at   TIMESTAMP,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(test_id, variant)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ab_test_id ON ab_tests (test_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ab_code ON ab_tests (bridge_code)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_ab_status ON ab_tests (status, created_at)
        """)

        # ── Revenue Tracking (estimated earnings) ────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS revenue_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                date_ist     TEXT,
                anime_name   TEXT,
                clicks       INTEGER DEFAULT 0,
                est_revenue  REAL DEFAULT 0.0,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(date_ist, anime_name)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_revenue_date ON revenue_log (date_ist)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_revenue_anime ON revenue_log (anime_name)
        """)

        # ── Ghost Pin Reviver Tracking ───────────────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ghost_revive_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                original_post_id  TEXT,
                new_post_id       TEXT,
                anime_name        TEXT,
                original_title    TEXT,
                new_title         TEXT,
                image_url         TEXT,
                revived_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_revive_orig ON ghost_revive_log (original_title, anime_name)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_revive_date ON ghost_revive_log (revived_at)
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


# ── Mastodon Cross-Post Tracking ─────────────────────────────────────────────

def is_mastodon_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Mastodon (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM mastodon_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_mastodon_posted(filename: str, post_url: str = "",
                         title: str = "", image_url: str = "") -> None:
    """Records a successful Mastodon post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO mastodon_posts (filename, post_url, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_url, title, image_url)
        )
        conn.commit()


def get_mastodon_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Mastodon posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM mastodon_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM mastodon_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, post_url, posted_at
            FROM mastodon_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "post_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }




# ── Pixelfed Cross-Post Tracking ─────────────────────────────────────────────

def is_pixelfed_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Pixelfed (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM pixelfed_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_pixelfed_posted(filename: str, post_url: str = "",
                         title: str = "", image_url: str = "") -> None:
    """Records a successful Pixelfed post. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO pixelfed_posts (filename, post_url, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_url, title, image_url)
        )
        conn.commit()


def get_pixelfed_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Pixelfed posting statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM pixelfed_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM pixelfed_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, post_url, posted_at
            FROM pixelfed_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "post_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }


def get_multi_platform_stats(today_str: str = None) -> dict:
    """Returns an aggregated snapshot of all platforms (Pinterest, Are.na, Bluesky, Raindrop, Mastodon, Pixelfed, Freeimage, Imghippo)."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    
    pins_today = get_today_uploads(today_str)
    all_time_pins = get_all_time_stats()
    arena_stats = get_arena_stats(today_str)
    bluesky_stats = get_bluesky_stats(today_str)
    raindrop_stats = get_raindrop_stats(today_str)
    mastodon_stats = get_mastodon_stats(today_str)
    pixelfed_stats = get_pixelfed_stats(today_str)
    freeimage_stats = get_freeimage_stats(today_str)
    imghippo_stats = get_imghippo_stats(today_str)
    wallhaven_stats = get_wallhaven_stats(today_str)

    return {
        "date": today_str,
        "pinterest": {
            "today": len(pins_today),
            "total": all_time_pins.get("total", 0),
            "pins": pins_today,
        },
        "arena": arena_stats,
        "bluesky": bluesky_stats,
        "raindrop": raindrop_stats,
        "mastodon": mastodon_stats,
        "pixelfed": pixelfed_stats,
        "freeimage": freeimage_stats,
        "imghippo": imghippo_stats,
        "wallhaven": wallhaven_stats,
    }


# ── Freeimage.host Cross-Post Tracking ───────────────────────────────────────

def is_freeimage_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Freeimage.host (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM freeimage_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_freeimage_posted(filename: str, post_url: str = "",
                          title: str = "", image_url: str = "") -> None:
    """Records a successful Freeimage.host upload. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO freeimage_posts (filename, post_url, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_url, title, image_url)
        )
        conn.commit()


def get_freeimage_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Freeimage.host upload statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM freeimage_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM freeimage_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, post_url, posted_at
            FROM freeimage_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "post_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }




# ── Imghippo Cross-Post Tracking ─────────────────────────────────────────────

def is_imghippo_posted(filename: str) -> bool:
    """Returns True if this image was already posted to Imghippo (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM imghippo_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_imghippo_posted(filename: str, post_url: str = "",
                         title: str = "", image_url: str = "") -> None:
    """Records a successful Imghippo upload. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO imghippo_posts (filename, post_url, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_url, title, image_url)
        )
        conn.commit()


def get_imghippo_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Imghippo upload statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM imghippo_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM imghippo_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, post_url, posted_at
            FROM imghippo_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "post_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
    }


# ── Wallhaven.cc Cross-Post Tracking ─────────────────────────────────────────

def is_wallhaven_posted(filename: str) -> bool:
    """Returns True if this image was already posted or syndicated to Wallhaven (prevents duplicates)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM wallhaven_posts WHERE filename = ?", (filename,)
        ).fetchone()
        return row is not None


def mark_wallhaven_posted(filename: str, post_url: str = "",
                          title: str = "", image_url: str = "") -> None:
    """Records a successful Wallhaven syndication. Ignores duplicates (INSERT OR IGNORE)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO wallhaven_posts (filename, post_url, title, image_url)
            VALUES (?, ?, ?, ?)
            """,
            (filename, post_url, title, image_url)
        )
        conn.commit()


def get_wallhaven_stats(today_str: str = None) -> dict:
    """Returns today's and all-time Wallhaven upload statistics."""
    if not today_str:
        import datetime as _dt
        today_str = (_dt.datetime.utcnow() + _dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    with _get_conn() as conn:
        today_count = conn.execute("""
            SELECT COUNT(*) FROM wallhaven_posts
            WHERE date(posted_at, '+5 hours', '+30 minutes') = ?
        """, (today_str,)).fetchone()[0]
        total_count = conn.execute("SELECT COUNT(*) FROM wallhaven_posts").fetchone()[0]
        recent = conn.execute("""
            SELECT filename, title, post_url, posted_at
            FROM wallhaven_posts
            ORDER BY id DESC LIMIT 5
        """).fetchall()
    return {
        "today": today_count,
        "total": total_count,
        "recent": [
            {"filename": r[0], "title": r[1], "post_url": r[2], "posted_at": r[3]}
            for r in recent
        ]
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


def mark_file_uploaded(filename: str, title: str = "", anime_name: str = "",
                       image_url: str = "", sha256: str = "", phash: str = "",
                       is_organic: int = 0):
    """Records that a file has been successfully uploaded to Pinterest with fingerprints and organic flag."""
    if (not sha256 or not phash) and filename:
        try:
            from image_dedup import get_image_fingerprints
            for cand in [os.path.join("processed", filename), os.path.join("downloads", filename), filename]:
                if os.path.isfile(cand):
                    calc_sha, calc_phash = get_image_fingerprints(cand)
                    sha256 = sha256 or calc_sha
                    phash = phash or calc_phash
                    break
        except Exception:
            pass

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO uploaded_files (filename, title, anime_name, image_url, sha256, phash, is_organic)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (filename, title, anime_name, image_url, sha256, phash, is_organic),
        )
        conn.execute(
            """
            UPDATE uploaded_files
            SET sha256 = COALESCE(NULLIF(sha256, ''), ?),
                phash = COALESCE(NULLIF(phash, ''), ?),
                is_organic = CASE WHEN ? = 1 THEN 1 ELSE is_organic END
            WHERE filename = ?
            """,
            (sha256, phash, is_organic, filename)
        )
        conn.commit()


def is_image_duplicate(filepath: str = None, sha256: str = None, phash: str = None,
                       max_distance: int = 4, exclude_queue_id: int = None) -> tuple[bool, str]:
    """
    Checks if an image is already in uploaded_files or pin_queue.
    Checks:
    1. Exact cryptographic SHA-256 match.
    2. Visual difference hash (dHash) with Hamming distance <= max_distance.
    
    Returns (is_duplicate: bool, reason: str).
    """
    if (not sha256 or not phash) and filepath and os.path.isfile(filepath):
        try:
            from image_dedup import get_image_fingerprints
            calc_sha, calc_phash = get_image_fingerprints(filepath)
            sha256 = sha256 or calc_sha
            phash = phash or calc_phash
        except Exception as e:
            logger.warning(f"[Dedup] Error extracting fingerprints from {filepath}: {e}")

    if not sha256 and not phash:
        return False, ""

    try:
        from image_dedup import hamming_distance
    except ImportError:
        return False, ""

    with _get_conn() as conn:
        # 1. Exact SHA-256 in uploaded_files
        if sha256:
            row = conn.execute(
                "SELECT filename, title FROM uploaded_files WHERE sha256 = ? LIMIT 1",
                (sha256,)
            ).fetchone()
            if row:
                return True, f"Exact SHA-256 match with uploaded pin '{row[1]}' ({row[0]})"

            # Exact SHA-256 in pin_queue
            if exclude_queue_id:
                row = conn.execute(
                    "SELECT id, title FROM pin_queue WHERE sha256 = ? AND id != ? LIMIT 1",
                    (sha256, exclude_queue_id)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT id, title FROM pin_queue WHERE sha256 = ? LIMIT 1",
                    (sha256,)
                ).fetchone()
            if row:
                return True, f"Exact SHA-256 match with queued pin #{row[0]} '{row[1]}'"

        # 2. Perceptual dHash check against uploaded_files
        if phash and len(phash) == 16:
            uploads = conn.execute(
                "SELECT filename, title, phash FROM uploaded_files "
                "WHERE phash IS NOT NULL AND length(phash) = 16 "
                "ORDER BY uploaded_at DESC LIMIT 2000"
            ).fetchall()
            for u_fn, u_title, u_phash in uploads:
                dist = hamming_distance(phash, u_phash)
                if dist <= max_distance:
                    return True, f"Visual duplicate of uploaded pin '{u_title}' ({u_fn}, dHash dist {dist}/{max_distance})"

            # Perceptual dHash check against pin_queue
            if exclude_queue_id:
                queue_items = conn.execute(
                    "SELECT id, title, phash FROM pin_queue "
                    "WHERE phash IS NOT NULL AND length(phash) = 16 AND id != ?",
                    (exclude_queue_id,)
                ).fetchall()
            else:
                queue_items = conn.execute(
                    "SELECT id, title, phash FROM pin_queue "
                    "WHERE phash IS NOT NULL AND length(phash) = 16"
                ).fetchall()
            for q_id, q_title, q_phash in queue_items:
                dist = hamming_distance(phash, q_phash)
                if dist <= max_distance:
                    return True, f"Visual duplicate of queued pin #{q_id} '{q_title}' (dHash dist {dist}/{max_distance})"

    return False, ""


def clean_duplicate_queue_items(max_distance: int = 4) -> int:
    """
    Scans the entire pin_queue.
    Removes any queued item that:
    1. Has already been uploaded (exact SHA or pHash <= max_distance).
    2. Is a duplicate of an earlier queued item.
    Returns number of purged queue items.
    """
    try:
        from image_dedup import get_image_fingerprints, hamming_distance
    except ImportError:
        return 0

    purged_count = 0
    with _get_conn() as conn:
        items = conn.execute(
            "SELECT id, image_path, title, sha256, phash FROM pin_queue ORDER BY priority DESC, id ASC"
        ).fetchall()

    seen_shas = set()
    seen_phashes = []

    for item_id, img_path, title, sha256, phash in items:
        # Populate missing fingerprints if file exists
        if (not sha256 or not phash) and img_path and os.path.isfile(img_path):
            calc_sha, calc_phash = get_image_fingerprints(img_path)
            sha256 = sha256 or calc_sha
            phash = phash or calc_phash
            if sha256 or phash:
                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE pin_queue SET sha256 = ?, phash = ? WHERE id = ?",
                        (sha256, phash, item_id)
                    )
                    conn.commit()

        # Check against uploaded_files
        is_dup, reason = is_image_duplicate(
            filepath=img_path, sha256=sha256, phash=phash,
            max_distance=max_distance, exclude_queue_id=item_id
        )
        if is_dup:
            logger.info(f"[Dedup Scan] Dropping queued pin #{item_id} '{title}': {reason}")
            remove_queued_pin(item_id)
            purged_count += 1
            continue

        # Check against earlier items in this same scan
        if sha256 and sha256 in seen_shas:
            logger.info(f"[Dedup Scan] Dropping duplicate in queue #{item_id} '{title}' (duplicate SHA in queue)")
            remove_queued_pin(item_id)
            purged_count += 1
            continue

        is_intra_queue_dup = False
        if phash and len(phash) == 16:
            for s_phash, s_label in seen_phashes:
                if hamming_distance(phash, s_phash) <= max_distance:
                    logger.info(f"[Dedup Scan] Dropping duplicate in queue #{item_id} '{title}' (matches {s_label})")
                    remove_queued_pin(item_id)
                    purged_count += 1
                    is_intra_queue_dup = True
                    break

        if is_intra_queue_dup:
            continue

        if sha256:
            seen_shas.add(sha256)
        if phash and len(phash) == 16:
            seen_phashes.append((phash, f"#{item_id} '{title}'"))

    return purged_count


def backfill_fingerprints_batch(limit: int = 200) -> int:
    """
    Backfills missing sha256 and phash for uploaded_files and pin_queue rows
    using existing files in processed/ or downloads/.
    Returns count of updated rows.
    """
    try:
        from image_dedup import get_image_fingerprints
    except ImportError:
        return 0

    updated = 0
    with _get_conn() as conn:
        # 1. Backfill pin_queue
        q_rows = conn.execute(
            "SELECT id, image_path FROM pin_queue "
            "WHERE (sha256 IS NULL OR sha256 = '' OR phash IS NULL OR phash = '') "
            "LIMIT ?", (limit,)
        ).fetchall()
        for q_id, img_path in q_rows:
            if img_path and os.path.isfile(img_path):
                sha, phash = get_image_fingerprints(img_path)
                if sha or phash:
                    conn.execute(
                        "UPDATE pin_queue SET sha256 = ?, phash = ? WHERE id = ?",
                        (sha, phash, q_id)
                    )
                    updated += 1

        # 2. Backfill uploaded_files
        u_rows = conn.execute(
            "SELECT filename FROM uploaded_files "
            "WHERE (sha256 IS NULL OR sha256 = '' OR phash IS NULL OR phash = '') "
            "LIMIT ?", (limit,)
        ).fetchall()
        for (fn,) in u_rows:
            target_path = None
            for cand in [os.path.join("processed", fn), os.path.join("downloads", fn), fn]:
                if os.path.isfile(cand):
                    target_path = cand
                    break
            if target_path:
                sha, phash = get_image_fingerprints(target_path)
                if sha or phash:
                    conn.execute(
                        "UPDATE uploaded_files SET sha256 = ?, phash = ? WHERE filename = ?",
                        (sha, phash, fn)
                    )
                    updated += 1
        conn.commit()
    return updated


def get_uploaded_file_image_url(filename: str) -> str:
    """Returns the hosted public image URL stored in uploaded_files for a filename."""
    if not filename:
        return ""
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT image_url FROM uploaded_files WHERE filename = ? ORDER BY uploaded_at DESC LIMIT 1",
                (filename,)
            ).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
    except Exception:
        pass
    return ""


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
                priority: int = 0, scheduled_date: str = "",
                sha256: str = "", phash: str = "",
                is_organic: int = 0) -> bool:
    """
    Add a pin to the persistent queue.
    priority=1 → new image (posted before backlog)
    priority=0 → backlog image
    is_organic=1 → organic trust pin (no affiliate link)
    Returns True if added, False if already in queue or duplicate.
    """
    if (not sha256 or not phash) and image_path and os.path.isfile(image_path):
        try:
            from image_dedup import get_image_fingerprints
            calc_sha, calc_phash = get_image_fingerprints(image_path)
            sha256 = sha256 or calc_sha
            phash = phash or calc_phash
        except Exception:
            pass

    # Pre-enqueue visual duplicate check
    if sha256 or phash or (image_path and os.path.isfile(image_path)):
        is_dup, reason = is_image_duplicate(filepath=image_path, sha256=sha256, phash=phash)
        if is_dup:
            logger.warning(f"[DB] Rejecting enqueue_pin for '{title}': {reason}")
            return False

    if not is_organic:
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
                     image_url, board_id, priority, scheduled_date, sha256, phash, is_organic)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (post_id, image_path, title, description, link, anime_name,
                  image_url, board_id, priority, scheduled_date, sha256, phash, is_organic))
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
                   anime_name, image_url, priority, board_id, is_organic
            FROM pin_queue
            WHERE scheduled_date <= ?
            ORDER BY priority DESC, id ASC
            LIMIT 1
        """, (today_str,)).fetchone()
    if not row:
        return None
    link_val = row[5]
    is_organic = row[10] if len(row) > 10 and row[10] is not None else 0
    if not is_organic:
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
        "is_organic": is_organic,
    }


def should_next_pin_be_organic(target_ratio: float = 0.20) -> bool:
    """
    Checks recent upload history to determine if the next pin should be organic.
    Maintains the 80/20 ratio (1 organic pin for every 4 affiliate pins).
    Safety Rule: If the last 4 consecutive posts were all monetized (is_organic=0),
    the 5th post MUST be organic to protect domain trust.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT is_organic FROM uploaded_files
            ORDER BY uploaded_at DESC LIMIT 4
        """).fetchall()
        if len(rows) >= 4 and all((r[0] or 0) == 0 for r in rows):
            return True
        recent_20 = conn.execute("""
            SELECT is_organic FROM uploaded_files
            ORDER BY uploaded_at DESC LIMIT 20
        """).fetchall()
        if recent_20:
            org_count = sum(1 for r in recent_20 if (r[0] or 0) == 1)
            actual_ratio = org_count / len(recent_20)
            if actual_ratio < target_ratio and len(rows) >= 2 and all((r[0] or 0) == 0 for r in rows[:2]):
                return True
        return False


def get_organic_trust_stats() -> dict:
    """
    Returns telemetry on the 80/20 organic trust ratio:
    total posts, organic count, monetized count, ratio percentage, and current consecutive streak.
    """
    with _get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM uploaded_files").fetchone()[0]
        organic = conn.execute("SELECT COUNT(*) FROM uploaded_files WHERE is_organic = 1").fetchone()[0]
        monetized = total - organic
        ratio_pct = round((organic / max(1, total)) * 100, 1)

        rows = conn.execute("""
            SELECT is_organic FROM uploaded_files
            ORDER BY uploaded_at DESC LIMIT 10
        """).fetchall()
        streak = 0
        for r in rows:
            if (r[0] or 0) == 0:
                streak += 1
            else:
                break

        queue_org = conn.execute("SELECT COUNT(*) FROM pin_queue WHERE is_organic = 1").fetchone()[0]

    return {
        "total_posts": total,
        "organic_posts": organic,
        "monetized_posts": monetized,
        "ratio_pct": ratio_pct,
        "monetized_streak": streak,
        "queue_organic": queue_org,
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


def move_to_dead_letter(pin: dict, fail_reason: str = ""):
    """
    Moves a failed pin from pin_queue to dead_letter_queue after exhausting retries.
    Preserves full pin metadata so the admin can review and manually re-queue via /deadqueue.
    The pin is NOT deleted from pin_queue here — caller must call remove_queued_pin() separately.
    """
    with _get_conn() as conn:
        try:
            conn.execute("""
                INSERT INTO dead_letter_queue
                    (post_id, image_path, title, description, link, anime_name,
                     image_url, fail_reason, retry_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pin.get("post_id", ""),
                pin.get("image_path", ""),
                pin.get("title", ""),
                pin.get("description", ""),
                pin.get("link", ""),
                pin.get("anime_name", ""),
                pin.get("image_url", ""),
                fail_reason[:500] if fail_reason else "Max retries exceeded",
                pin.get("retry_count", 0),
            ))
            conn.commit()
            logger.info(f"[DB] Pin moved to dead-letter queue: '{pin.get('title', '')}' — reason: {fail_reason[:80]}")
        except Exception as e:
            logger.error(f"[DB] move_to_dead_letter error: {e}")


def get_dead_letter_pins(limit: int = 20) -> list:
    """
    Returns the most recent dead-letter pins for admin review (/deadqueue command).
    Each row is a dict with full pin metadata plus fail_reason and failed_at.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT id, title, anime_name, fail_reason, retry_count, failed_at
            FROM dead_letter_queue
            ORDER BY failed_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "anime_name": r[2],
            "fail_reason": r[3], "retry_count": r[4], "failed_at": r[5],
        }
        for r in rows
    ]


def count_dead_letter_pins() -> int:
    """Returns total number of pins in the dead-letter queue."""
    with _get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM dead_letter_queue").fetchone()[0]


def clear_dead_letter_queue() -> int:
    """Clears all entries from the dead-letter queue. Returns count deleted."""
    with _get_conn() as conn:
        conn.execute("DELETE FROM dead_letter_queue")
        conn.commit()
        return conn.execute("SELECT changes()").fetchone()[0]


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
                   anime_name, image_url, priority, board_id, is_organic
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
    is_organic = row[10] if len(row) > 10 and row[10] is not None else 0
    if not is_organic:
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
        "is_organic": is_organic,
    }


# ── Click Tracking & Affiliate Analytics ──────────────────────────────────────

def create_tracked_link(target_url: str, anime_name: str = "", title: str = "", image_url: str = "") -> str:
    """
    Stores a destination URL and returns a short code.
    If target_url is a direct product (/dp/ASIN), embeds the ASIN (e.g. 'dp_B0CHR8R1L3')
    making the link stateless and 100% resilient across server restarts.
    Also stores image_url for the multi-merchant showcase bridge page.
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
                INSERT OR REPLACE INTO tracked_links (code, target_url, anime_name, title, image_url)
                VALUES (?, ?, ?, ?, ?)
            """, (code, target_url, anime_name, title, image_url))
            conn.commit()
        except Exception as e:
            logger.error(f"[DB] Error creating tracked link: {e}")
    return code


def record_tracked_link(code: str, target_url: str, anime_name: str = "", title: str = "", image_url: str = "") -> bool:
    """Store or update a tracked link with a specific pre-assigned code."""
    with _get_conn() as conn:
        try:
            conn.execute("""
                INSERT OR REPLACE INTO tracked_links (code, target_url, anime_name, title, image_url)
                VALUES (?, ?, ?, ?, ?)
            """, (code.strip(), target_url.strip(), (anime_name or "")[:200], (title or "")[:200], (image_url or "").strip()))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"[DB] Error recording tracked link: {e}")
            return False


def get_tracked_link_data(code: str) -> dict:
    """
    Returns full tracked link data (target_url, anime_name, title, image_url).
    Supports stateless ASIN self-healing if code was not found in DB.
    """
    from config import AMAZON_AFFILIATE_TAG
    tag = AMAZON_AFFILIATE_TAG or "animeasthet06-21"

    with _get_conn() as conn:
        row = conn.execute("""
            SELECT target_url, anime_name, title, image_url FROM tracked_links WHERE code = ?
        """, (code,)).fetchone()
        if row:
            return {
                "target_url": row[0] or "",
                "anime_name": row[1] or "Anime",
                "title":      row[2] or "Anime Merchandise",
                "image_url":  row[3] or "",
            }

    # Stateless recovery for ASIN codes
    asin = None
    if code.startswith("dp_") and len(code) == 13 and code[3:].isalnum():
        asin = code[3:]
    elif len(code) == 10 and code.isalnum() and code.isupper():
        asin = code

    if asin:
        direct_product_url = f"https://www.amazon.in/dp/{asin}?tag={tag}&linkCode=ogi&th=1&psc=1"
        return {
            "target_url": direct_product_url,
            "anime_name": "Anime",
            "title":      "Anime Merchandise",
            "image_url":  "",
        }

    fallback_url = f"https://www.amazon.in/s?k=anime+merchandise+poster+figure&tag={tag}&sort=review-rank"
    return {
        "target_url": fallback_url,
        "anime_name": "Anime",
        "title":      "Anime Merchandise",
        "image_url":  "",
    }


def record_link_click(code: str, user_agent: str = "", referrer: str = "", merchant: str = "amazon") -> tuple[str, str, str]:
    """
    Records a click for the given link code and merchant ('amazon', 'displate', 'wallpaper').
    Returns: (target_url, anime_name, title)
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
                INSERT INTO link_clicks (code, anime_name, title, user_agent, referrer, merchant)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (code, anime_name, title, user_agent[:255], referrer[:255], merchant[:50]))
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
                    INSERT OR REPLACE INTO tracked_links (code, target_url, anime_name, title, image_url)
                    VALUES (?, ?, ?, ?, '')
                """, (code, direct_product_url, "Anime", "Anime Product"))
                conn.execute("""
                    INSERT INTO link_clicks (code, anime_name, title, user_agent, referrer, merchant)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (code, "Anime", "Anime Product", user_agent[:255], referrer[:255], merchant[:50]))
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
    Returns full click analytics and conversion estimates for Telegram commands,
    including merchant breakdown (Amazon, Displate, Wallpaper).
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

        # Merchant breakdown (Today & Total)
        today_merchant_rows = conn.execute("""
            SELECT COALESCE(merchant, 'amazon') as m, COUNT(*) as cnt
            FROM link_clicks
            WHERE date(clicked_at, '+5 hours', '+30 minutes') = date('now', '+5 hours', '+30 minutes')
            GROUP BY m
        """).fetchall()
        today_by_merchant = {r[0]: r[1] for r in today_merchant_rows}

        total_merchant_rows = conn.execute("""
            SELECT COALESCE(merchant, 'amazon') as m, COUNT(*) as cnt
            FROM link_clicks
            GROUP BY m
        """).fetchall()
        by_merchant = {r[0]: r[1] for r in total_merchant_rows}

    # Calculate estimated conversions based on standard e-commerce metrics (2-4% CR, ₹35-80 avg commission)
    est_orders_min = max(0, int(week_clicks * 0.02))
    est_orders_max = max(1 if week_clicks >= 5 else 0, int(week_clicks * 0.05))
    est_revenue_min = est_orders_min * 35
    est_revenue_max = est_orders_max * 95

    return {
        "today": today_clicks,
        "week": week_clicks,
        "total": total_clicks,
        "today_by_merchant": today_by_merchant,
        "by_merchant": by_merchant,
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
            SET target_url = ?,
                is_healed = 1,
                heal_count = COALESCE(heal_count, 0) + 1,
                last_checked_at = CURRENT_TIMESTAMP
            WHERE code = ?
        """, (new_target_url, code))
        conn.commit()
        return cursor.rowcount > 0


def mark_link_health(code: str, is_healthy: bool, new_target_url: str = None) -> bool:
    """
    Records the audit result for a tracked link.
    If new_target_url is given, updates destination and increments heal_count.
    Otherwise simply updates last_checked_at timestamp.
    """
    with _get_conn() as conn:
        if new_target_url:
            cursor = conn.execute("""
                UPDATE tracked_links
                SET target_url = ?,
                    is_healed = 1,
                    heal_count = COALESCE(heal_count, 0) + 1,
                    last_checked_at = CURRENT_TIMESTAMP
                WHERE code = ?
            """, (new_target_url, code))
        else:
            cursor = conn.execute("""
                UPDATE tracked_links
                SET last_checked_at = CURRENT_TIMESTAMP
                WHERE code = ?
            """, (code,))
        conn.commit()
        return cursor.rowcount > 0


def get_top_priority_links_to_heal(limit: int = 25) -> list[dict]:
    """
    Returns links prioritized by popularity (most clicks) and audit staleness.
    High-traffic links with dead products get repaired first to maximize saved revenue.
    """
    with _get_conn() as conn:
        rows = conn.execute("""
            SELECT t.code, t.target_url, t.anime_name, t.title, t.image_url,
                   t.last_checked_at, t.is_healed, t.heal_count,
                   COUNT(c.id) AS click_count
            FROM tracked_links t
            LEFT JOIN link_clicks c ON t.code = c.code
            GROUP BY t.id
            ORDER BY
                click_count DESC,
                CASE WHEN t.last_checked_at IS NULL THEN 0 ELSE 1 END ASC,
                t.last_checked_at ASC,
                t.id DESC
            LIMIT ?
        """, (limit,)).fetchall()

    return [
        {
            "code": r[0],
            "target_url": r[1],
            "anime_name": r[2] or "Anime",
            "title": r[3] or "Anime Merch",
            "image_url": r[4] or "",
            "last_checked_at": r[5],
            "is_healed": bool(r[6]),
            "heal_count": r[7] or 0,
            "click_count": r[8] or 0,
        }
        for r in rows
    ]


def get_link_healing_stats() -> dict:
    """
    Returns aggregated metrics on link health and autonomous healing activity.
    """
    with _get_conn() as conn:
        total_links = conn.execute("SELECT COUNT(*) FROM tracked_links").fetchone()[0]
        healed_links = conn.execute("SELECT COUNT(*) FROM tracked_links WHERE is_healed = 1").fetchone()[0]
        total_heals = conn.execute("SELECT COALESCE(SUM(heal_count), 0) FROM tracked_links").fetchone()[0]
        checked_links = conn.execute("SELECT COUNT(*) FROM tracked_links WHERE last_checked_at IS NOT NULL").fetchone()[0]
        top_healed = conn.execute("""
            SELECT code, anime_name, title, target_url, heal_count
            FROM tracked_links
            WHERE is_healed = 1
            ORDER BY heal_count DESC, last_checked_at DESC
            LIMIT 5
        """).fetchall()

    return {
        "total_links": total_links,
        "healed_links": healed_links,
        "total_heals": total_heals,
        "checked_links": checked_links,
        "top_healed": [
            {"code": r[0], "anime": r[1], "title": r[2], "target_url": r[3], "heals": r[4]}
            for r in top_healed
        ]
    }


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


def get_stock_stats_alltime() -> dict:
    """
    Returns all-time total successful upload count per stock platform
    and a grand total. Used by /stockstats Telegram command.
    """
    platforms = ["shutterstock", "adobe", "freepik", "depositphotos", "dreamstime", "123rf"]
    result = {}
    with _get_conn() as conn:
        for p in platforms:
            row = conn.execute(
                "SELECT COUNT(*) FROM stock_uploads WHERE platform = ? AND status = 'success'",
                (p,)
            ).fetchone()
            result[p] = row[0] if row else 0
    result["total"] = sum(result.values())
    return result


# ── Backlink SEO Waterfall Tracking Functions ───────────────────────────────

def record_backlink(filename: str, anime_name: str, platform: str, target_url: str, anchor_type: str = "pinterest"):
    """Records a syndication backlink generated for an artwork."""
    try:
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO backlink_ledger (filename, anime_name, platform, target_url, anchor_type)
                VALUES (?, ?, ?, ?, ?)
                """,
                (filename, anime_name, platform.lower().strip(), target_url.strip(), anchor_type),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"[Database] record_backlink failed: {e}")


def get_backlink_stats() -> dict:
    """Returns all-time and today's backlink counts grouped by platform."""
    try:
        with _get_conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM backlink_ledger").fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM backlink_ledger WHERE date(created_at, '+5 hours', '+30 minutes') = date('now', '+5 hours', '+30 minutes')"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT platform, COUNT(*) FROM backlink_ledger GROUP BY platform ORDER BY COUNT(*) DESC"
            ).fetchall()
            by_platform = {r[0]: r[1] for r in rows}
            return {
                "total": total,
                "today": today,
                "by_platform": by_platform,
            }
    except Exception as e:
        logger.debug(f"[Database] get_backlink_stats failed: {e}")
        return {"total": 0, "today": 0, "by_platform": {}}


def record_surge_alert(code: str, anime_name: str, visits_count: int, clicks_count: int, surge_score: float) -> None:
    """Record an alert sent for a viral surge on a pin code."""
    try:
        with _get_conn() as conn:
            conn.execute(
                """
                INSERT INTO viral_surge_alerts (code, anime_name, visits_count, clicks_count, surge_score)
                VALUES (?, ?, ?, ?, ?)
                """,
                (code.strip(), (anime_name or "")[:200], visits_count, clicks_count, float(surge_score)),
            )
            conn.commit()
    except Exception as e:
        logger.debug(f"[Database] record_surge_alert failed: {e}")


def is_surge_alerted_recently(code: str, cooldown_hours: int = 12) -> bool:
    """Check if an alert was already triggered for this code within cooldown window."""
    try:
        import datetime
        cutoff = (datetime.datetime.utcnow() - datetime.timedelta(hours=cooldown_hours)).strftime("%Y-%m-%d %H:%M:%S")
        with _get_conn() as conn:
            row = conn.execute(
                """
                SELECT id FROM viral_surge_alerts
                WHERE code = ? AND alerted_at >= ?
                LIMIT 1
                """,
                (code.strip(), cutoff),
            ).fetchone()
            return bool(row)
    except Exception as e:
        logger.debug(f"[Database] is_surge_alerted_recently failed: {e}")
        return False


def boost_queue_priority_by_anime(anime_name: str, new_priority: int = 2, limit: int = 2) -> int:
    """
    Boost priority of queued pins for a specific anime to express priority (priority=2).
    Also sets scheduled_date to today so they are posted at the very next slot.
    Returns the count of pins boosted.
    """
    if not anime_name:
        return 0
    clean_anime = anime_name.strip().lower()
    today_str = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
    try:
        with _get_conn() as conn:
            # Find eligible matching pins in queue
            rows = conn.execute(
                """
                SELECT id FROM pin_queue
                WHERE LOWER(anime_name) = ? OR LOWER(anime_name) LIKE ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (clean_anime, f"%{clean_anime}%", limit),
            ).fetchall()

            if not rows:
                return 0

            ids = [r[0] for r in rows]
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE pin_queue
                SET priority = ?, scheduled_date = ?
                WHERE id IN ({placeholders})
                """,
                [new_priority, today_str] + ids,
            )
            conn.commit()
            return len(ids)
    except Exception as e:
        logger.error(f"[Database] boost_queue_priority_by_anime failed: {e}")
        return 0


def get_active_surge_history(limit: int = 10) -> list[dict]:
    """Returns recent surge alerts from the database."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT code, anime_name, visits_count, clicks_count, surge_score, alerted_at
                FROM viral_surge_alerts
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [
                {
                    "code": r[0],
                    "anime_name": r[1],
                    "visits_count": r[2],
                    "clicks_count": r[3],
                    "surge_score": r[4],
                    "alerted_at": r[5],
                }
                for r in rows
            ]
    except Exception as e:
        logger.debug(f"[Database] get_active_surge_history failed: {e}")
        return []


# ── A/B Split Testing Helpers ────────────────────────────────────────────────

def record_ab_test(test_id: str, variant: str, post_id: str, anime_name: str,
                   title: str, image_url: str, bridge_code: str):
    """Register a new A/B test variant in the database."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO ab_tests
                    (test_id, variant, post_id, anime_name, title, image_url, bridge_code)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (test_id, variant, post_id, anime_name, title, image_url, bridge_code))
            conn.commit()
    except Exception as e:
        logger.error(f"[Database] record_ab_test failed: {e}")


def update_ab_test_stats(bridge_code: str, visits: int, clicks: int):
    """Update visit/click counts for an A/B variant by its bridge code."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                UPDATE ab_tests SET visits = ?, clicks = ?
                WHERE bridge_code = ? AND status = 'running'
            """, (visits, clicks, bridge_code))
            conn.commit()
    except Exception as e:
        logger.error(f"[Database] update_ab_test_stats failed: {e}")


def get_running_ab_tests() -> list[dict]:
    """Returns all currently running A/B tests."""
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT test_id, variant, anime_name, title, bridge_code,
                       visits, clicks, created_at
                FROM ab_tests WHERE status = 'running'
                ORDER BY created_at DESC
            """).fetchall()
        return [
            {
                "test_id": r[0],
                "variant": r[1],
                "anime": r[2],
                "title": r[3],
                "code": r[4],
                "visits": r[5],
                "clicks": r[6],
                "created_at": r[7],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[Database] get_running_ab_tests failed: {e}")
        return []


def declare_ab_winner(test_id: str, winner_variant: str):
    """Mark winner/loser for a completed A/B test pair."""
    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_conn() as conn:
            conn.execute("""
                UPDATE ab_tests SET status = 'winner', decided_at = ?
                WHERE test_id = ? AND variant = ?
            """, (now, test_id, winner_variant))
            loser = 'B' if winner_variant == 'A' else 'A'
            conn.execute("""
                UPDATE ab_tests SET status = 'loser', decided_at = ?
                WHERE test_id = ? AND variant = ?
            """, (now, test_id, loser))
            conn.commit()
    except Exception as e:
        logger.error(f"[Database] declare_ab_winner failed: {e}")


# ── Revenue Tracking Helpers ─────────────────────────────────────────────────

def get_revenue_summary(days: int = 7) -> dict:
    """Aggregate estimated revenue for the last N days."""
    import datetime
    cutoff = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
              - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        with _get_conn() as conn:
            total = conn.execute("""
                SELECT COALESCE(SUM(est_revenue), 0), COALESCE(SUM(clicks), 0)
                FROM revenue_log WHERE date_ist >= ?
            """, (cutoff,)).fetchone()
            by_anime = conn.execute("""
                SELECT anime_name, SUM(clicks) as c, SUM(est_revenue) as r
                FROM revenue_log WHERE date_ist >= ?
                GROUP BY anime_name ORDER BY r DESC LIMIT 10
            """, (cutoff,)).fetchall()
        return {
            "total_revenue": round(total[0], 2) if total else 0.0,
            "total_clicks": total[1] if total else 0,
            "by_anime": [
                {"anime": r[0], "clicks": r[1], "revenue": round(r[2], 2)}
                for r in by_anime
            ]
        }
    except Exception as e:
        logger.error(f"[Database] get_revenue_summary failed: {e}")
        return {"total_revenue": 0.0, "total_clicks": 0, "by_anime": []}


# ── Ghost Pin Reviver Helpers ────────────────────────────────────────────────

def get_uploaded_files_with_zero_visits(days_old: int = 7, limit: int = 10) -> list[dict]:
    """Returns uploaded pins older than N days with NO bridge_visits recorded."""
    import datetime
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT uf.filename, uf.title, uf.anime_name, uf.image_url, uf.uploaded_at
                FROM uploaded_files uf
                LEFT JOIN bridge_visits bv ON (
                    bv.title = uf.title
                    OR (bv.anime_name = uf.anime_name AND bv.title LIKE '%' || substr(uf.title, 1, 15) || '%')
                )
                WHERE uf.uploaded_at <= ?
                  AND uf.image_url IS NOT NULL AND uf.image_url != ''
                  AND bv.id IS NULL
                GROUP BY uf.filename
                ORDER BY uf.uploaded_at ASC
                LIMIT ?
            """, (cutoff, limit)).fetchall()
        return [
            {
                "filename": r[0],
                "title": r[1],
                "anime": r[2],
                "image_url": r[3],
                "uploaded_at": r[4],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"[Database] get_uploaded_files_with_zero_visits failed: {e}")
        return []


# Initialize on import
init_db()




