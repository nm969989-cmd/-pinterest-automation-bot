# Bug Fix Plan

Source: four-part code review plus the graphify dependency graph
(`graphify-out/graph.json`, 1511 nodes, 4077 edges, built with
`graphify extract . --code-only`).

Before each phase, check blast radius with:
`graphify affected "<function>" --depth 2`
After each phase, rebuild with `graphify update .` and run `python -m pytest tests`.

## Graph findings that shape the order

| Node | Edges | Consequence |
|---|---|---|
| `_get_conn()` (database.py) | 167 | Every DB function depends on it. Change it last, in its own phase. |
| `_is_admin()` | 77 | Used by every `cmd_*` handler. Fixing it fixes auth for all commands at once. |
| `handle_approval_callback()` | 30 | Only reached via `_run` / `_async_polling` / `start_bot`, so adding an admin check is isolated. |
| `set_metadata()` | 38 | Shared by breakers, digest, scheduler, DeviantArt tokens. Whitelist the sync filter, not this function. |
| `notify_admin()` | 27 | Called by uploaders, scheduler and reports. Only change its return value or escaping inside it, never its signature. |
| `resolve_to_direct_link()` | 8 | Reached from `enqueue_pin`, `get_next_queued_pin`, `pop_next_pin_for_immediate_post` and both uploaders, so one import fix repairs the whole upload path. |
| `get_next_queued_pin()` | 6 | Only `scheduler._worker_loop` and `main` use it, so the poison-pill fix is contained. |

## Phase 0: Safety (before editing)
- [ ] `git status` and commit or stash current work. Create a branch.
- [ ] Back up `pinterest_bot.db` and `bot_state.db`.
- [ ] Note: `.env` exists in the project root. graphify skipped it as sensitive. Confirm it is in `.gitignore` and not tracked (`git ls-files .env`).

## Phase 1: Secrets (no code risk, highest value)
1. **Rotate** every key that has a hardcoded default: DeviantArt client secret, access token and refresh token, ImageKit private and public keys, ImgBB, Freeimage and ImgPile keys.
2. Remove defaults in `config.py` (lines ~98-129), `imagekit_uploader.py`, `imgbb_uploader.py` and `get_deviantart_token.py`. Use `os.getenv("X", "")`. Log a warning at startup when one is missing.
3. Set `DEVIANTART_ENABLED` and similar flags to default false unless the key is present.
4. Add the missing variables to `render.yaml` (`sync: false`).
5. Add a pre-commit secret scan (gitleaks). Consider `git filter-repo` to purge history.
6. `.gitignore`: add `.pytest_cache/`, `graphify-out/`, `.agents/`, `*.db`.
- Verify: `grep -rn "getenv(.*,\s*\"[A-Za-z0-9]{16,}" .` returns nothing.

## Phase 2: Telegram auth (graph: single choke point)
1. `_is_admin`: return `False` when no admin is configured.
2. `cmd_start`: do not overwrite `admin_chat_id`. Only echo the chat ID unless an admin is already set. Persist the admin ID through an env var or `set_metadata`.
3. `handle_approval_callback`: add `if not _is_admin(update): return` at the top.
4. Token redaction: add a `logging.Filter` that strips `bot\d+:[\w-]+` and apply it in `logger.get_logger`. Wrap the `requests` exception logging at L5084, L5119 and L5396.
5. `cmd_set_wallhaven_key`: delete the user's message after reading.
6. Tests (new `tests/test_telegram_auth.py`): non-admin `/start`, non-admin callback, unconfigured admin.

## Phase 3: Crashers (small, local edits)
| Bug | File | Fix |
|---|---|---|
| `logger` undefined | keep_alive.py:158 | `from logger import get_logger; logger = get_logger(__name__)` |
| `APP_BASE_URL` NameError | amazon_search.py:499,510 | Move the import to module level |
| `org_label` unbound | scheduler.py:352 | Move the `logger.info` inside `if added:` |
| `threading` shadowed | scheduler.py:621 | Remove the inner `import threading` |
| Scheduler thread dies | scheduler.py:596 | Wrap the loop body in try/except. Log and `sleep`. |
| `health_check.py` pops a real pin | health_check.py:79 | Use a peek (read-only) call and add an `if __name__` guard |
| `check_db.py` runs on import | check_db.py | Add an `if __name__` guard |

