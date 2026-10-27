"""
ab_tester.py — A/B Title Split Tester
======================================
Posts 2 title variants of the same image/anime to Pinterest.
After AB_DECISION_HOURS, compares bridge visit CTR and declares a winner.
The winner gets boosted to express priority in the queue.

Usage:
  From main.py: call ab_tester.maybe_enqueue_ab_test(...)
  From scheduler: call ab_tester.check_and_decide_ab_tests()
"""

import uuid
import datetime
from logger import get_logger
import config

logger = get_logger(__name__)

import re

# Minimum hours before comparing A vs B and picking winner
AB_DECISION_HOURS = int(getattr(config, "AB_DECISION_HOURS", 48))
AB_MIN_VISITS = int(getattr(config, "AB_MIN_VISITS", 3))  # skip if too few visits


def _extract_title_modifier(title: str, anime_name: str = "") -> str:
    """Extracts the intent/modifier segment from a pin title for keyword evolution."""
    if not title:
        return ""
    try:
        from seo_matrix import BASE_SEARCH_MODIFIERS
        for mod in BASE_SEARCH_MODIFIERS:
            if mod.lower() in title.lower():
                return mod
    except Exception:
        pass
    segments = [s.strip() for s in re.split(r"[|\-—🔥]", title) if s.strip()]
    for seg in segments:
        if anime_name and anime_name.lower() in seg.lower() and len(seg) < len(anime_name) + 6:
            continue
        if 5 <= len(seg) <= 40:
            return seg
    return ""


def _generate_b_title(original_title: str, anime_name: str, genre: str = "") -> str:
    """Generate an alternative title variant (B) from the original (A)."""
    try:
        from seo_matrix import build_multi_intent_title
        alt_base = f"{anime_name} Aesthetic 4K Wallpaper Art"
        return build_multi_intent_title(
            alt_base,
            anime_name=anime_name,
            character_name="",
            genre=genre or "wallpaper",
        )
    except Exception:
        # Fallback: rearrange keywords
        parts = original_title.split(" - ")
        if len(parts) >= 2:
            return f"{parts[-1].strip()} | {parts[0].strip()} — {anime_name}"
        return f"{anime_name} Art 4K | {original_title[:40]}"


def maybe_enqueue_ab_test(
    image_path: str,
    image_url: str,
    anime_name: str,
    title_a: str,
    description: str,
    link: str,
    board_id: str = "",
    sha256: str = "",
    phash: str = "",
    is_organic: int = 0,
    genre: str = "",
) -> bool:
    """
    Enqueues 2 pin variants (A and B) for A/B testing if AB_TESTING_ENABLED=true.
    Returns True if an A/B test was created, False if skipped.
    """
    try:
        if not getattr(config, "AB_TESTING_ENABLED", False):
            return False

        from database import enqueue_pin, record_ab_test, record_tracked_link

        test_id = f"ab_{uuid.uuid4().hex[:8]}"
        title_b = _generate_b_title(title_a, anime_name, genre)

        base_url = (getattr(config, "APP_BASE_URL", "") or "").rstrip("/")

        for variant, title in [("A", title_a), ("B", title_b)]:
            code = f"{test_id}_{variant.lower()}"
            bridge_url = f"{base_url}/p/{code}" if base_url else link
            post_id = f"ab_{code}_{uuid.uuid4().hex[:4]}"

            # Register tracked link
            record_tracked_link(
                code=code,
                target_url=link,
                anime_name=anime_name,
                title=title,
                image_url=image_url,
            )

            # Enqueue pin: Variant A gets priority 1 (posts first), Variant B gets priority 0 (backlog)
            priority = 1 if variant == "A" else 0
            today_str = (
                datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
            ).strftime("%Y-%m-%d")

            success = enqueue_pin(
                post_id=post_id,
                image_path=image_path,
                title=title,
                description=description,
                link=bridge_url,
                anime_name=anime_name,
                image_url=image_url,
                board_id=board_id,
                priority=priority,
                scheduled_date=today_str,
                sha256=sha256,
                phash=phash,
                is_organic=is_organic,
                allow_duplicate=(variant == "B"),
            )
            if success:
                record_ab_test(
                    test_id=test_id,
                    variant=variant,
                    post_id=post_id,
                    anime_name=anime_name,
                    title=title,
                    image_url=image_url,
                    bridge_code=code,
                )
                logger.info(f"[A/B] Enqueued variant {variant} for '{anime_name}': {title[:60]}")

        return True

    except Exception as e:
        logger.error(f"[A/B] maybe_enqueue_ab_test failed: {e}", exc_info=True)
        return False


def refresh_ab_stats():
    """Pull latest bridge visit and click counts from DB and update ab_tests table."""
    try:
        from database import get_running_ab_tests, update_ab_test_stats, _get_conn

        tests = get_running_ab_tests()
        if not tests:
            return

        with _get_conn() as conn:
            for t in tests:
                code = t["code"]
                row = conn.execute("""
                    SELECT
                        COUNT(DISTINCT bv.id),
                        COUNT(DISTINCT lc.id)
                    FROM bridge_visits bv
                    LEFT JOIN link_clicks lc ON lc.code = bv.code
                    WHERE bv.code = ?
                """, (code,)).fetchone()
                visits = row[0] or 0
                clicks = row[1] or 0
                update_ab_test_stats(code, visits, clicks)
    except Exception as e:
        logger.error(f"[A/B] refresh_ab_stats failed: {e}")


