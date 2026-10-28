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

def get_visitor_country(req) -> str:
    """
    Extracts visitor country code from reverse proxy / CDN headers (Cloudflare, Render, etc.).
    Returns uppercase 2-letter ISO country code (e.g., 'IN', 'US') or empty string if unknown.
    """
    country = (
        req.headers.get("CF-IPCountry")
        or req.headers.get("X-Country-Code")
        or req.headers.get("X-GeoIP-Country")
        or ""
    ).strip().upper()
    if country in ("XX", "T1", "UNKNOWN", "NONE"):
        return ""
    return country[:2]


@app.route('/r/<code>')
def redirect_link(code):
    """
    Multi-Merchant & Geo-Targeted Affiliate Link Tracker and Redirector.
    Supports query param `?m=amazon|amazon_in|amazon_us|displate|wallpaper`.
    Automatically detects visitor country (Cloudflare CF-IPCountry) and dual-routes:
      - 🇮🇳 India visitors -> Amazon India with tag animeasthet06-21 (₹ INR)
      - 🇺🇸 US/Global visitors -> Amazon US with tag animeasthet-20 ($ USD)
    """
    from flask import redirect, request
    merchant   = request.args.get('m', 'amazon').strip().lower()
    user_agent = request.headers.get('User-Agent', '')
    referrer   = request.referrer or ''
    country    = get_visitor_country(request)

    try:
        from database import record_link_click, count_clicks_today, get_tracked_link_data
        from config import AMAZON_AFFILIATE_TAG_IN, AMAZON_AFFILIATE_TAG_US, AMAZON_AFFILIATE_TAG
        tag_in = AMAZON_AFFILIATE_TAG_IN or "animeasthet06-21"
        tag_us = AMAZON_AFFILIATE_TAG_US or AMAZON_AFFILIATE_TAG or "animeasthet-20"

        link_data = get_tracked_link_data(code)
        target_amazon_url = link_data.get("target_url", "")
        anime_name        = link_data.get("anime_name", "Anime")
        title             = link_data.get("title", "Anime Merchandise")
        image_url         = link_data.get("image_url", "")

        # Dynamic Link Redirection & Stock Healer: hot-swaps dead/out-of-stock items in <15ms
        try:
            from link_healer import resolve_or_heal_link
            target_amazon_url = resolve_or_heal_link(
                code, fallback_anime=anime_name, fallback_title=title, current_url=target_amazon_url
            )
        except Exception:
            pass

        # Determine if visitor should be routed to Amazon India
        is_india_dest = (country == "IN" and merchant != "amazon_us") or (merchant == "amazon_in")
        effective_merchant = merchant
        if merchant in ("amazon", "amazon_in", "amazon_us"):
            effective_merchant = "amazon_in" if is_india_dest else "amazon_us"

        # Bot and crawler filtering: prevent social preview bots (Pinterest, Bluesky, Mastodon, Telegram)
        from pinterest_analytics import is_bot_user_agent
        is_bot = is_bot_user_agent(user_agent)

        if not is_bot:
            # Record click with merchant and country code for real human visitors
            record_link_click(code, user_agent, referrer, merchant=effective_merchant, country=country)
            today_total = count_clicks_today()

            # Determine traffic source from referrer
            ref_lower = (referrer or "").lower()
            if "pinterest" in ref_lower:
                source = "Pinterest"
            elif "t.me" in ref_lower or "telegram" in ref_lower:
                source = "Telegram"
            elif "bsky" in ref_lower or "bluesky" in ref_lower:
                source = "Bluesky"
            elif "mastodon" in ref_lower:
                source = "Mastodon"
            elif ref_lower:
                source = ref_lower.split("/")[2] if "://" in ref_lower else ref_lower[:25]
            else:
                source = "Direct"

            # Notify admin via Telegram if enabled
            from config import CLICK_NOTIFICATION
            if CLICK_NOTIFICATION:
                try:
                    from telegram_bot import notify_link_clicked
                    display_merchant = "Amazon India" if effective_merchant == "amazon_in" else ("Amazon US" if effective_merchant == "amazon_us" else effective_merchant.capitalize())
                    notify_link_clicked(
                        anime_name=f"{anime_name} ({display_merchant})",
                        title=title,
                        today_count=today_total,
                        source=source,
                        country=country
                    )
                except Exception:
                    pass
        else:
            logger.debug(f"[Tracker] Filtered bot/crawler redirect for '{code}' (UA: {user_agent[:60]})")

        if merchant == "displate":
            from amazon_search import build_displate_url
            displate_url = build_displate_url(anime_name=anime_name, title=title)
            return redirect(displate_url, code=302)

        elif merchant == "wallpaper":
            if image_url:
                return redirect(image_url, code=302)
            return redirect(target_amazon_url, code=302)

        else: # Amazon (Geo-Targeted)
            if is_india_dest:
                # Guaranteed 0-error Amazon India destination in ₹ INR with Fast Delivery
                clean_anime = anime_name if anime_name and anime_name.lower() != "anime" else (title[:30] if title else "anime merchandise")
                import urllib.parse
                encoded = urllib.parse.quote_plus(f"{clean_anime} merchandise poster figure".strip())
                india_url = f"https://www.amazon.in/s?k={encoded}&tag={tag_in}&sort=review-rank"
                return redirect(india_url, code=302)
            else:
                # Amazon US / Global in $ USD
                dest = target_amazon_url or f"https://www.amazon.com/s?k=anime+merchandise+poster+figure&tag={tag_us}&sort=review-rank"
                if "/r/" in dest:
                    from database import get_tracked_target_url
                    dest = get_tracked_target_url(dest)
                if "/r/" in dest:
                    # Final safety: if still contains /r/, construct direct Amazon search URL
                    clean_anime = anime_name if anime_name and anime_name.lower() != "anime" else (title[:30] if title else "anime merchandise")
                    import urllib.parse
                    encoded = urllib.parse.quote_plus(f"{clean_anime} merchandise poster figure".strip())
                    dest = f"https://www.amazon.com/s?k={encoded}&tag={tag_us}&sort=review-rank"
                return redirect(dest, code=302)

    except Exception as e:
        from config import AMAZON_AFFILIATE_TAG_US, AMAZON_AFFILIATE_TAG
        tag = AMAZON_AFFILIATE_TAG_US or AMAZON_AFFILIATE_TAG or "animeasthet-20"
        return redirect(
            f"https://www.amazon.com/s?k=anime+merchandise+poster+figure&tag={tag}&sort=review-rank",
            code=302
        )


