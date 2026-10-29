"""
check_db.py - SQLite Database Inspector & Platform Sync Status
==============================================================
Provides quick CLI visibility into recent uploads, cross-posts, and queue state.
Run directly: python check_db.py
"""

import sys
import sqlite3


def _short(value, width: int = 40) -> str:
    return str(value if value is not None else "")[:width]


def _print_recent(cur, heading: str, query: str, empty_msg: str, has_image_url: bool = False):
    print(f"\n{heading}")
    try:
        cur.execute(query)
        rows = cur.fetchall()
    except sqlite3.Error as e:
        print(f"  ⚠️ Could not read table: {e}")
        return
    if not rows:
        print(f"  ({empty_msg})")
        return
    for r in rows:
        ts = r[1]
        print(f"  • [{ts}] {_short(r['title'])} | File: {r['filename']}")
        if has_image_url:
            print(f"    URL: {_short(r['image_url'], 80)}...")
        else:
            print(f"    Post URL: {r['post_url']}")


def main():
    from database import get_multi_platform_stats, get_queue_counts, _get_conn

    # Force UTF-8 console output
    sys.stdout.reconfigure(encoding='utf-8')

    print("=" * 65)
    print("     ANIME PINTEREST BOT — DATABASE & CROSS-POST INSPECTOR")
    print("=" * 65)

    # 1. Multi-Platform Totals
    print("\n📊 CROSS-PLATFORM POSTING TOTALS:")
    try:
        stats = get_multi_platform_stats()
        queue = get_queue_counts()
        print(f"  • Queue Status    : {queue['new']} new | {queue['backlog']} backlog | {queue['total']} total")
        print(f"  • Pinterest Uploads: {stats['pinterest']}")
        print(f"  • Freeimage.host   : {stats['freeimage']}")
        print(f"  • Imghippo         : {stats['imghippo']}")
        print(f"  • Pixelfed         : {stats['pixelfed']}")
        print(f"  • Are.na           : {stats['arena']}")
        print(f"  • Bluesky          : {stats['bluesky']}")
        print(f"  • Raindrop.io      : {stats['raindrop']}")
        print(f"  • Mastodon         : {stats['mastodon']}")
    except Exception as e:
        print(f"  ⚠️ Error fetching multi-platform stats: {e}")

    # 2. Detailed Table Inspect
    with _get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        _print_recent(
            cur, "📌 RECENT PINTEREST UPLOADS (Last 3):",
            "SELECT filename, uploaded_at, title, image_url FROM uploaded_files ORDER BY rowid DESC LIMIT 3",
            "No uploads yet", has_image_url=True,
        )
        _print_recent(
            cur, "🦛 RECENT IMGHIPPO POSTS (Last 3):",
            "SELECT filename, posted_at, title, post_url FROM imghippo_posts ORDER BY id DESC LIMIT 3",
            "No Imghippo posts yet",
        )
        _print_recent(
            cur, "📷 RECENT FREEIMAGE POSTS (Last 3):",
            "SELECT filename, posted_at, title, post_url FROM freeimage_posts ORDER BY id DESC LIMIT 3",
            "No Freeimage posts yet",
        )
        _print_recent(
            cur, "📸 RECENT PIXELFED POSTS (Last 3):",
            "SELECT filename, posted_at, title, post_url FROM pixelfed_posts ORDER BY id DESC LIMIT 3",
            "No Pixelfed posts yet",
        )

    print("\n" + "=" * 65)


if __name__ == "__main__":
    main()
