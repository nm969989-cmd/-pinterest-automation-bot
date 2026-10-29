# Handoff Report: Survey of Requirements R3 & R4

## 1. Observation

### Requirement R3: ImgBB Upload & Maintenance Handling

#### Obs-3.1: File Path vs URL Upload Handling in `imgbb_uploader.py`
In `d:\pinterst automation\imgbb_uploader.py`, lines 105–136:
```python
105:    is_url = image_path.startswith("http://") or image_path.startswith("https://")
106:    if not is_url and not os.path.exists(image_path):
107:        logger.error(f"[ImgBB] File not found: {image_path}")
108:        return None
...
120:            if is_url:
121:                try:
122:                    dl = requests.get(image_path, headers=_HEADERS, timeout=20)
123:                    if dl.status_code == 200 and dl.content:
124:                        files = {"image": (f"{safe_name}.jpg", io.BytesIO(dl.content), "image/jpeg")}
125:                        res = requests.post(_API_URL, data=payload, files=files, headers=_HEADERS, timeout=60)
126:                    else:
127:                        payload["image"] = image_path
128:                        res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
129:                except Exception:
130:                    payload["image"] = image_path
131:                    res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
132:            else:
133:                with open(image_path, "rb") as f:
134:                    files = {"image": (os.path.basename(image_path), f, "image/jpeg")}
135:                    res = requests.post(_API_URL, data=payload, files=files, headers=_HEADERS, timeout=60)
```
- Line 106 immediately terminates if `image_path` is a local file path that does not exist on disk, logging `File not found`.
- On ephemeral filesystems (e.g., Render, Docker containers without persistent volumes, or after disk cache pruning), local file paths are not persisted. Callers often only have a public URL or database reference.
- When `is_url` is True, lines 121–125 attempt to download the file into an in-memory `io.BytesIO` buffer and post via multipart `files={"image": ...}`. If this fails, it falls back to URL string upload in `payload["image"] = image_path`.
- ImgBB API v1 specification (`https://api.imgbb.com/1/upload`) officially supports direct URL upload, multipart binary upload, and base64 string encoded upload (`image` parameter in form data).
- The current implementation lacks base64 encoding fallback when local file paths are not available on ephemeral filesystems or when multipart/direct uploads require base64 form encoding.

#### Obs-3.2: Maintenance Response Handling & Retries in `imgbb_uploader.py`
In `d:\pinterst automation\imgbb_uploader.py`, lines 137–168:
```python
137:            if res.status_code == 200:
...
147:            else:
148:                logger.warning(f"[ImgBB] Upload returned HTTP {res.status_code}: {res.text[:200]}")
149:                last_error = f"HTTP {res.status_code}"
150:                if res.status_code == 429 or "rate limit" in res.text.lower():
151:                    logger.error("[ImgBB] Rate limited (HTTP 429). Tripping circuit breaker.")
152:                    try:
153:                        from circuit_breaker import trip_breaker
154:                        trip_breaker("imgbb", "HTTP 429: Rate limited by ImgBB", cooldown_hours=6.0)
155:                    except Exception:
156:                        pass
157:                    return None
...
166:    logger.error(f"[ImgBB] All upload attempts failed. Last error: {last_error}")
167:    return None
```
- Retry schedule is defined on lines 31–32: `_MAX_RETRIES = 3`, `_RETRY_DELAYS = [0, 4, 12]`.
- When ImgBB returns HTTP 400 with `"Imgbb is currently down for maintenance."` and `code: 100`:
  1. The response is not HTTP 200 and not HTTP 429.
  2. The code logs a warning on line 148 and continues to retry for attempts 2 and 3, sleeping for 4s and 12s (16s wasted latency).
  3. All 3 attempts hit maintenance and fail.
  4. Line 166 logs `[ImgBB] All upload attempts failed. Last error: HTTP 400`.
  5. Line 167 returns `None`.

