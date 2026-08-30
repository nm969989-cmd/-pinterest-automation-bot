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

@app.route('/r/<code>')
def redirect_link(code):
    """
    Affiliate link click tracker and redirector.
    Intercepts clicks from Pinterest, records analytics, and redirects to Amazon.
    """
    from flask import redirect, request
    try:
        from database import record_link_click, count_clicks_today
        user_agent = request.headers.get('User-Agent', '')
        referrer   = request.referrer or ''

        target_url, anime_name, title = record_link_click(code, user_agent, referrer)
        today_total = count_clicks_today()

        # Notify admin via Telegram if enabled
        from config import CLICK_NOTIFICATION
        if CLICK_NOTIFICATION:
            try:
                from telegram_bot import notify_link_clicked
                notify_link_clicked(anime_name=anime_name, title=title, today_count=today_total)
            except Exception:
                pass

        return redirect(target_url, code=302)
    except Exception as e:
        logging.error(f"[Redirect] Error handling /r/{code}: {e}")
        from config import AMAZON_AFFILIATE_TAG
        return redirect(f"https://www.amazon.in/s?k=anime+merchandise&tag={AMAZON_AFFILIATE_TAG or 'aniflexindia-21'}", code=302)


def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
