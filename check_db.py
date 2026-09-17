import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('bot_state.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== UPLOADED FILES (last 5) ===")
cur.execute('SELECT filename, uploaded_at, title, image_url FROM uploaded_files ORDER BY rowid DESC LIMIT 5')
for row in cur.fetchall():
    print(f"  {row['filename']} | {row['uploaded_at']}")
    print(f"  image_url: {str(row['image_url'])[:120]}")
    print()

print("\n=== BLUESKY POSTS ===")
cur.execute('SELECT * FROM bluesky_posts ORDER BY rowid DESC LIMIT 5')
for row in cur.fetchall():
    d = dict(row)
    for k,v in d.items(): print(f"  {k}: {str(v)[:100]}")
    print()

print("\n=== MASTODON POSTS ===")
cur.execute('SELECT * FROM mastodon_posts ORDER BY rowid DESC LIMIT 5')
for row in cur.fetchall():
    d = dict(row)
    for k,v in d.items(): print(f"  {k}: {str(v)[:100]}")
    print()

print("\n=== ARENA POSTS ===")
cur.execute('SELECT * FROM arena_posts ORDER BY rowid DESC LIMIT 5')
for row in cur.fetchall():
    d = dict(row)
    for k,v in d.items(): print(f"  {k}: {str(v)[:100]}")
    print()

conn.close()