#### Obs-3.3: False Failure Alert Triggered via `crosspost_dispatcher.py`
In `d:\pinterst automation\crosspost_dispatcher.py`, lines 326–349:
```python
326: def _post_imgbb(filename, title, anime_name, link, public_img_url, original_image_url, image_path, config, pin_url=""):
...
337:         target_img = image_path if (image_path and os.path.exists(image_path)) else (public_img_url or original_image_url)
338:         if not is_imgbb_posted(filename):
339:             ibb_url = post_to_imgbb(image_path=target_img, title=title, anime_name=anime_name, affiliate_url=link)
340:             if ibb_url:
341:                 mark_imgbb_posted(filename, post_url=ibb_url, title=title, image_url=public_img_url or original_image_url)
342:                 return True, ibb_url
343:             return False, profile_url
344:         return True, profile_url
345:     except Exception as e:
346:         logger.warning(f"[Crosspost] ImgBB error: {e}")
347:         _handle_platform_error("imgbb", e)
348:         return False, ""
```
- Line 343: When `post_to_imgbb` returns `None`, `_post_imgbb` returns `(False, profile_url)`.
- In `dispatch_all_crossposts` (line 513): `results["imgbb_ok"] = False`.
- In `telegram_bot.py` (line 3564): `active_items.append(f"🖼️ ImgBB     {_platform_icon(imgbb_ok)}")`.
- Because `imgbb_ok` is `False`, `_platform_icon(False)` returns `"❌"`.
- This trips a false failure alert in Telegram notifications (`🖼️ ImgBB ❌`), notifying the admin of an upload failure even though ImgBB is merely under maintenance.

#### Obs-3.4: Token Verification Assumption in `imgbb_uploader.py` & Status Reporting
In `d:\pinterst automation\imgbb_uploader.py`, lines 43–62:
```python
43: def verify_imgbb_token() -> bool:
...
55:         data = res.json()
56:         # Code 130 means "Empty upload source" -> Key is valid and authenticated!
57:         # Code 100 means "Invalid API v1 key" -> Rejected
58:         return data.get("error", {}).get("code") == 130
```
In `d:\pinterst automation\doctor.py`, lines 271–279:
```python
271:     # 19. ImgBB Cross-Post Status
272:     imgbb_status = "⚪ Disabled"
273:     try:
274:         if getattr(config, "IMGBB_ENABLED", False):
275:             from imgbb_uploader import verify_imgbb_token
276:             ibb_ok = verify_imgbb_token()
277:             imgbb_status = "🟢 Active (@muthelyrics)" if ibb_ok else "🔴 Token Error"
```
In `d:\pinterst automation\telegram_bot.py`, lines 4038–4044:
```python
4038:         # ImgBB status
4039:         imgbb_status = "DISABLED (IMGBB_ENABLED=false)"
4040:         from config import IMGBB_ENABLED
4041:         if IMGBB_ENABLED:
4042:             from imgbb_uploader import verify_imgbb_token
4043:             imgbb_status = "🟢 ACTIVE (API Key Verified)" if verify_imgbb_token() else "🔴 KEY ERROR"
```
- Line 57 assumes code 100 uniquely means "Invalid API v1 key". When ImgBB returns HTTP 400 with code 100 and message `"Imgbb is currently down for maintenance."`, `verify_imgbb_token()` returns `False`.
- This causes `doctor.py` and `telegram_bot.py` to falsely report `"🔴 Token Error"` or `"🔴 KEY ERROR"`.

---

### Requirement R4: Telegram Notification Formatting

