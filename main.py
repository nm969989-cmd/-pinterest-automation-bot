import os
from logger import get_logger
from keep_alive import keep_alive
from telegram_listener import start_listener, set_image_callback
from image_processor import process_image
from amazon_search import generate_amazon_link
from pinterest_uploader import upload_to_pinterest
from scheduler import scheduler
from ai_caption import generate_pin_content
from board_router import get_board_for_anime
from hashtag_optimizer import optimize_hashtags, replace_hashtags_in_description
from telegram_bot import start_bot, record_pin, send_pin_approval_request
from jsonbin_sync import restore_db_from_cloud, start_sync_thread, save_cloud_state
from crash_protection import init_crash_protection, cleanup_old_files
from keyword_seo import build_seo_title, inject_keywords_into_description
import config  # Ensure env vars are loaded and validated

# Log AI status at startup
if config.OPENROUTER_API_KEY:
    print("[config] Vision AI captions: ENABLED (OpenRouter - sees actual images!)")
else:
    print("[config] AI captions: DISABLED (regex fallback) - set OPENROUTER_API_KEY to enable")

# Log Pinterest board status at startup
if config.PINTEREST_BOARD_ID:
    print(f"[config] Pinterest board: CONFIGURED (ID: {config.PINTEREST_BOARD_ID})")
elif config.MAKE_WEBHOOK_URL:
    print("[config] Pinterest board: Using Make.com default board (PINTEREST_BOARD_ID is empty in .env)")
else:
    print("[config] Pinterest board: WARNING - No board ID or Make.com webhook configured")

logger = get_logger(__name__)

