# Handoff Report: Survey of Requirements R1 and R2

- **Agent**: `explorer_survey_1`
- **Working Directory**: `d:\pinterst automation\.agents\teamwork\explorer_survey_1\`
- **Date**: 2026-10-29T16:16:00Z
- **Target Audience**: Planner / Lead Orchestrator / Implementers

---

## 1. Observation

### R1. DeviantArt Token Persistence Across Render Restarts

#### 1.1 OAuth Implementation & Token Refresh
- **File**: `deviantart_uploader.py`
  - **Lines 30-68 (`_get_access_token`)**:
    ```python
    33: def _get_access_token(force_refresh: bool = False) -> str:
    ...
    49:     # 1. Try reading latest persisted tokens from SQLite bot_metadata
    50:     db_access = ""
    51:     db_refresh = ""
    52:     try:
    53:         from database import get_metadata
    54:         db_access = get_metadata("deviantart_access_token", "").strip()
    55:         db_refresh = get_metadata("deviantart_refresh_token", "").strip()
    56:     except Exception as e:
    57:         logger.debug(f"[DeviantArt] Metadata lookup failed: {e}")
    58: 
    59:     token = "" if force_refresh else (db_access or DEVIANTART_ACCESS_TOKEN or os.getenv("DEVIANTART_ACCESS_TOKEN", "").strip())
    60:     refresh_tok = db_refresh or DEVIANTART_REFRESH_TOKEN or os.getenv("DEVIANTART_REFRESH_TOKEN", "").strip()
    61: 
    62:     if not token and refresh_tok and DEVIANTART_CLIENT_ID and DEVIANTART_CLIENT_SECRET:
    63:         token = _refresh_token(DEVIANTART_CLIENT_ID, DEVIANTART_CLIENT_SECRET, refresh_tok)
    ```
    - `DEVIANTART_ACCESS_TOKEN` is loaded from `config.py:99`, which contains a hardcoded fallback string `'9b67b0c368ad153ce4d4551678812f8357237fe34c030a7876'`.
    - If `token` is non-empty (which it always is unless `force_refresh=True` is explicitly passed), line 62 `if not token` evaluates to `False`, so `_refresh_token()` is **never called on startup** or on initial calls.
  - **Lines 70-128 (`_refresh_token`)**:
    ```python
    70: def _refresh_token(client_id: str, client_secret: str, refresh_token: str = "") -> str:
    ...
    108:             if r.status_code == 200:
    109:                 resp = r.json()
    110:                 new_acc = resp.get("access_token", "")
    111:                 new_ref = resp.get("refresh_token", "")
    112:                 if new_acc:
    113:                     _cached_access_token = new_acc
    114:                     try:
    115:                         from database import set_metadata
    116:                         set_metadata("deviantart_access_token", new_acc)
    117:                         if new_ref:
    118:                             set_metadata("deviantart_refresh_token", new_ref)
    119:                     except Exception as db_err:
    120:                         logger.warning(f"[DeviantArt] Failed to persist refreshed tokens in DB: {db_err}")
    121:                     logger.info("[DeviantArt] Successfully refreshed OAuth2 access token.")
    122:                     return new_acc
    ```
    - When tokens are rotated, `set_metadata` stores them **only** in local SQLite `bot_metadata`.
    - **Crucial Observation**: There is **no call** to `trigger_cloud_sync_now()` or `save_cloud_state()` anywhere in `_refresh_token()`.
  - **Lines 230-238 & 263-269 (`post_to_deviantart`)**:
    - Catches HTTP 401 on Sta.sh upload and publish, calls `_refresh_token()`, retries once.
  - **Lines 292-317 (`verify_deviantart_token`)**:
    - Validates token against `https://www.deviantart.com/api/v1/oauth2/user/whoami`.
    - On 401, calls `_get_access_token(force_refresh=True)` which triggers `_refresh_token()`.

