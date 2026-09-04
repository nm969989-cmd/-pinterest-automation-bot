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
    return jsonify({
        "status":  "ok",
        "service": "Animanoizing Pinterest Bot",
        "uptime":  f"{h}h {m}m",
    }), 200


@app.route('/ping')
def ping():
    """Ultra-lightweight keep-alive endpoint for cron-job.org."""
    return "pong", 200

@app.route('/health')
def health():
    """Rich health check — used by cron-job.org and Render."""
    try:
        from crash_protection import get_memory_mb, get_disk_usage_mb
        from database import get_queue_counts, count_posts_today
        import datetime as dt
        # Use IST date — matches our count_posts_today IST offset fix
        now_ist = dt.datetime.utcnow() + dt.timedelta(hours=5, minutes=30)
        today_ist = now_ist.strftime("%Y-%m-%d")
        mem  = round(get_memory_mb(), 1)
        disk = round(get_disk_usage_mb("downloads") + get_disk_usage_mb("processed"), 1)
        q    = get_queue_counts()
        posted_today = count_posts_today(today_ist)
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
        from config import AMAZON_AFFILIATE_TAG
        tag = AMAZON_AFFILIATE_TAG or "animeasthet06-21"
        return redirect(
            f"https://www.amazon.in/s?k=anime+action+figure+poster&rh=n%3A1350387031&tag={tag}&sort=review-rank",
            code=302
        )


def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