#### Obs-4.1: Hardcoded Platforms & Inclusion of Disabled Platforms in Manual Post Callback
In `d:\pinterst automation\telegram_bot.py`, lines 2518–2536:
```python
2518:             def _platform_icon(result):
2519:                 if result is True:   return "✅"
2520:                 if result is False:  return "❌"
2521:                 return "—"  # None = disabled or circuit-breaker skipped
2522: 
2523: 
2524:             cross_lines = ""
2525:             if any(cp_res[k] is not None for k in cp_res if k.endswith("_ok")):
2526:                 cross_lines = (
2527:                     f"\n{'─' * 26}\n"
2528:                     f"🔮 Are.na    {_platform_icon(cp_res.get('arena_ok'))}  "
2529:                     f"🦋 Bluesky  {_platform_icon(cp_res.get('bluesky_ok'))}  "
2530:                     f"💧 Raindrop   {_platform_icon(cp_res.get('raindrop_ok'))}\n"
2531:                     f"🐘 Mastodon {_platform_icon(cp_res.get('mastodon_ok'))}  "
2532:                     f"📷 Pixelfed  {_platform_icon(cp_res.get('pixelfed_ok'))}  "
2533:                     f"🖼️ Freeimage {_platform_icon(cp_res.get('freeimage_ok'))}\n"
2534:                     f"🦛 Imghippo  {_platform_icon(cp_res.get('imghippo_ok'))}  "
2535:                     f"🌌 Wallhaven {_platform_icon(cp_res.get('wallhaven_ok'))}"
2536:                 )
```
- Line 2525: If any single platform returns a non-None result, ALL 8 hardcoded platforms are printed.
- For disabled platforms (e.g., `PIXELFED_ENABLED = False`, `ARENA_ENABLED = False`, `BLUESKY_ENABLED = False`, `RAINDROP_ENABLED = False`, `MASTODON_ENABLED = False`, `IMGHIPPO_ENABLED = False`), `cp_res.get(...)` is `None`.
- `_platform_icon(None)` outputs `"—"`.
- The notification message outputs:
  ```
  🔮 Are.na    —  🦋 Bluesky  —  💧 Raindrop   —
  🐘 Mastodon —  📷 Pixelfed  —  🖼️ Freeimage ✅
  🦛 Imghippo  —  🌌 Wallhaven ✅
  ```
- Disabled platforms are explicitly displayed with `—` instead of being omitted.
- Furthermore, platforms added later (`DeviantArt`, `ImgBB`, `ImageKit`) are completely missing from lines 2528–2535.

#### Obs-4.2: Handling of `None` Results in `notify_admin_pin_posted`
In `d:\pinterst automation\telegram_bot.py`, lines 3538–3574:
```python
3538:     def _platform_icon(result):
3539:         if result is True:   return "✅"
3540:         if result is False:  return "❌"
3541:         return "—"  # None = disabled or circuit-breaker paused
3542: 
3543:     import config
3544:     active_items = []
3545:     if getattr(config, "BLUESKY_ENABLED", False):
3546:         active_items.append(f"🦋 Bluesky  {_platform_icon(bluesky_ok)}")
3547:     if getattr(config, "RAINDROP_ENABLED", False):
3548:         active_items.append(f"💧 Raindrop   {_platform_icon(raindrop_ok)}")
3549:     if getattr(config, "MASTODON_ENABLED", False):
3550:         active_items.append(f"🐘 Mastodon {_platform_icon(mastodon_ok)}")
3551:     if getattr(config, "FREEIMAGE_ENABLED", False):
3552:         active_items.append(f"🖼️ Freeimage {_platform_icon(freeimage_ok)}")
3553:     if getattr(config, "WALLHAVEN_ENABLED", False):
3554:         active_items.append(f"🌌 Wallhaven {_platform_icon(wallhaven_ok)}")
3555:     if getattr(config, "ARENA_ENABLED", False):
3556:         active_items.append(f"🔮 Are.na    {_platform_icon(arena_ok)}")
3557:     if getattr(config, "PIXELFED_ENABLED", False):
3558:         active_items.append(f"📷 Pixelfed  {_platform_icon(pixelfed_ok)}")
3559:     if getattr(config, "IMGHIPPO_ENABLED", False):
3560:         active_items.append(f"🦛 Imghippo  {_platform_icon(imghippo_ok)}")
3561:     if getattr(config, "DEVIANTART_ENABLED", False):
3562:         active_items.append(f"🎨 DeviantArt {_platform_icon(deviantart_ok)}")
3563:     if getattr(config, "IMGBB_ENABLED", False):
3564:         active_items.append(f"🖼️ ImgBB     {_platform_icon(imgbb_ok)}")
3565:     if getattr(config, "IMAGEKIT_ENABLED", False):
3566:         active_items.append(f"🖼️ ImageKit {_platform_icon(imagekit_ok)}")
```
- In `notify_admin_pin_posted`, if a platform is configured as enabled (`getattr(config, "...", False) is True`), but its posting was skipped (e.g., circuit breaker paused, or skipped during execution so `ok` is `None`), it is still appended to `active_items` with `" — "`!
- Requirement R4 explicitly mandates:
  *"display only active platforms with status indicators (✅/❌) and completely omit disabled platforms from the notification message."*
  A platform that is disabled or was not executed/active (`result is None`) must not appear with `—`; it must be completely excluded from `active_items`.

---