#### 1.2 JSONBin Synchronization Mechanism
- **File**: `jsonbin_sync.py`
  - **Lines 22-26**: `_SYNC_INTERVAL = 30 * 60` (syncs every 30 minutes).
  - **Lines 74-120 (`build_state_payload`)**:
    - Dumps `bot_metadata`:
      ```python
      115:         metadata = [
      116:             {"key": r[0], "value": r[1]}
      117:             for r in conn.execute(
      118:                 "SELECT key, value FROM bot_metadata"
      119:             ).fetchall()
      120:         ]
      ```
    - Dumps `arena_posts`, `bluesky_posts`, `raindrop_posts`, `mastodon_posts`, `pixelfed_posts`, `freeimage_posts`, `imghippo_posts`, `wallhaven_posts`.
    - **Crucial Observation**: `deviantart_posts` is **completely absent** from `build_state_payload()` and `restore_db_from_cloud()`.
  - **Lines 376-386 (`trigger_cloud_sync_now`)**:
    - Provides debounced immediate sync:
      ```python
      def trigger_cloud_sync_now(min_interval_sec: int = 60) -> bool:
      ```
    - Used in `link_healer.py:248`, but **never called** in `deviantart_uploader.py`.
  - **Lines 391-451 (`restore_db_from_cloud`)**:
    - Restores `bot_metadata`:
      ```python
      446:             metadata = state.get("bot_metadata", [])
      447:             if metadata:
      448:                 conn.executemany(
      449:                     "INSERT OR REPLACE INTO bot_metadata (key, value) VALUES (?, ?)",
      450:                     [(m.get("key",""), m.get("value","")) for m in metadata if m.get("key")]
      451:                 )
      ```

#### 1.3 Boot Sequence & Render Environment
- **File**: `main.py`
  - **Lines 266-308 (`main`)**:
    ```python
    270:     init_crash_protection()
    273:     restore_db_from_cloud()
    276:     keep_alive()
    281:     start_bot(...)
    293:     scheduler.start()
    303:     start_sync_thread()
    307:     start_listener()
    ```
    - **Crucial Observation**: `restore_db_from_cloud()` runs, but there is **no startup token refresh or validation call** for DeviantArt.
    - DeviantArt access tokens expire in 3600 seconds (1 hour). After 1 hour, the restored access token is expired, but the bot continues running without refreshing it until a post fails or `/crosspost` / `/doctor` is invoked.
- **File**: `render.yaml`
  - **Lines 1-112**:
    - Defines a free-tier web service on Render without any persistent disk mount.
    - Container filesystem is ephemeral: all local files (including `bot_state.db`) are wiped on spin-down, deployment, or restart.
    - `DEVIANTART_*` environment variables are **not defined in `render.yaml`**.
- **File**: `get_deviantart_token.py`
  - **Lines 94-100**: PKCE generator stores tokens only in SQLite `bot_metadata`. Does not trigger JSONBin sync.

---

### R2. Pixelfed Status Handling: Distinguishing Disabled vs 401 Unauthenticated

#### 2.1 Configuration Conflation
- **File**: `config.py`
  - **Lines 105-107**:
    ```python
    105: PIXELFED_INSTANCE_URL  = os.getenv('PIXELFED_INSTANCE_URL', 'https://pixelfed.social').strip().rstrip('/')
    106: PIXELFED_ACCESS_TOKEN  = os.getenv('PIXELFED_ACCESS_TOKEN', '').strip()
    107: PIXELFED_ENABLED       = os.getenv('PIXELFED_ENABLED', 'false').lower() in ('true', '1', 'yes') and bool(PIXELFED_ACCESS_TOKEN)
    ```
    - **Crucial Observation**: `PIXELFED_ENABLED` incorporates `and bool(PIXELFED_ACCESS_TOKEN)`.
    - If `PIXELFED_ENABLED=true` in `.env` or Render, but `PIXELFED_ACCESS_TOKEN` is empty or missing, `PIXELFED_ENABLED` evaluates to `False`.
    - As a direct consequence, the rest of the codebase considers Pixelfed "Disabled" rather than "Enabled but Unauthenticated / Token Missing".