def check_and_decide_ab_tests() -> int:
    """
    Evaluates all running A/B tests older than AB_DECISION_HOURS.
    Declares winner by highest CTR (or visits if no clicks).
    Sends Telegram alert with results.
    Returns count of tests decided.
    """
    try:
        from database import declare_ab_winner, _get_conn, boost_queue_priority_by_anime

        refresh_ab_stats()
        cutoff_dt = datetime.datetime.utcnow() - datetime.timedelta(hours=AB_DECISION_HOURS)

        with _get_conn() as conn:
            mature_tests = conn.execute("""
                SELECT DISTINCT test_id FROM ab_tests
                WHERE status = 'running'
                  AND datetime(created_at) <= ?
            """, (cutoff_dt.strftime("%Y-%m-%d %H:%M:%S"),)).fetchall()

        decided = 0
        for (test_id,) in mature_tests:
            try:
                with _get_conn() as conn:
                    variants = conn.execute("""
                        SELECT variant, title, visits, clicks, anime_name FROM ab_tests
                        WHERE test_id = ? ORDER BY variant
                    """, (test_id,)).fetchall()

                if len(variants) < 2:
                    continue

                results = {
                    r[0]: {"title": r[1], "visits": r[2], "clicks": r[3], "anime": r[4]}
                    for r in variants
                }
                a, b = results.get("A", {}), results.get("B", {})

                # Skip decision if combined visits are less than AB_MIN_VISITS
                total_visits = a.get("visits", 0) + b.get("visits", 0)
                if total_visits < AB_MIN_VISITS:
                    logger.debug(f"[A/B] Test {test_id} has only {total_visits} visits (< {AB_MIN_VISITS}), waiting.")
                    continue

                a_ctr = a["clicks"] / max(1, a["visits"])
                b_ctr = b["clicks"] / max(1, b["visits"])

                if a_ctr > b_ctr:
                    winner = "A"
                elif b_ctr > a_ctr:
                    winner = "B"
                else:
                    winner = "A" if a["visits"] >= b["visits"] else "B"

                loser = "B" if winner == "A" else "A"
                declare_ab_winner(test_id, winner)
                decided += 1

                # Record in evolved_keywords for self-learning loop
                w_data, l_data = results[winner], results[loser]
                try:
                    from database import record_keyword_evolution
                    w_mod = _extract_title_modifier(w_data["title"], w_data.get("anime", ""))
                    l_mod = _extract_title_modifier(l_data["title"], l_data.get("anime", ""))
                    if w_mod:
                        record_keyword_evolution(w_mod, won=True, clicks=w_data["clicks"], visits=w_data["visits"])
                    if l_mod and l_mod.lower() != (w_mod or "").lower():
                        record_keyword_evolution(l_mod, won=False, clicks=l_data["clicks"], visits=l_data["visits"])
                except Exception as _evo_err:
                    logger.warning(f"[A/B] Keyword evolution extraction error: {_evo_err}")

                # Send Telegram notification
                w_ctr = round((w_data["clicks"] / max(1, w_data["visits"])) * 100, 1)
                l_ctr = round((l_data["clicks"] / max(1, l_data["visits"])) * 100, 1)

                msg = (
                    f"🔬 *A/B Test Result!*\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🏆 *Winner: Variant {winner}*\n"
                    f"  Title: _{w_data['title'][:70]}_\n"
                    f"  Visits: *{w_data['visits']}* | Clicks: *{w_data['clicks']}* ({w_ctr}% CTR)\n\n"
                    f"❌ *Loser: Variant {loser}*\n"
                    f"  Title: _{l_data['title'][:70]}_\n"
                    f"  Visits: *{l_data['visits']}* | Clicks: *{l_data['clicks']}* ({l_ctr}% CTR)\n\n"
                    f"✅ Winner pattern recorded in Evolver!"
                )
                try:
                    from telegram_bot import notify_admin
                    notify_admin(msg, parse_mode="Markdown")
                except Exception:
                    pass

                logger.info(f"[A/B] Test {test_id} decided: Variant {winner} won.")
            except Exception as e:
                logger.error(f"[A/B] Decision error for {test_id}: {e}")

        return decided
    except Exception as e:
        logger.error(f"[A/B] check_and_decide_ab_tests failed: {e}")
        return 0


def format_ab_leaderboard() -> str:
    """Returns a Markdown summary of recent A/B tests for the /ab command."""
    try:
        from database import _get_conn
        refresh_ab_stats()

        with _get_conn() as conn:
            rows = conn.execute("""
                SELECT test_id, variant, anime_name, title, visits, clicks, status, created_at
                FROM ab_tests ORDER BY created_at DESC LIMIT 20
            """).fetchall()

        if not rows:
            return (
                "🔬 *A/B Test Results*\n"
                "No tests run yet. Set `AB_TESTING_ENABLED=true` in `.env` to start split testing titles."
            )

        lines = ["🔬 *A/B Title Test Leaderboard*", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        seen = {}
        for r in rows:
            test_id, variant, anime, title, visits, clicks, status, created = r
            if test_id not in seen:
                seen[test_id] = []
            icon = "🏆" if status == "winner" else ("❌" if status == "loser" else "⏳")
            ctr = round(clicks / max(1, visits) * 100, 1)
            seen[test_id].append(
                f"  {icon} Variant {variant}: _{title[:50]}_ | {visits}v {clicks}c ({ctr}% CTR)"
            )

        for test_id, parts in list(seen.items())[:5]:
            lines.append(f"\n*{test_id}*")
            lines.extend(parts)

        return "\n".join(lines)
    except Exception as e:
        return f"❌ Failed to generate A/B leaderboard: {e}"
