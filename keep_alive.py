import os
import datetime
from flask import Flask, jsonify
from threading import Thread
import logging

# Disable noisy Flask access logs
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app   = Flask(__name__)
_start_time = datetime.datetime.utcnow()

@app.route('/')
def home():
    uptime = datetime.datetime.utcnow() - _start_time
    h = int(uptime.total_seconds() // 3600)
    m = int((uptime.total_seconds() % 3600) // 60)
    return f"Animanoizing Bot is alive! Uptime: {h}h {m}m", 200

@app.route('/health')
def health():
    """Rich health check — used by cron-job.org and Render."""
    try:
        from crash_protection import get_memory_mb, get_disk_usage_mb
        from database import get_queue_counts, count_posts_today
        import datetime as dt
        today = (dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)).strftime("%Y-%m-%d")
        mem  = round(get_memory_mb(), 1)
        disk = round(get_disk_usage_mb("downloads") + get_disk_usage_mb("processed"), 1)
        q    = get_queue_counts()
        posted_today = count_posts_today(today)
        uptime = datetime.datetime.utcnow() - _start_time

        return jsonify({
            "status":      "ok",
            "uptime_min":  int(uptime.total_seconds() // 60),
            "memory_mb":   mem,
            "disk_mb":     disk,
            "posted_today": posted_today,
            "queue":       q,
            "warning":     "HIGH MEMORY" if mem > 400 else None,
        }), 200
    except Exception as e:
        return jsonify({"status": "ok", "error": str(e)}), 200

def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