#### 2.2 Token Verification Function
- **File**: `pixelfed_uploader.py`
  - **Lines 46-71 (`verify_pixelfed_token`)**:
    ```python
    46: def verify_pixelfed_token() -> bool:
    47:     """Checks if the PIXELFED_ACCESS_TOKEN is valid by verifying credentials."""
    48:     from config import PIXELFED_ACCESS_TOKEN
    49:     if not PIXELFED_ACCESS_TOKEN:
    50:         return False
    51:     base_url = _get_instance_url()
    52:     try:
    53:         res = requests.get(
    54:             f"{base_url}/api/v1/accounts/verify_credentials",
    55:             headers=_get_headers(),
    56:             timeout=10,
    57:         )
    58:         if res.status_code == 200 and "id" in res.json():
    59:             data = res.json()
    60:             logger.info(f"[Pixelfed] Token valid — logged in as @{data.get('username', '?')}")
    61:             return True
    62:         elif res.status_code == 401:
    63:             logger.warning("[Pixelfed] Token invalid (HTTP 401 Unauthorized).")
    64:             return False
    65:         else:
    66:             logger.warning(f"[Pixelfed] Token verification returned HTTP {res.status_code}")
    67:             return res.status_code != 401
    68:     except Exception as e:
    69:         logger.warning(f"[Pixelfed] Token check failed: {e}")
    70:         return False
    ```
    - Returns a raw boolean `False` for both missing token, 401 Unauthorized, and network exceptions.
    - In line 67, `return res.status_code != 401` inadvertently evaluates to `True` for HTTP 500 or 503 error responses.
    - No mechanism exists for callers to discern between `disabled`, `401 unauthenticated`, and other states.

#### 2.3 Status Reporting in Telegram Commands
- **File**: `telegram_bot.py`
  - **Lines 3987-4004 & 4081-4084 (`cmd_crosspost` / `/crosspost`, `/platforms`)**:
    ```python
    3987:         # Pixelfed status
    3988:         pixelfed_status = "DISABLED (PIXELFED_ENABLED=false)"
    3989:         pixelfed_posts_str = ""
    3990:         pixelfed_link = PIXELFED_INSTANCE_URL
    3991:         if PIXELFED_ENABLED:
    3992:             from pixelfed_uploader import verify_pixelfed_token, get_pixelfed_profile_info
    3993:             pix_token_ok = verify_pixelfed_token()
    3994:             if pix_token_ok:
    3995:                 pp = get_pixelfed_profile_info()
    3996:                 cnt = pp.get("statuses_count", 0) if pp else "?"
    3997:                 pixelfed_status = "🟢 ACTIVE"
    3998:                 pixelfed_posts_str = f" ({cnt} posts)"
    3999:                 if pp and pp.get("url"):
    4000:                     pixelfed_link = pp["url"]
    4001:             else:
    4002:                 pixelfed_status = "🔴 AUTH ERROR (Check token)"
    4003:         pixelfed_st = get_pixelfed_stats(today_str)
    ...
    4081:             f"📷 Pixelfed:\n"
    4082:             f"  • Status: {pixelfed_status}{pixelfed_posts_str}\n"
    4083:             f"  • Posts: {pixelfed_st['today']} today  |  {pixelfed_st['total']} all-time\n"
    4084:             f"  • Link: {pixelfed_link}\n\n"
    ```
    - **Crucial Observation**: Lines 4081-4084 are unconditionally formatted into the final `msg` string.
    - When `PIXELFED_ENABLED` is `false`, `/crosspost` explicitly displays:
      ```
      📷 Pixelfed:
        • Status: DISABLED (PIXELFED_ENABLED=false)
        • Posts: 0 today  |  0 all-time
        • Link: https://pixelfed.social
      ```
    - Requirement R2 mandates: "hide Pixelfed status reporting when the platform is disabled."
  - **Lines 4424-4455 (`cmd_pixelfed` / `/pixelfed`)**:
    ```python
    4424: async def cmd_pixelfed(update: "Update", context: "ContextTypes.DEFAULT_TYPE"):
    ...
    4430:         enabled_str = "ENABLED" if PIXELFED_ENABLED else "DISABLED (set PIXELFED_ENABLED=true)"
    4431:         valid = verify_pixelfed_token()
    4432:         info = get_pixelfed_profile_info() if valid else None
    4433:         if info:
    ...
    4449:         else:
    4450:             msg = "📷 Pixelfed: 🔴 Token invalid or not configured. Check PIXELFED_ACCESS_TOKEN in .env"
    ```
    - If `PIXELFED_ENABLED` is `false`, running `/pixelfed` executes `verify_pixelfed_token()`, fails, and reports `"🔴 Token invalid or not configured"`. It fails to report that Pixelfed is currently disabled.