## Phase 4: Queue integrity
1. Poison pill (scheduler.py:909). After `increment_retry_count`, if the count is >= max, call the dead-letter function. Skip the pin and continue to the next one.
2. Double count (scheduler.py:981). Call `mark_file_uploaded` once, with the actual filename.
3. `crash_protection.cleanup_old_files`: skip files referenced by `pin_queue` and raise the age limit to 48h or more.
4. `os._exit(0)` in the memory restart: exit non-zero so `run_local` restarts the process.
5. Persist `_slots_fired_today` in `bot_metadata`.
6. Move the digest, healer, A/B, ghost-reviver and report jobs to a separate thread so posting is never late.
- Verify with tests: missing file leads to dead letter, and the next pin is returned.

## Phase 5: Data and analytics correctness
1. `pinterest_analytics.py:223`: use `COUNT(DISTINCT bv.id)`, or aggregate in subqueries.
2. `pinterest_analytics.py:84`: remove `"pinterest"`, `"preview"` and `"render"` from the bot UA list. Match `bot`/`crawler` on word boundaries.
3. `jsonbin_sync.py`:
   - whitelist the `bot_metadata` keys that sync, and exclude `*token*` and `*secret*`
   - add ImgBB, ImageKit and ImgPile to the payload and restore
   - refuse to PUT when the boot restore failed
   - re-check the size after tier 2
4. `crosspost_dispatcher.py:574`: use `executor.shutdown(wait=False, cancel_futures=True)`. Replace the `str(err)` matching (`"103"`) with status codes.
5. `daily_digest.py` and `weekly_report.py`: set the "sent" flag only if `notify_admin` returns True. Escape Markdown, or switch to HTML mode.
6. `/weekly`: remove the duplicate send.

## Phase 6: Pin quality and monetisation
1. Description builder (`pinterest_uploader.py:445,716`, `ai_caption.py`, `seo_matrix.py`, `ghost_pin_reviver.py`): reserve space for the disclosure (`#ad`), put it first, and truncate the body instead of the tags.
2. Ghost check (`pinterest_uploader.py:482`): return and use the real pin ID. Treat a login redirect as "unknown", not "ghost".
3. Marketplace: pick one (amazon.in or amazon.com) in `_build_deep_link` and `pa_api.py`. Remove the made-up `_SAFE_FALLBACK_ASINS`.
4. `link_healer.py`: mark a link dead only after 2 or more HTTP failures, not on `ConnectionError`. Wrap `repair_dead_link` calls in try/except.
5. `board_router.py` and `hashtag_optimizer.py`: word-boundary matching only, longest match wins. Reject an empty name. Remove the duplicate `black clover` key.
6. `viral_radar.py`: fail when `APP_BASE_URL` is unset instead of using the placeholder domain.
7. Make-webhook retries: add an idempotency key.

## Phase 7: Web endpoints (keep_alive.py)
1. Escape `title`, `anime_name` and `image_url` (`markupsafe.escape`).
2. Rate-limit `/r/*` and validate the code format. Do not create rows for unknown codes.
3. Token-gate `/verify_public`. `/health` must not return `str(e)`.
4. `create_tracked_link`: use `INSERT OR IGNORE` plus a longer code, so collisions do not overwrite.

## Phase 8: Deploy
- Run with `gunicorn` (already in `requirements.txt`). Add a Render persistent disk. Add `google-genai` and pin versions. Use `runtime: python`.
- Configure logging once on the root logger.

## Phase 9: Last, with its own test pass
- `database._get_conn` (167 edges): replace the global lock with WAL plus a short write lock. Run the full suite after this change.
- `image_processor`: handle `LA` and `I;16` modes, add EXIF transpose.

## Decision for you (ToS / legal risk, not just bugs)
Gate behind default-off flags, or remove: `watermark_inpainter.py`, `human_simulator.py`, `ghost_pin_reviver.py` (duplicate reposts), `backlink_waterfall.py` stealth mode, and the A/B tester posting identical images.

## Cleanup (any time)
Dead code (see review), `datetime.utcnow()` replaced with `datetime.now(timezone.utc)`, a shared `BaseUploader` or registry for the crosspost dispatcher, and tests for digest, weekly report, analytics and the listener.