@app.route('/p/<code>')
def bridge_page(code):
    """
    🌉 Multi-Merchant Showcase Landing Page (Amazon + Displate + 4K Wallpaper).
    Bypasses Pinterest affiliate detection while tripling conversion opportunities.
    """
    from flask import request
    user_agent = request.headers.get('User-Agent', '')
    referrer   = request.referrer or ''

    try:
        from database import get_tracked_link_data
        link_data = get_tracked_link_data(code)
        target_amazon_url = link_data.get("target_url", "")
        anime_name        = link_data.get("anime_name", "Anime")
        title             = link_data.get("title", "Anime Merchandise")
        image_url         = link_data.get("image_url", "")
    except Exception:
        target_amazon_url = ""
        anime_name = "Anime"
        title = "Anime Merchandise"
        image_url = ""

    if not target_amazon_url:
        from config import AMAZON_AFFILIATE_TAG
        tag = AMAZON_AFFILIATE_TAG or "animeasthet-20"
        target_amazon_url = f"https://www.amazon.com/s?k=anime+merchandise+poster+figure&tag={tag}&sort=review-rank"
    else:
        # Pre-resolve / heal Amazon product link before rendering showcase buttons
        try:
            from link_healer import resolve_or_heal_link
            target_amazon_url = resolve_or_heal_link(
                code, fallback_anime=anime_name, fallback_title=title, current_url=target_amazon_url
            )
        except Exception:
            pass

    # Record bridge visit for funnel analytics
    try:
        from pinterest_analytics import record_bridge_visit, is_bot_user_agent
        country = get_visitor_country(request)
        if not is_bot_user_agent(user_agent):
            record_bridge_visit(code, anime_name, title, user_agent, referrer, country=country)
            # Debounced check if this visit crossed a viral surge threshold (max once per 5 min)
            from viral_radar import trigger_debounced_radar_check
            trigger_debounced_radar_check()
    except Exception:
        pass

    # Dynamic URLs through tracker (logs merchant click)
    from config import APP_BASE_URL
    base = (APP_BASE_URL or "").rstrip("/")
    country = get_visitor_country(request)
    is_india = (country == "IN")

    if is_india:
        amazon_cta = f"{base}/r/{code}?m=amazon_in" if base else target_amazon_url
        amazon_btn_title = "Shop on Amazon India (₹)"
        amazon_btn_sub = "Fast Delivery · COD & UPI Available"
    else:
        amazon_cta = f"{base}/r/{code}?m=amazon_us" if base else target_amazon_url
        amazon_btn_title = "Shop on Amazon ($)"
        amazon_btn_sub = "Figures, Nendoroids, Manga & Collectibles"

    from amazon_search import build_displate_url
    raw_displate = build_displate_url(anime_name=anime_name, title=title)
    displate_cta = f"{base}/r/{code}?m=displate" if base else raw_displate

    wallpaper_cta = f"{base}/r/{code}?m=wallpaper" if (base and image_url) else (image_url or target_amazon_url)

    # Optional image preview banner
    preview_img_html = ""
    if image_url:
        preview_img_html = f"""
        <div class="art-preview">
          <img src="{image_url}" alt="{title}" loading="lazy" />
        </div>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{title} | Anime Merch &amp; Art Hub</title>
  <meta name="description" content="Explore official {anime_name} merchandise, steel posters, and 4K wallpaper." />
  <meta property="og:title" content="{title}" />
  <meta property="og:description" content="Shop official {anime_name} figures, metal posters &amp; download 4K wallpaper." />
  <meta property="og:type" content="product" />
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet" />
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    :root {{
      --bg:      #0b0b12;
      --card:    #151522;
      --surface: #1e1e2f;
      --border:  rgba(255,255,255,0.08);
      --accent:  #ff3366;
      --text:    #f8f8fc;
      --sub:     #9d9db5;
      --radius:  20px;
    }}
    body {{
      font-family: 'Inter', -apple-system, sans-serif;
      background: radial-gradient(circle at 50% 0%, #1f1435 0%, var(--bg) 60%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 24px 16px 56px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: rgba(255,51,102,.12);
      border: 1px solid rgba(255,51,102,.35);
      color: #ff6699;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .08em;
      text-transform: uppercase;
      padding: 5px 14px;
      border-radius: 999px;
      margin-bottom: 18px;
    }}
    .card {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      max-width: 480px;
      width: 100%;
      overflow: hidden;
      box-shadow: 0 28px 80px rgba(0,0,0,.65);
      animation: rise .4s ease-out;
    }}
    @keyframes rise {{
      from {{ opacity:0; transform: translateY(20px); }}
      to   {{ opacity:1; transform: translateY(0);    }}
    }}
    .anime-tag {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: linear-gradient(90deg, #ff3366, #ff6b4a);
      color: #fff;
      font-size: 11px;
      font-weight: 800;
      letter-spacing: .08em;
      text-transform: uppercase;
      padding: 8px 20px;
    }}
    .art-preview {{
      width: 100%;
      max-height: 280px;
      overflow: hidden;
      background: #000;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .art-preview img {{
      width: 100%;
      height: 100%;
      object-fit: cover;
      display: block;
      transition: transform .3s ease;
    }}
    .art-preview:hover img {{
      transform: scale(1.03);
    }}
    .card-body {{ padding: 22px 20px 28px; }}
    .pin-title {{
      font-size: 1.35rem;
      font-weight: 800;
      line-height: 1.3;
      margin-bottom: 8px;
      background: linear-gradient(135deg, #fff 50%, #d8ccff);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .pin-desc {{
      font-size: .86rem;
      color: var(--sub);
      line-height: 1.55;
      margin-bottom: 22px;
    }}
    .options-grid {{
      display: flex;
      flex-direction: column;
      gap: 12px;
    }}
    .btn-option {{
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 14px 16px;
      border-radius: 14px;
      text-decoration: none;
      transition: transform .18s, box-shadow .18s, filter .18s;
      color: #fff;
      position: relative;
    }}
    .btn-option:hover {{
      transform: translateY(-2px);
      filter: brightness(1.08);
    }}
    .btn-option .icon {{
      font-size: 26px;
      line-height: 1;
    }}
    .btn-option .btn-text {{
      display: flex;
      flex-direction: column;
      text-align: left;
    }}
    .btn-option .btn-title {{
      font-weight: 800;
      font-size: 0.98rem;
      letter-spacing: 0.01em;
    }}
    .btn-option .btn-sub {{
      font-size: 0.75rem;
      opacity: 0.85;
      margin-top: 2px;
    }}
    /* Merchant 1: Amazon */
    .btn-amazon {{
      background: linear-gradient(135deg, #f59e0b, #ea580c);
      box-shadow: 0 8px 24px rgba(234,88,12,.3);
    }}
    /* Merchant 2: Displate */
    .btn-displate {{
      background: linear-gradient(135deg, #06b6d4, #4f46e5);
      box-shadow: 0 8px 24px rgba(79,70,229,.3);
    }}
    /* Option 3: Wallpaper */
    .btn-wallpaper {{
      background: linear-gradient(135deg, #10b981, #059669);
      box-shadow: 0 8px 24px rgba(16,185,129,.25);
    }}
    .disclosure {{
      margin-top: 18px;
      font-size: .72rem;
      color: #707088;
      text-align: center;
      line-height: 1.45;
    }}
    footer {{
      margin-top: 28px;
      font-size: .75rem;
      color: #55556a;
      text-align: center;
    }}
  </style>
</head>
<body>
  <div class="badge">✨ Verified Anime Merch &amp; Art</div>
  <div class="card">
    <div class="anime-tag">
      <span>🎌 {anime_name}</span>
      <span>4K UHD</span>
    </div>
    {preview_img_html}
    <div class="card-body">
      <h1 class="pin-title">{title}</h1>
      <p class="pin-desc">
        Curated merchandise and artwork for {anime_name} fans. Choose an option below:
      </p>
      
      <div class="options-grid">
        <!-- Option 1: Amazon Merch -->
        <a class="btn-option btn-amazon" href="{amazon_cta}" rel="sponsored noopener" target="_blank">
          <span class="icon">🛒</span>
          <span class="btn-text">
            <span class="btn-title">{amazon_btn_title}</span>
            <span class="btn-sub">{amazon_btn_sub}</span>
          </span>
        </a>

        <!-- Option 2: Displate Metal Poster -->
        <a class="btn-option btn-displate" href="{displate_cta}" rel="sponsored noopener" target="_blank">
          <span class="icon">🖼️</span>
          <span class="btn-text">
            <span class="btn-title">Metal Wall Poster (Displate)</span>
            <span class="btn-sub">20-Year Steel Canvas · Magnetic Wall Mount</span>
          </span>
        </a>

        <!-- Option 3: Free 4K Wallpaper -->
        <a class="btn-option btn-wallpaper" href="{wallpaper_cta}" target="_blank">
          <span class="icon">📱</span>
          <span class="btn-text">
            <span class="btn-title">Download 4K Wallpaper (Free)</span>
            <span class="btn-sub">Ultra-HD Phone &amp; Desktop Background</span>
          </span>
        </a>
      </div>

      <p class="disclosure">
        #ad — As an Amazon Associate &amp; affiliate partner, we may earn commissions from qualifying purchases. Artwork curated for anime fans worldwide.
      </p>
    </div>
  </div>
  <footer>Animanoizing · Anime Art &amp; Merch Community</footer>
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
    # Falls back to APP_BASE_URL/ping so outbound-to-inbound traffic traverses Render's external router
    external_url = os.environ.get('EXTERNAL_PING_URL', '').strip()
    if not external_url:
        app_base = os.environ.get('APP_BASE_URL', '').strip().rstrip('/')
        if app_base.startswith(('http://', 'https://')) and 'localhost' not in app_base:
            external_url = f"{app_base}/ping"
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