## 2. Logic Chain

### Requirement R3 Logic Chain
1. **From Obs-3.1**: ImgBB upload is triggered from `crosspost_dispatcher.py:339` via `post_to_imgbb(image_path=target_img, ...)`.
   - On ephemeral systems (Render free tier), files written to temporary paths can be garbage-collected or absent between tasks.
   - When a local path is not present on disk, `post_to_imgbb` currently returns `None` immediately (`imgbb_uploader.py:107`).
   - If `image_path` does not exist locally, the system can resolve the remote URL using `database.get_uploaded_file_image_url(os.path.basename(image_path))` or accept direct image URLs.
   - For image URLs, ImgBB API supports both direct URL upload (`payload["image"] = url`) and Base64-encoded string upload (`payload["image"] = base64_str`).
   - Adding in-memory Base64 encoding fallback ensures that if direct URL fetch fails, image bytes are downloaded into memory, encoded via `base64.b64encode`, and transmitted without requiring persistent local filesystem storage.

2. **From Obs-3.2 and Obs-3.3**: During ImgBB service maintenance:
   - ImgBB returns HTTP 400 with body `{"status_code": 400, "error": {"message": "Imgbb is currently down for maintenance.", "code": 100}}`.
   - Currently, `post_to_imgbb` treats this as a generic failure, executing 3 retries (16s delay) before returning `None`.
   - `_post_imgbb` in `crosspost_dispatcher.py:343` receives `None` and returns `(False, profile_url)`.
   - `results["imgbb_ok"] = False` is passed to `notify_admin_pin_posted` in `telegram_bot.py:3564`, formatting ImgBB with `❌` ("Failed").
   - This constitutes a **false failure alert**: ImgBB maintenance is an external, temporary availability state, not a persistent failure or bad configuration.
   - Therefore, `post_to_imgbb` should inspect the HTTP 400 response for `"down for maintenance"` (or code 100 with maintenance message), immediately abort retries, log an informative notice, and signal maintenance.
   - In `crosspost_dispatcher.py:339-344`, detecting this maintenance state allows returning `(None, profile_url)` so that `imgbb_ok` is `None` (omitted from notification) rather than `False` (`❌`).

3. **From Obs-3.4**: In `verify_imgbb_token()`:
   - A maintenance response (HTTP 400, code 100) must not be treated as an invalid API key (`"🔴 Token Error"` or `"🔴 KEY ERROR"`).
   - `verify_imgbb_token()` should detect maintenance and report maintenance status gracefully without raising alerts.

### Requirement R4 Logic Chain
1. **From Obs-4.1**: In `telegram_bot.py:2518–2536` (`post_now` quick-post callback):
   - The platforms are hardcoded in a static string template.
   - Any platform where `cp_res.get(k)` is `None` is formatted with `_platform_icon(None)` which returns `"—"`.
   - As a result, disabled platforms like Pixelfed, Are.na, and Bluesky appear in Telegram notifications with `"—"`.
   - This directly breaks Requirement R4.
2. **From Obs-4.2**: In `telegram_bot.py:3538–3574` (`notify_admin_pin_posted`):
   - If a platform is marked enabled in config, but its post result is `None` (disabled, skipped, or breaker paused), it displays with `"—"`.
   - To satisfy Requirement R4, a platform must be included in the Telegram message **only if**:
     a) It is enabled (`getattr(config, f"{PLATFORM}_ENABLED", False) is True`), AND
     b) It has an active execution result (`result is True` or `result is False`).
   - If `result is True`, the indicator is `✅`.
   - If `result is False`, the indicator is `❌`.
   - If disabled OR `result is None`, the platform is **completely omitted**.
3. Reconciling both locations:
   - A centralized helper `format_crosspost_notification_lines(results: dict) -> str` ensures both `notify_admin_pin_posted` and the interactive `/post_now` handler share identical, consistent behavior that completely hides disabled platforms.

---

## 3. Caveats