#### 2.4 Status Reporting in System Doctor
- **File**: `doctor.py`
  - **Lines 191-213 (`run_health_check`)**:
    ```python
    191:     pixelfed_status = "⚪ Disabled"
    192:     try:
    193:         if getattr(config, "PIXELFED_ENABLED", False):
    ...
    198:             elif config.PIXELFED_ACCESS_TOKEN:
    ...
    206:                     pixelfed_status = "🔴 Token Invalid"
    207:                     warnings.append("Pixelfed token is invalid — check PIXELFED_ACCESS_TOKEN in .env")
    208:             else:
    209:                 pixelfed_status = "🔴 Token Missing"
    210:                 warnings.append("PIXELFED_ENABLED=true but PIXELFED_ACCESS_TOKEN is missing in .env")
    ```
    - Lines 208-210 (`Token Missing`) are unreachable because of `config.py:107`'s `and bool(PIXELFED_ACCESS_TOKEN)`.
  - **Lines 395-396 (`format_health_report`)**:
    ```python
    395:     if getattr(config, "PIXELFED_ENABLED", False):
    396:         integration_items.append(f"  • Pixelfed           : {diag.get('pixelfed_status', 'N/A')}")
    ```
    - In `doctor.py`, `integration_items` already omits Pixelfed when `PIXELFED_ENABLED` is `false`.

---

## 2. Logic Chain

### Logic Chain for R1 (DeviantArt Token Persistence)
1. **Observation 1.1**: DeviantArt OAuth2 utilizes refresh token rotation (`new_acc, new_ref = resp.get("access_token"), resp.get("refresh_token")`). When a refresh token is used once, DeviantArt invalidates it and issues a replacement.
2. **Observation 1.1**: When `_refresh_token()` succeeds, it writes rotated tokens only to SQLite `bot_metadata` (`database.set_metadata`).
3. **Observation 1.2 & 1.3**: Render runs on an ephemeral container filesystem (`render.yaml`). SQLite is stored locally (`bot_state.db`).
4. **Observation 1.2**: JSONBin sync runs on a 30-minute periodic timer (`_SYNC_INTERVAL = 30 * 60`).
5. **Deduction A**: When Render restarts, spins down after 15 minutes of inactivity, or re-deploys, any token rotation that occurred within the last 30 minutes without a sync is lost.
6. **Deduction B**: On reboot, `restore_db_from_cloud()` restores the previous token state from JSONBin (or fails back to static `.env` tokens). Because the previous refresh token was already rotated and used, DeviantArt rejects it with HTTP 400 (`{"error":"invalid_request","error_description":"Refresh token is invalid or expired"}`). Authentication is permanently bricked until a human intervenes.
7. **Observation 1.3**: In `main.py`, `main()` starts up without any call to verify or refresh the DeviantArt token.
8. **Deduction C**: Since access tokens expire in 1 hour (3600s), containers restarted after 1 hour hold an expired access token. In `deviantart_uploader.py:59`, `token = db_access or DEVIANTART_ACCESS_TOKEN` is non-empty due to hardcoded defaults in `config.py:99`, preventing `_refresh_token()` from running proactively on boot.
9. **Conclusion for R1**: To preserve authentication state across restarts:
   - (a) `_refresh_token()` must immediately trigger an out-of-band JSONBin synchronization (`trigger_cloud_sync_now(min_interval_sec=0)` or `save_cloud_state()`).
   - (b) A startup token refresh function (e.g. `refresh_deviantart_startup_token()` or calling `verify_deviantart_token()`) must run during `main()` right after `restore_db_from_cloud()`.
   - (c) `deviantart_posts` should also be added to `jsonbin_sync.py` to prevent duplicate deviation postings after restarts.

