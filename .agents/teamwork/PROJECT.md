# Project: Pinterest Automation Code Audit Fixes

## Architecture
Pinterest Multi-Platform Automation Bot coordinating 11 social and image hosting platforms:
- Social / Syndication Platforms: Are.na, Bluesky, Raindrop, Mastodon, Pixelfed, Wallhaven, DeviantArt
- Image Hosting Platforms: Freeimage, Imghippo, ImgBB, ImageKit
- Core Infrastructure: Crosspost Dispatcher, Backlink Waterfall, JSONBin State Synchronization, Telegram Admin Bot, Flask Keep-Alive Server, APScheduler

## Feature Inventory
| # | Feature | Description | Milestone | Source | Status |
|---|---------|-------------|-----------|--------|--------|
| 1 | DeviantArt Token Persistence | Persist rotated tokens to JSONBin immediately on refresh and verify/refresh on startup in main.py | M1 | ORIGINAL_REQUEST § R1 | DONE |
| 2 | Pixelfed Status Handling | Decouple PIXELFED_ENABLED from access token, distinguish disabled vs 401 unauthenticated, hide Pixelfed when disabled | M1 | ORIGINAL_REQUEST § R2 | DONE |
| 3 | ImgBB Upload & Maintenance | Support URL upload with base64 fallback on ephemeral filesystem, gracefully handle HTTP 400 code 100 maintenance without retries/alerts | M1 | ORIGINAL_REQUEST § R3 | DONE |
| 4 | Telegram Notification Formatting | Display only active platforms with status indicators (✅/❌), completely omit disabled and skipped platforms | M1 | ORIGINAL_REQUEST § R4 | DONE |
| 5 | Crosspost Dispatcher Docs | Align platform count to 11 across crosspost_dispatcher, backlink_waterfall, telegram_bot, .env.example, scratch/test_wallhaven | M1 | ORIGINAL_REQUEST § R5 | DONE |
| 6 | Automated Test Suite | Comprehensive unit, mock, and adversarial tests in tests/, 73/73 pytest tests pass | M1, M2 | ORIGINAL_REQUEST § Acceptance Criteria | DONE |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| 1 | Comprehensive Audit Fixes & Unit Tests | Implementation of R1, R2, R3, R4, R5 and tests/test_audit_fixes.py | Survey complete | DONE |
| 2 | Adversarial Hardening & Verification Gate | Adversarial coverage testing (39 adversarial tests), full regression pass (73 tests), and forensic audit verification | M1 | DONE |

## Interface Contracts
### DeviantArt ↔ JSONBin
- `_refresh_token(client_id, client_secret, refresh_token)` in `deviantart_uploader.py` writes rotated tokens to SQLite `bot_metadata` and immediately calls `jsonbin_sync.trigger_cloud_sync_now(min_interval_sec=0)`.
- `main.py` adds `refresh_deviantart_startup_token()` right after `restore_db_from_cloud()` to proactively refresh expired tokens on boot via `/user/whoami`.
- `jsonbin_sync.py` backs up and restores `deviantart_posts` alongside the other platform tables.

### Pixelfed ↔ Status Reporting
- `config.PIXELFED_ENABLED = os.getenv('PIXELFED_ENABLED', 'false').lower() in ('true', '1', 'yes')` (decoupled from `bool(PIXELFED_ACCESS_TOKEN)`).
- `pixelfed_uploader.get_pixelfed_status()` cleanly distinguishes 401 unauthenticated vs active vs disabled vs missing token.
- `pixelfed_uploader.verify_pixelfed_token()` evaluates `authenticated` (resolves HTTP 500 regression).
- Telegram `/crosspost` and `/pixelfed` check `PIXELFED_ENABLED` before reporting status, completely hiding disabled reporting.

### ImgBB ↔ Crosspost Dispatcher
- `post_to_imgbb(image_path, ...)` supports URL uploads with Base64 encoding fallback for ephemeral filesystems where local file paths do not exist.
- On HTTP 400 with code 100 ("Imgbb is currently down for maintenance."), `post_to_imgbb` aborts retries immediately, flags maintenance, and returns `None`.
- `crosspost_dispatcher._post_imgbb` returns `(None, profile_url)` on maintenance so it is omitted from notifications without tripping false alerts (`❌`).
- `verify_imgbb_token()` detects maintenance without falsely reporting "Token Error".

### Telegram Notification Formatter
- `format_crosspost_status_block(results: dict) -> str`: Checks `config.{PLATFORM}_ENABLED` AND `result in (True, False)`.
- Displays ONLY active platforms with `✅` (True) or `❌` (False).
- Completely omits platforms that are disabled or unexecuted (`None`), with zero `—` characters emitted.
- Used consistently in both `notify_admin_pin_posted` and `_post_now`.

### Crosspost Dispatcher & Ecosystem Documentation
- `crosspost_dispatcher.py`: Header docstring lists all 11 platforms; `dispatch_all_crossposts` documents parameters, concurrency (11 workers), 60s timeout budget, and return schema.
- `backlink_waterfall.py`: Docstring updated to 11 platforms.
- `telegram_bot.py`: Docstrings and comments updated to 11 platforms.
- `.env.example`: Removed Tumblr, added ImageKit.
- `scratch/test_wallhaven.py`: Updated assertion to 11 workers.

## Code Layout
- `deviantart_uploader.py`: Token refresh, OAuth storage, startup verification
- `jsonbin_sync.py`: State payload building and cloud restoration including DeviantArt
- `main.py`: Startup boot sequence and proactive DeviantArt token check
- `config.py`: Decoupled `PIXELFED_ENABLED` flag
- `pixelfed_uploader.py`: Credential verification distinguishing 401 unauthenticated
- `imgbb_uploader.py`: Base64 fallback, URL upload, HTTP 400 code 100 maintenance handling
- `crosspost_dispatcher.py`: ImgBB maintenance handling, docstrings for 11 platforms
- `telegram_bot.py`: Centralized `format_crosspost_status_block`, admin notifications, manual post formatting, docstrings
- `backlink_waterfall.py`: Docstrings for 11 platforms
- `doctor.py`: Health check status for Pixelfed and ImgBB maintenance
- `.env.example`: Removed Tumblr, added ImageKit
- `scratch/test_wallhaven.py`: Updated assertion to 11 workers
- `tests/test_audit_fixes.py`: Comprehensive automated unit/mock test suite (22 tests)
- `tests/test_adversarial_m1.py`: Adversarial stress tests for R1 & R2 (14 tests)
- `tests/test_adversarial_m1_2.py`: Adversarial stress tests for R3, R4 & R5 (25 tests)
