import os
import time
import datetime
import threading
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
        # Layer 1 fallback: clean anime merchandise search (NO broken category node)
        return redirect(
            f"https://www.amazon.in/s?k=anime+merchandise+poster+figure&tag={tag}&sort=review-rank",
            code=302
        )


@app.route('/p/<code>')
def bridge_page(code):
    """
    🌉 Clean Bridge / Showcase Landing Page — Bypass Pinterest Affiliate Flagging.

    Instead of linking directly to Amazon (which Pinterest's algorithm detects and
    shadowbans), each pin links to THIS page on your own domain. Pinterest's algorithm
    sees a legitimate independent website, not an affiliate redirect.

    The page shows:
      - Anime title and description
      - The high-quality pin image (loaded from Cloudinary CDN)
      - A prominent "Shop on Amazon" CTA button (with affiliate tag)
      - Beautiful, mobile-first design optimized for Pinterest click-throughs

    URL pattern: https://your-app.onrender.com/p/<code>
    """
    from flask import request
    user_agent = request.headers.get('User-Agent', '')
    referrer   = request.referrer or ''

    # Look up pin metadata (title, anime, target URL)
    target_url = ""
    anime_name = "Anime"
    title      = "Anime Merchandise"
    try:
        from database import _get_conn
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT target_url, anime_name, title FROM tracked_links WHERE code = ?",
                (code,)
            ).fetchone()
        if row:
            target_url = row[0] or ""
            anime_name = row[1] or "Anime"
            title      = row[2] or "Anime Merchandise"
    except Exception:
        pass

    if not target_url:
        from config import AMAZON_AFFILIATE_TAG
        tag        = AMAZON_AFFILIATE_TAG or "animeasthet06-21"
        target_url = f"https://www.amazon.in/s?k=anime+merchandise+poster+figure&tag={tag}&sort=review-rank"

    # ── Record bridge PAGE VISIT (funnel analytics — separate from Amazon click) ──
    # The /r/<code> route records the Amazon click when the user taps "Shop on Amazon"
    # Here we record that the user LANDED on the bridge page (from Pinterest or elsewhere)
    try:
        from pinterest_analytics import record_bridge_visit
        record_bridge_visit(code, anime_name, title, user_agent, referrer)
    except Exception:
        pass

    # Render the showcase landing page
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Anime Shop</title>
  <meta name="description" content="Official anime merchandise for {anime_name}. Posters, figures, and collectibles on Amazon." />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="Shop official {anime_name} merchandise — posters, figures &amp; collectibles." />
  <meta property="og:type" content="product" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;900&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #0f0f14;
      --surface: #1a1a24;
      --card:    #22222f;
      --accent:  #e60026;
      --gold:    #f59e0b;
      --text:    #f0f0f5;
      --sub:     #9090aa;
      --radius:  18px;
    }}
    body {{
      font-family: 'Inter', sans-serif;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px 16px 48px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(230,0,38,.15);
      border: 1px solid rgba(230,0,38,.4);
      color: #ff6680;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
      padding: 5px 12px;
      border-radius: 999px;
      margin-bottom: 20px;
    }}
    .card {{
      background: var(--card);
      border-radius: var(--radius);
      max-width: 480px;
      width: 100%;
      overflow: hidden;
      box-shadow: 0 24px 80px rgba(0,0,0,.6), 0 0 0 1px rgba(255,255,255,.06);
      animation: rise .45s cubic-bezier(.22,.68,0,1.2);
    }}
    @keyframes rise {{
      from {{ opacity:0; transform: translateY(30px) scale(.97); }}
      to   {{ opacity:1; transform: translateY(0)   scale(1);    }}
    }}
    .anime-tag {{
      display: block;
      background: linear-gradient(90deg, var(--accent), #ff6047);
      color: #fff;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      padding: 7px 20px;
    }}
    .card-body {{ padding: 24px 24px 28px; }}
    .pin-title {{
      font-size: 1.4rem;
      font-weight: 900;
      line-height: 1.25;
      margin-bottom: 10px;
      background: linear-gradient(135deg, #fff 40%, #c9b8ff);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .pin-desc {{
      font-size: .88rem;
      color: var(--sub);
      line-height: 1.6;
      margin-bottom: 24px;
    }}
    .cta-btn {{
      display: block;
      width: 100%;
      padding: 16px;
      background: linear-gradient(135deg, #f59e0b, #f97316);
      color: #fff;
      font-size: 1.05rem;
      font-weight: 800;
      border: none;
      border-radius: 12px;
      text-decoration: none;
      text-align: center;
      letter-spacing: .02em;
      box-shadow: 0 8px 28px rgba(245,158,11,.35);
      transition: transform .15s, box-shadow .15s;
    }}
    .cta-btn:hover {{
      transform: translateY(-2px);
      box-shadow: 0 12px 36px rgba(245,158,11,.5);
    }}
    .cta-icon {{ margin-right: 8px; }}
    .disclosure {{
      margin-top: 14px;
      font-size: .72rem;
      color: #60607a;
      text-align: center;
    }}
    footer {{
      margin-top: 32px;
      font-size: .75rem;
      color: #40404f;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="badge">✨ Anime Merchandise</div>
  <div class="card">
    <span class="anime-tag">🎌 {anime_name}</span>
    <div class="card-body">
      <h1 class="pin-title">{title}</h1>
      <p class="pin-desc">
        Official {anime_name} merchandise curated for fans. Posters, figures, and collectibles — delivered by Amazon.
      </p>
      <a class="cta-btn" href="{target_url}" rel="sponsored noopener" target="_blank">
        <span class="cta-icon">🛒</span> Shop on Amazon India
      </a>
      <p class="disclosure">
        #ad — As an Amazon Associate, we earn from qualifying purchases. This supports our free content. 🙏
      </p>
    </div>
  </div>
  <footer>AnimAnoizing · Anime Art &amp; Merch Community</footer>
</body>
</html>"""
    return html, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route('/verify_public/<pin_id>')
def verify_pin_public_route(pin_id):
    """
    🔍 Ghost Pin Inspector — checks if a Pinterest pin is publicly visible.
    Usage: GET /verify_public/<pin_id>
    Returns JSON with visibility status.
    """
    try:
        from ghost_pin_checker import check_pin_by_id
        result = check_pin_by_id(pin_id, expected_title="")
        return jsonify(result), 200
    except Exception as e:
        return jsonify({"error": str(e), "pin_id": pin_id}), 500


def run():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)


def _self_ping_loop():
    """
    Pings our own /ping endpoint every 8 minutes to prevent Render free tier
    from spinning down the instance. Without this, the bot goes offline after
    ~15 minutes of inactivity, causing missed posts and morning messages.

    Also pings EXTERNAL_PING_URL if set (e.g. cron-job.org or UptimeRobot URL)
    which provides an external heartbeat even when self-ping fails.
    """
    # Wait for Flask to fully start before attempting the first ping
    time.sleep(30)
    port = int(os.environ.get('PORT', 8080))
    local_url = f"http://localhost:{port}/ping"
    # Optional external ping URL (set in Render env vars or .env)
    # Example: https://cron-job.org or your own UptimeRobot URL
    external_url = os.environ.get('EXTERNAL_PING_URL', '').strip()
    while True:
        try:
            import requests as _req
            _req.get(local_url, timeout=8)
        except Exception:
            pass  # Non-critical — don't log to avoid noise
        # Also ping external URL if configured
        if external_url:
            try:
                import requests as _req
                _req.get(external_url, timeout=10)
            except Exception:
                pass
        time.sleep(480)  # ping every 8 minutes (well within 15-min Render spin-down)


def keep_alive():
    t = Thread(target=run, daemon=True)
    t.start()
    # Self-ping to prevent Render free-tier spin-down (kills posts & morning messages)
    threading.Thread(target=_self_ping_loop, daemon=True, name="SelfPing").start()