def handle_new_image(filepath, caption, channel_name, image_url=""):
    """
    Callback fired when a new image is downloaded from Telegram.
    image_url: the original Telegram CDN URL (may expire in ~1h). After
               processing, we upload to a permanent host (Cloudinary/Catbox)
               and store THAT URL in the queue instead, so the scheduler can
               always resurrect the file after a Render restart/FS wipe.
    Wrapped in try/except so one bad image never crashes the whole bot.
    """
    try:
        logger.info(f"Main handler received new image: {filepath}")

        # 1. Process Image
        processed_path = process_image(filepath, channel_name)

        # Gate 2: Compute fingerprints and check for duplicate before AI generation & hosting
        from image_dedup import get_image_fingerprints
        from database import is_image_duplicate
        img_sha, img_phash = get_image_fingerprints(processed_path)
        is_dup, dup_reason = is_image_duplicate(filepath=processed_path, sha256=img_sha, phash=img_phash)
        if is_dup:
            logger.info(f"[Main] Skipping visual duplicate image '{processed_path}': {dup_reason}")
            try:
                if os.path.exists(processed_path):
                    os.remove(processed_path)
                if filepath != processed_path and os.path.exists(filepath):
                    os.remove(filepath)
            except Exception:
                pass
            return

        # 1b. Upload to permanent host immediately so the queue stores a
        #     permanent URL, not the expiring Telegram CDN URL.
        #     If upload fails (Cloudinary/Catbox down), fall back to original
        #     Telegram CDN URL — resurrection may fail later but won't crash now.
        try:
            from image_host import upload_image_to_host
            permanent_url = upload_image_to_host(processed_path)
            if permanent_url:
                logger.info(f"[Main] Permanent image URL stored: {permanent_url[:60]}...")
                image_url = permanent_url  # overwrite expiring Telegram CDN URL
            else:
                logger.warning(
                    "[Main] Permanent host upload failed — falling back to Telegram CDN URL "
                    "(may expire before posting slot)."
                )
        except Exception as _host_err:
            logger.warning(f"[Main] Permanent host upload error (non-critical): {_host_err}")

        # 2. Generate Content (Vision AI via OpenRouter)
        anime_name, title, desc_template = generate_pin_content(
            caption,
            channel_name,
            image_path=processed_path,
            api_key=None,  # Gemini removed — OpenRouter only
            openrouter_key=config.OPENROUTER_API_KEY
        )

        # 3. Extract character name from AI or title for more precise Amazon product match
        character_name = ""
        try:
            from ai_caption import get_last_character_name
            character_name = get_last_character_name()
        except Exception:
            character_name = ""

        if not character_name and title:
            from amazon_search import clean_character_name
            if " - " in title:
                # e.g. "Tanjiro - Demon Slayer Poster" → first word before dash = character
                raw_char = title.split(" - ")[0].strip().split()[0]
                character_name = clean_character_name(raw_char, anime_name=anime_name)
            elif ":" in title:
                raw_char = title.split(":")[0].strip().split()[0]
                character_name = clean_character_name(raw_char, anime_name=anime_name)

        if character_name:
            from amazon_search import clean_character_name
            character_name = clean_character_name(character_name, anime_name=anime_name)
            if character_name:
                logger.info(f"[Main] Character extracted: '{character_name}'")


        # 4. Route to correct Pinterest board + get genre
        genre, board_id = get_board_for_anime(anime_name)

        # 4b. SEO-optimize title with Multi-Intent keywords
        try:
            from seo_matrix import build_multi_intent_title
            title = build_multi_intent_title(
                original_title=title,
                anime_name=anime_name,
                character_name=character_name,
                genre=genre,
            )
            logger.info(f"[Main] Multi-Intent SEO title: {title[:80]}")
        except Exception as _seo_err:
            try:
                title = build_seo_title(
                    original_title=title,
                    anime_name=anime_name,
                    character_name=character_name,
                    genre=genre,
                )
                logger.info(f"[Main] SEO title (legacy fallback): {title[:80]}")
            except Exception:
                logger.warning(f"[Main] SEO title build failed (non-critical): {_seo_err}")

        # 5. Check 80/20 Organic Trust Ratio
        is_organic = 0
        try:
            from database import should_next_pin_be_organic
            if getattr(config, "ORGANIC_TRUST_ENABLED", True) and should_next_pin_be_organic(getattr(config, "ORGANIC_TRUST_RATIO", 0.20)):
                is_organic = 1
                logger.info(f"[Main] 🌿 Pin designated as ORGANIC TRUST PIN (80/20 rule) for '{anime_name}'")
        except Exception as _org_check_err:
            logger.debug(f"[Main] Organic trust check error (defaulting to monetized): {_org_check_err}")

        # 5b. Generate Link (Profile/Board URL if organic, otherwise Amazon/Displate affiliate bridge)
        if is_organic:
            pin_link = getattr(config, "ORGANIC_PIN_LINK", "") or getattr(config, "PINTEREST_PROFILE_URL", "https://in.pinterest.com/muthelyrics/")
        else:
            pin_link = generate_amazon_link(anime_name, character_name=character_name, title=title, image_url=image_url)

        # 6. Build Multi-Intent SEO Description & Intent-Balanced Hashtags
        #    Prepend a natural hook in the first 150 chars connecting 4K wallpapers,
        #    Displate metal wall art, and Amazon figures for maximum search discoverability.
        try:
            from seo_matrix import build_multi_intent_description, generate_multi_intent_tags
            description = build_multi_intent_description(
                base_description=desc_template,
                anime_name=anime_name,
                character_name=character_name,
                link_url=pin_link,
            )
            color_tags = []
            try:
                from image_processor import extract_image_color_tags
                color_tags = extract_image_color_tags(processed_path)
            except Exception:
                pass
            optimized_tags = generate_multi_intent_tags(
                anime_name=anime_name,
                character_name=character_name,
                genre=genre,
                target_count=13,
                include_disclosure=(not is_organic),
                color_tags=color_tags,
            )
            description = description.rstrip() + "\n\n" + optimized_tags
            logger.info(f"[Main] Multi-Intent SEO description & tags built for '{anime_name}' (Organic={is_organic})")
        except Exception as _seo_mat_err:
            logger.warning(f"[Main] Multi-intent SEO matrix failed, using fallback: {_seo_mat_err}")
            description = desc_template.replace("##LINK_PLACEHOLDER##", pin_link)
            try:
                description = inject_keywords_into_description(
                    description=description,
                    anime_name=anime_name,
                    character_name=character_name,
                )
            except Exception:
                pass
            optimized_tags = optimize_hashtags(
                anime_name=anime_name,
                genre=genre,
                character_name=character_name,
                product_hint="anime merch",
            )
            description = replace_hashtags_in_description(description, optimized_tags)

        # Guarantee FTC & Pinterest required disclosure tags are present ONLY for monetized affiliate pins
        if not is_organic:
            if "#ad" not in description.lower() and "#affiliate" not in description.lower():
                description = description.rstrip() + "\n#ad #affiliate"
        else:
            description = description.replace("#ad", "").replace("#affiliate", "").replace("#AD", "").strip()

        # 8. Record pin for Telegram /preview and /stats
        record_pin(anime_name, title, description, pin_link, processed_path)

        # 9. Queue with priority scheduling (new images always before backlog)
        if config.MAKE_WEBHOOK_URL and not config.DRY_RUN and not config.AUTO_POST_MODE:
            logger.info("[Main] Sending pin to Telegram for approval...")
            send_pin_approval_request(processed_path, title, description, pin_link)
        else:
            ab_used = False
            if not config.DRY_RUN and getattr(config, "AB_TESTING_ENABLED", False):
                try:
                    from ab_tester import maybe_enqueue_ab_test
                    ab_used = maybe_enqueue_ab_test(
                        image_path=processed_path,
                        image_url=image_url,
                        anime_name=anime_name,
                        title_a=title,
                        description=description,
                        link=pin_link,
                        board_id=board_id,
                        sha256=img_sha,
                        phash=img_phash,
                        is_organic=is_organic,
                        genre=genre,
                    )
                except Exception as _ab_err:
                    logger.warning(f"[Main] A/B test enqueue failed (falling back): {_ab_err}")

            if not ab_used:
                safe_post_id = filepath.replace("/", "_").replace("\\", "_")
                scheduler.enqueue_new_image(
                    post_id=safe_post_id,
                    image_path=processed_path,
                    title=title,
                    description=description,
                    link=pin_link,
                    anime_name=anime_name,
                    board_id=board_id,
                    image_url=image_url,  # Store Telegram CDN URL for file resurrection after restarts
                    sha256=img_sha,
                    phash=img_phash,
                    is_organic=is_organic,
                )

        # 10. Clean up original download to save disk space (keep processed copy)
        try:
            if filepath != processed_path and os.path.exists(filepath):
                os.remove(filepath)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[Main] handle_new_image failed for {filepath}: {e}", exc_info=True)