1. **ImgBB API Direct URL Fetch Limitations**: When sending `payload["image"] = url` to ImgBB, ImgBB's servers may occasionally be blocked by Cloudflare or source CDN hotlink protections (e.g., Pinterest CDN tokens). The Base64 encoding fallback ensures our bot fetches the bytes directly with proper User-Agent headers and delivers the image as Base64 text in the POST body.
2. **Circuit Breaker Independence**: Maintenance is temporary. It should not trip the 12-hour or 48-hour circuit breaker (`trip_breaker("imgbb", ...)`), because doing so sends an alarm message to Telegram admin (`🛡️ Platform Circuit Breaker Activated`). A silent or informative cooldown or immediate bypass is preferable.
3. **Pixelfed Cross-Requirement Interaction**: Requirement R2 addresses Pixelfed status distinction between disabled and 401 unauthenticated. Requirement R4 ensures that whenever Pixelfed (or any other platform) is disabled or not posted (`pixelfed_ok is None`), it is omitted from Telegram notifications.
4. **Scope Constraint**: As an Explorer agent, no source code files or tests were edited. All observations and proposed modifications are documented herein for the implementation phase.

---

## 4. Conclusion & Concrete Implementation Recommendations

### Target File 1: `d:\pinterst automation\imgbb_uploader.py`

#### Change 1.1: Base64 Upload & Ephemeral Filesystem Fallback
- **Location**: `imgbb_uploader.py:105–136`
- **Rationale**: Support URL upload handling with base64 encoding fallback, and resolve public URLs from SQLite if local file paths are missing on ephemeral filesystems.
- **Proposed Implementation**:
```python
import base64
import io
import os
import requests

def _load_image_bytes(image_path: str) -> tuple[bytes | None, str]:
    """Loads image bytes from local file, URL, or database lookup for ephemeral filesystems."""
    # Case 1: Existing local file
    if image_path and os.path.exists(image_path) and os.path.isfile(image_path):
        try:
            with open(image_path, "rb") as f:
                return f.read(), os.path.basename(image_path)
        except Exception as e:
            logger.warning(f"[ImgBB] Failed reading local file {image_path}: {e}")

    # Case 2: Remote URL
    target_url = image_path if (image_path and image_path.startswith(("http://", "https://"))) else ""
    
    # Case 3: Missing local file on ephemeral filesystem — fallback to database URL lookup
    if not target_url and image_path:
        try:
            from database import get_uploaded_file_image_url
            target_url = get_uploaded_file_image_url(os.path.basename(image_path))
        except Exception:
            target_url = ""

    if target_url and target_url.startswith(("http://", "https://")):
        try:
            dl = requests.get(target_url, headers=_HEADERS, timeout=25)
            if dl.status_code == 200 and dl.content:
                fn = os.path.basename(target_url.split("?")[0]) or "image.jpg"
                return dl.content, fn
        except Exception as e:
            logger.warning(f"[ImgBB] Failed downloading remote image {target_url}: {e}")

    return None, ""
```

In `post_to_imgbb`:
```python
    # URL upload handling with base64 fallback
    is_url = bool(image_path and image_path.startswith(("http://", "https://")))
    img_bytes = None
    
    # If not a URL and not on disk, check database for public URL on ephemeral filesystem
    if not is_url and not os.path.exists(image_path):
        try:
            from database import get_uploaded_file_image_url
            resolved_url = get_uploaded_file_image_url(os.path.basename(image_path))
            if resolved_url:
                image_path = resolved_url
                is_url = True
        except Exception:
            pass

    if not is_url and not os.path.exists(image_path):
        logger.error(f"[ImgBB] File not found and no remote URL available: {image_path}")
        return None
```
In upload loop:
```python
    # Step A: If URL, attempt direct URL upload first
    if is_url:
        payload["image"] = image_path
        res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=30)
        # If URL upload rejected or failed, fall back to downloading bytes and base64 encoding
        if res.status_code != 200:
            img_bytes, _ = _load_image_bytes(image_path)
            if img_bytes:
                b64_str = base64.b64encode(img_bytes).decode("utf-8")
                payload["image"] = b64_str
                res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
    else:
        # Step B: Local file or base64 fallback
        img_bytes, fn = _load_image_bytes(image_path)
        if img_bytes:
            b64_str = base64.b64encode(img_bytes).decode("utf-8")
            payload["image"] = b64_str
            res = requests.post(_API_URL, data=payload, headers=_HEADERS, timeout=45)
```