### Logic Chain for R2 (Pixelfed Status Handling)
1. **Observation 2.1**: In `config.py:107`, `PIXELFED_ENABLED` is tied to `bool(PIXELFED_ACCESS_TOKEN)`.
2. **Deduction A**: If an operator configures `PIXELFED_ENABLED=true` but hasn't set `PIXELFED_ACCESS_TOKEN`, `config.PIXELFED_ENABLED` evaluates to `False`. The system falsely flags Pixelfed as disabled rather than reporting missing credentials or unauthenticated state.
3. **Observation 2.2**: `verify_pixelfed_token()` returns a binary boolean, masking the distinction between disabled, 401 Unauthorized, and other network errors.
4. **Observation 2.3**: In `telegram_bot.py:4081-4084` (`cmd_crosspost`), the Pixelfed section (`📷 Pixelfed:\n • Status: DISABLED...`) is hardcoded into the output regardless of `PIXELFED_ENABLED`.
5. **Observation 2.3**: In `telegram_bot.py:4424-4455` (`cmd_pixelfed`), when `PIXELFED_ENABLED` is False, the command still tests the token and reports `"🔴 Token invalid or not configured"`.
6. **Conclusion for R2**:
   - (a) In `config.py`, separate `PIXELFED_ENABLED` from `bool(PIXELFED_ACCESS_TOKEN)`.
   - (b) In `pixelfed_uploader.py`, provide a structured status inspection (or enhance `verify_pixelfed_token`) that explicitly identifies HTTP 401 Unauthorized vs disabled vs active.
   - (c) In `telegram_bot.py:cmd_crosspost`, conditionally omit the entire `📷 Pixelfed:` section whenever `PIXELFED_ENABLED` is `false`.
   - (d) In `telegram_bot.py:cmd_pixelfed`, immediately check `if not PIXELFED_ENABLED:` and inform the user that Pixelfed is disabled rather than claiming the token is invalid.

---

## 3. Caveats

1. **Local Test Execution Restraint**:
   - Unsandboxed command execution (`pytest`) was denied by system policy during this turn. Automated test verification was therefore evaluated via static analysis of `tests/test_evolved_features.py` and `tests/test_geo_redirect.py`.
2. **Live DeviantArt OAuth Endpoint**:
   - DeviantArt token rotation cannot be executed live against the real OAuth endpoint without valid client secrets and an active authorization grant; verification must be mocked in unit tests using `unittest.mock` / `requests_mock`.
3. **Multi-Platform Consistency**:
   - While investigating R2, `cmd_crosspost` in `telegram_bot.py` was observed to also hardcode other platforms (e.g. Mastodon, Are.na). While R2 specifically mandates hiding Pixelfed when disabled, the implementer should ensure no syntax or formatting regressions occur in other platform sections.

---

## 4. Conclusion & Actionable Recommendations

### Summary Table of Findings