def main():
    logger.info("Initializing Anime Pinterest Bot (Web Scraper Edition)...")

    # -1. Crash protection FIRST — catches everything from here onwards
    init_crash_protection()

    # 0. Restore persisted state from JSONBin cloud (survives Render restarts!)
    restore_db_from_cloud()

    # 0a. Immediate startup queue dedup (prune cached duplicates)
    try:
        from database import purge_already_posted_queue_items, clean_duplicate_queue_items
        _purged = purge_already_posted_queue_items() + clean_duplicate_queue_items()
        if _purged > 0:
            logger.info(f"[Main] Immediate startup dedup pruned {_purged} duplicate queue item(s).")
    except Exception as _dedup_err:
        logger.warning(f"[Main] Immediate startup dedup failed (non-critical): {_dedup_err}")

    # 0b. Proactively verify and refresh DeviantArt OAuth tokens after state restoration
    try:
        from deviantart_uploader import refresh_deviantart_startup_token
        refresh_deviantart_startup_token()
    except Exception as _da_err:
        logger.warning(f"[Main] DeviantArt startup token verification failed: {_da_err}")

    # 0c. Smart Auth Preflight: Auto-disable platforms with expired/unauthorized tokens (zero ❌ guarantee)
    try:
        from auth_preflight import run_auth_preflight
        run_auth_preflight()
    except Exception as _pf_err:
        logger.warning(f"[Main] Auth preflight failed (non-critical): {_pf_err}")

    # 1. Start Flask web server (Keep-alive for Render)
    keep_alive()
    logger.info("Keep-alive server started on port 8080.")

    # 2. Start Telegram Control Bot (in background thread)
    if config.TELEGRAM_BOT_TOKEN:
        start_bot(
            token=config.TELEGRAM_BOT_TOKEN,
            admin_chat_id=config.TELEGRAM_ADMIN_CHAT_ID,
            channels=config.TELEGRAM_CHANNELS,
            dry_run=config.DRY_RUN,
            post_delay=config.POST_DELAY_MINUTES,
            max_per_day=config.MAX_POSTS_PER_DAY,   # ← fixes the "5/15" bug
        )
    else:
        logger.info("[TG BOT] No TELEGRAM_BOT_TOKEN set, control bot disabled.")

    # 3. Start Scheduler (smart time-slot posting)
    scheduler.start()
    try:
        from crash_protection import register_thread
        from scheduler import _start_scheduler_thread
        register_thread("scheduler", _start_scheduler_thread, thread=scheduler.thread)
    except Exception as _reg_err:
        logger.warning(f"[Main] Watchdog registration failed (non-critical): {_reg_err}")
    logger.info("Upload scheduler started.")

    # 4. Start JSONBin sync thread (saves state every 30 min)
    start_sync_thread()

    # 4b. Background auto-heal & queue stocking
    def _run_startup_heal():
        try:
            import time
            import threading
            from database import get_metadata, set_metadata, get_queue_counts
            time.sleep(15)  # Wait for Flask & Telegram bot to fully start
            ran_flag = get_metadata("state_heal_completed_v1")
            if not ran_flag:
                logger.info("[Main] Starting initial automated state heal in background...")
                from state_healer import heal_state
                stats = heal_state()
                set_metadata("state_heal_completed_v1", "true")
                logger.info(f"[Main] Automated state heal complete: {stats}")
                try:
                    from telegram_bot import notify_admin
                    notify_admin(
                        f"🩺 *Automated State Heal Complete*\n"
                        f"• History fingerprints added: {stats.get('history_added', 0)}\n"
                        f"• Queue items fingerprinted: {stats.get('queue_fingerprinted', 0)}\n"
                        f"• Duplicates pruned: {stats.get('queue_purged', 0)}\n"
                        f"• Telegram IDs marked: {stats.get('marked_processed', 0)}\n"
                        f"• Cloud synced: {'✅' if stats.get('cloud_synced') else '❌'}"
                    )
                except Exception:
                    pass

            # Ensure queue has at least 5 items for tomorrow's slots
            from telegram_listener import ensure_queue_stocked
            ensure_queue_stocked(target_count=5)
        except Exception as _heal_err:
            logger.warning(f"[Main] Background startup heal failed: {_heal_err}")

    import threading
    threading.Thread(target=_run_startup_heal, daemon=True, name="StartupHealer").start()

    # 5. Set callback and start Telegram scraper (blocks forever)
    set_image_callback(handle_new_image)
    start_listener()

if __name__ == "__main__":
    # Ensure required directories exist
    for d in ['downloads', 'processed', 'logs']:
        os.makedirs(d, exist_ok=True)
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Bot stopped by user — saving state...")
        try:
            save_cloud_state()
        except Exception:
            pass
    except Exception as e:
        logger.critical(f"Bot crashed: {e}", exc_info=True)
        try:
            save_cloud_state()  # Save to JSONBin before dying
        except Exception:
            pass
        raise  # Re-raise so Render sees the crash and restarts