#### Change 1.2: ImgBB Maintenance Response Handling
- **Location**: `imgbb_uploader.py:137–158` and `verify_imgbb_token()`
- **Rationale**: Detect HTTP 400 with code 100 and `"down for maintenance"`, abort retries immediately, and return cleanly without false failure alerts.
- **Proposed Implementation**:
```python
# Module-level maintenance indicator
_imgbb_in_maintenance = False

def is_imgbb_maintenance() -> bool:
    return _imgbb_in_maintenance

def _check_is_maintenance(res) -> bool:
    if res is None:
        return False
    if res.status_code == 400:
        try:
            err = res.json().get("error", {})
            msg = str(err.get("message", "")).lower()
            code = err.get("code")
            if "down for maintenance" in msg or "maintenance" in msg:
                return True
            if code == 100 and ("maintenance" in msg or "down for maintenance" in res.text.lower()):
                return True
        except Exception:
            if "down for maintenance" in res.text.lower():
                return True
    return False
```
Inside `post_to_imgbb`:
```python
    if _check_is_maintenance(res):
        global _imgbb_in_maintenance
        _imgbb_in_maintenance = True
        logger.warning("[ImgBB] ImgBB API is currently down for maintenance (HTTP 400, code 100). Gracefully aborting upload without retries or alerts.")
        return None
```
Inside `verify_imgbb_token()`:
```python
    if _check_is_maintenance(res):
        logger.info("[ImgBB] ImgBB API is currently down for maintenance.")
        return False  # Or return False while setting _imgbb_in_maintenance = True
```

---

### Target File 2: `d:\pinterst automation\crosspost_dispatcher.py`

#### Change 2.1: Graceful Return on ImgBB Maintenance
- **Location**: `crosspost_dispatcher.py:334–344`
- **Rationale**: If ImgBB was skipped due to maintenance, return `(None, profile_url)` so `results["imgbb_ok"] = None`.
- **Proposed Implementation**:
```python
        if not is_imgbb_posted(filename):
            from imgbb_uploader import is_imgbb_maintenance
            ibb_url = post_to_imgbb(image_path=target_img, title=title, anime_name=anime_name, affiliate_url=link)
            if ibb_url:
                mark_imgbb_posted(filename, post_url=ibb_url, title=title, image_url=public_img_url or original_image_url)
                return True, ibb_url
            if is_imgbb_maintenance():
                logger.info("[Crosspost] ImgBB down for maintenance — omitting from failure alert.")
                return None, profile_url
            return False, profile_url
```

---

### Target File 3: `d:\pinterst automation\telegram_bot.py`

#### Change 3.1: Standardized Cross-Post Formatting Omitting Disabled Platforms
- **Location**: `telegram_bot.py:2518–2536` and `telegram_bot.py:3538–3574`
- **Rationale**: Centralize platform status line compilation. Display only active platforms with `✅` or `❌`, and completely omit disabled platforms or platforms with `None` status.
- **Proposed Implementation**:
```python
def format_crosspost_status_block(results: dict) -> str:
    """
    Formats the cross-post syndication status block for Telegram notifications.
    Displays ONLY active/enabled platforms with status indicators (✅/❌).
    Completely omits disabled platforms and unattempted/skipped platforms (status None).
    """
    import config

    platforms_meta = [
        ("bluesky",    "BLUESKY_ENABLED",    "🦋 Bluesky "),
        ("raindrop",   "RAINDROP_ENABLED",   "💧 Raindrop  "),
        ("mastodon",   "MASTODON_ENABLED",   "🐘 Mastodon"),
        ("freeimage",  "FREEIMAGE_ENABLED",  "🖼️ Freeimage"),
        ("wallhaven",  "WALLHAVEN_ENABLED",  "🌌 Wallhaven"),
        ("arena",      "ARENA_ENABLED",      "🔮 Are.na   "),
        ("pixelfed",   "PIXELFED_ENABLED",   "📷 Pixelfed "),
        ("imghippo",   "IMGHIPPO_ENABLED",   "🦛 Imghippo "),
        ("deviantart", "DEVIANTART_ENABLED", "🎨 DeviantArt"),
        ("imgbb",      "IMGBB_ENABLED",      "🖼️ ImgBB    "),
        ("imagekit",   "IMAGEKIT_ENABLED",   "🖼️ ImageKit "),
    ]

    active_items = []
    for key, config_flag, label in platforms_meta:
        # Check 1: Must be enabled in config
        if not getattr(config, config_flag, False):
            continue

        # Check 2: Must have an active execution result (True or False)
        status = results.get(f"{key}_ok")
        if status is True:
            active_items.append(f"{label} ✅")
        elif status is False:
            active_items.append(f"{label} ❌")
        # If status is None (skipped, circuit breaker, or not run): completely omit!

    if not active_items:
        return ""

    grouped_lines = []
    for i in range(0, len(active_items), 3):
        grouped_lines.append("  ".join(active_items[i:i+3]))
    return f"\n{'─' * 26}\n" + "\n".join(grouped_lines)
```

