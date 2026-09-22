"""
check_db.py - SQLite Database Inspector & Platform Sync Status
==============================================================
Provides quick CLI visibility into recent uploads, cross-posts, and queue state.
"""

import sys
import sqlite3
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
    
    print("\n📌 RECENT PINTEREST UPLOADS (Last 3):")
    cur.execute("SELECT filename, uploaded_at, title, image_url FROM uploaded_files ORDER BY rowid DESC LIMIT 3")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  • [{r['uploaded_at']}] {r['title'][:40]} | File: {r['filename']}")
            print(f"    URL: {str(r['image_url'])[:80]}...")
    else:
        print("  (No uploads yet)")

    print("\n🦛 RECENT IMGHIPPO POSTS (Last 3):")
    cur.execute("SELECT filename, posted_at, title, post_url FROM imghippo_posts ORDER BY id DESC LIMIT 3")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  • [{r['posted_at']}] {r['title'][:40]} | File: {r['filename']}")
            print(f"    Post URL: {r['post_url']}")
    else:
        print("  (No Imghippo posts yet)")


    print("\n📷 RECENT FREEIMAGE POSTS (Last 3):")
    cur.execute("SELECT filename, posted_at, title, post_url FROM freeimage_posts ORDER BY id DESC LIMIT 3")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  • [{r['posted_at']}] {r['title'][:40]} | File: {r['filename']}")
            print(f"    Post URL: {r['post_url']}")
    else:
        print("  (No Freeimage posts yet)")

    print("\n📸 RECENT PIXELFED POSTS (Last 3):")
    cur.execute("SELECT filename, posted_at, title, post_url FROM pixelfed_posts ORDER BY id DESC LIMIT 3")
    rows = cur.fetchall()
    if rows:
        for r in rows:
            print(f"  • [{r['posted_at']}] {r['title'][:40]} | File: {r['filename']}")
            print(f"    Post URL: {r['post_url']}")
    else:
        print("  (No Pixelfed posts yet)")

print("\n" + "=" * 65)