| Requirement | Affected File(s) | Function / Scope | Identified Root Cause | Recommended Fix |
|---|---|---|---|---|
| **R1: DeviantArt Persistence** | `deviantart_uploader.py` | `_refresh_token` (lines 114-122) | Rotated tokens saved only to SQLite; never synced to JSONBin cloud. | Call `trigger_cloud_sync_now(min_interval_sec=0)` immediately upon token rotation. |
| **R1: DeviantArt Persistence** | `deviantart_uploader.py` | `_get_access_token` (lines 59-63) | Default fallback access token prevents proactive refresh; expired tokens returned. | Add `refresh_deviantart_startup_token()` that verifies validity with `/user/whoami` and refreshes on 401. |
| **R1: DeviantArt Persistence** | `main.py` | `main` (lines 273-274) | No startup token verification or refresh step in boot sequence. | Call `refresh_deviantart_startup_token()` in `main()` immediately after `restore_db_from_cloud()`. |
| **R1: DeviantArt Persistence** | `jsonbin_sync.py` | `build_state_payload`, `restore_db_from_cloud` | `deviantart_posts` table is not backed up or restored to/from JSONBin. | Add `deviantart_posts` tracking to JSONBin payload and restoration. |
| **R2: Pixelfed Status** | `config.py` | Line 107 | `PIXELFED_ENABLED` conflated with `bool(PIXELFED_ACCESS_TOKEN)`. | Decouple: `PIXELFED_ENABLED = os.getenv('PIXELFED_ENABLED', 'false').lower() in ('true', '1', 'yes')`. |
| **R2: Pixelfed Status** | `pixelfed_uploader.py` | `verify_pixelfed_token` (lines 46-71) | Returns only `bool`; line 67 returns `True` for 500 error; no 401 distinction. | Return explicit status or create `get_pixelfed_status()` distinguishing `disabled`, `401 unauthenticated`, `active`, `error`. |
| **R2: Pixelfed Status** | `telegram_bot.py` | `cmd_crosspost` (lines 4081-4084) | Pixelfed section unconditionally printed even when disabled. | Conditionally display `📷 Pixelfed:` section only `if PIXELFED_ENABLED:`. Omit completely when disabled. |
| **R2: Pixelfed Status** | `telegram_bot.py` | `cmd_pixelfed` (lines 4424-4455) | Prints "Token invalid" even when platform is disabled. | Check `if not PIXELFED_ENABLED:` first and display disabled message. On 401, display explicit 401 error. |
| **R2: Pixelfed Status** | `doctor.py` | `run_health_check` (lines 191-213) | `Token Missing` branch unreachable due to `config.py` conflation. | Clean up with decoupled `PIXELFED_ENABLED`; display 401 vs disabled cleanly. |

---

## 5. Verification Method

### Test Plan for Implementers
1. **R1 Unit/Mock Tests**:
   - Create `tests/test_deviantart_persistence.py`:
     - **Test 1**: Mock `requests.post` on `https://www.deviantart.com/oauth2/token` to return `{"access_token": "mock_acc_123", "refresh_token": "mock_ref_456"}`.
     - Verify `set_metadata` receives both tokens and `jsonbin_sync.trigger_cloud_sync_now` is called.
     - **Test 2**: Test startup token refresh when token is expired (mock `user/whoami` returning 401). Verify token is refreshed and saved.
     - **Test 3**: Mock Render container reboot sequence: populate JSONBin cloud record with `bot_metadata` containing rotated DA tokens, run `restore_db_from_cloud()`, and assert SQLite contains the restored DA tokens.
2. **R2 Unit/Mock Tests**:
   - Create `tests/test_pixelfed_status.py`:
     - **Test 1 (`disabled`)**: With `PIXELFED_ENABLED=False`, verify `cmd_crosspost` output text does **not** contain `"📷 Pixelfed"`.
     - **Test 2 (`401 unauthenticated`)**: With `PIXELFED_ENABLED=True` and invalid token (mock `verify_credentials` returning 401), verify status explicitly states `401` / `UNAUTHENTICATED`, not `DISABLED`.
     - **Test 3 (`cmd_pixelfed`)**: With `PIXELFED_ENABLED=False`, verify `/pixelfed` outputs disabled notice, not token invalid error.
     - **Test 4 (`active`)**: With `PIXELFED_ENABLED=True` and valid token (mock `verify_credentials` returning 200 with user), verify status displays active with username.
3. **Regression Tests**:
   - Run the existing test suite:
     `pytest tests/`

### Invalidation Conditions
- If DeviantArt switches from OAuth2 refresh token rotation to long-lived static bearer tokens (unlikely per official DeviantArt API specs).
- If Pixelfed instance removes `/api/v1/accounts/verify_credentials` Mastodon API compatibility.