In `notify_admin_pin_posted` (lines 3543–3574):
Replace lines 3538–3574 with:
```python
    results_map = {
        "bluesky_ok": bluesky_ok,
        "raindrop_ok": raindrop_ok,
        "mastodon_ok": mastodon_ok,
        "freeimage_ok": freeimage_ok,
        "wallhaven_ok": wallhaven_ok,
        "arena_ok": arena_ok,
        "pixelfed_ok": pixelfed_ok,
        "imghippo_ok": imghippo_ok,
        "deviantart_ok": deviantart_ok,
        "imgbb_ok": imgbb_ok,
        "imagekit_ok": imagekit_ok,
    }
    cross_lines = format_crosspost_status_block(results_map)
```

In `_post_now` manual post handler (lines 2518–2536):
Replace lines 2518–2536 with:
```python
    cross_lines = format_crosspost_status_block(cp_res)
```

---

## 5. Verification Method

### Test Suite Execution
Run the full existing test suite with:
```bash
pytest tests/
```
Current baseline passes 12/12 tests in `tests\test_evolved_features.py` and `tests\test_geo_redirect.py`.

### New Unit / Mock Tests for Implementation Verification
Create `tests/test_imgbb_and_telegram_formatting.py` covering:

1. **ImgBB URL Upload & Base64 Fallback**:
   - Mock `requests.post` to verify direct URL upload passes `payload["image"] = "https://example.com/art.jpg"`.
   - Mock local file not existing on ephemeral filesystem: verify fallback resolves URL from `database.get_uploaded_file_image_url` and sends base64-encoded string in `payload["image"]`.
   - Mock URL upload failure: verify fallback downloads bytes and re-posts with base64 encoded string.

2. **ImgBB Maintenance Handling**:
   - Mock `requests.post` returning HTTP 400 with `{"status_code": 400, "error": {"message": "Imgbb is currently down for maintenance.", "code": 100}}`.
   - Verify `post_to_imgbb` aborts immediately without executing retries (call count = 1).
   - Verify `post_to_imgbb` returns `None` without raising unhandled exceptions.
   - Verify `_post_imgbb` returns `(None, ...)` rather than `(False, ...)`.
   - Verify `verify_imgbb_token()` detects maintenance without raising error alerts.

3. **Telegram Notification Formatting**:
   - When platforms are disabled in config (`PIXELFED_ENABLED=False`, `BLUESKY_ENABLED=False`), verify `format_crosspost_status_block` completely omits them from the output string.
   - When platforms are enabled and succeed (`result is True`), verify output contains `✅`.
   - When platforms are enabled and fail (`result is False`), verify output contains `❌`.
   - When a platform is enabled but `result is None` (e.g. maintenance or circuit breaker), verify it is completely omitted (no `—` characters present).
   - Verify that all 11 platforms (`Bluesky`, `Raindrop`, `Mastodon`, `Freeimage`, `Wallhaven`, `Are.na`, `Pixelfed`, `Imghippo`, `DeviantArt`, `ImgBB`, `ImageKit`) are correctly handled by the formatter.

### Invalidation Conditions
- If any Telegram notification contains a `—` dash indicator for a disabled or skipped platform, Requirement R4 is invalidated.
- If an ImgBB upload retry loop runs for 3 attempts when encountering HTTP 400 code 100 maintenance, Requirement R3 is invalidated.
- If `imgbb_ok` evaluates to `False` during ImgBB maintenance causing an `❌` alert on Telegram, Requirement R3 is invalidated.
- If ImgBB upload fails on an ephemeral filesystem when local files are not on disk despite valid URLs/bytes being accessible, Requirement R3 is invalidated.
