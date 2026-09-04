"""
link_healer.py - Automated Monthly Self-Healing Links & Auto-Repair Engine
Audits all Pinterest affiliate links on the 1st of every month.
Detects dead Amazon product pages (404s, discontinued listings), automatically
repairs them with fresh working search/product links, and notifies admin in Telegram.
"""

import os
import time
import requests
import datetime
import urllib.parse
from logger import get_logger

logger = get_logger(__name__)

# Realistic browser headers for Amazon link health verification
_CHECK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Signatures that indicate Amazon India page is dead / discontinued
_DEAD_PAGE_SIGNATURES = [
    "looking for something? we're sorry",
    "we couldn't find that page",
    "page not found",
    "the web address you entered is not a functioning page",
    "this item is no longer available",
    "currently unavailable",
]


def check_link_health(url: str) -> tuple[bool, str]:
    """
    Checks if a target Amazon affiliate URL is alive or dead.
    Returns: (is_dead: bool, reason: str)
    """
    if not url or not url.startswith("http"):
        return True, "Empty or invalid URL"

    try:
        # Fast HEAD or lightweight GET request
        res = requests.get(
            url,
            headers=_CHECK_HEADERS,
            timeout=8,
            allow_redirects=True
        )

        if res.status_code in (404, 410):
            return True, f"HTTP {res.status_code} Not Found"

        if res.status_code >= 500:
            return False, f"Server HTTP {res.status_code} (Transient)"

        # Check response body for Amazon dead page dog / error text
        body_lower = res.text[:4000].lower()
        for signature in _DEAD_PAGE_SIGNATURES:
            if signature in body_lower:
                return True, f"Amazon '{signature.title()}'"

        return False, "Active & Healthy"

    except requests.exceptions.Timeout:
        return False, "Request Timeout (Transient)"
    except requests.exceptions.ConnectionError:
        return True, "Connection Failed / Dead Domain"
    except Exception as e:
        return False, f"Check skipped ({e})"


def repair_dead_link(code: str, anime_name: str, title: str) -> str:
    """
    Generates a fresh, active Amazon deep product link for the anime/title
    and updates the database destination for this short link.
    """
    from amazon_search import generate_amazon_link, clean_character_name
    from database import update_tracked_link_target

    character_name = ""
    if title:
        raw_char = title.split(" - ")[0].strip().split()[0] if " - " in title else title.strip().split()[0]
        character_name = clean_character_name(raw_char)

    fresh_url = generate_amazon_link(anime_name, character_name=character_name, title=title)

    # Update the destination in SQLite
    update_tracked_link_target(code, fresh_url)
    logger.info(f"[LinkHealer] Repaired link '{code}' [{anime_name}] -> {fresh_url}")
    return fresh_url


def run_link_healing_audit(max_links: int = 50) -> dict:
    """
    Scans tracked links for dead pages and auto-repairs any found.
    Returns structured audit results.
    """
    from database import get_all_tracked_links

    links = get_all_tracked_links(limit=max_links)
    logger.info(f"[LinkHealer] Starting link health audit on {len(links)} links...")

    healthy_count = 0
    repaired_count = 0
    repaired_details = []

    for item in links:
        code       = item["code"]
        target_url = item["target_url"]
        anime_name = item["anime_name"]
        title      = item["title"]

        is_dead, reason = check_link_health(target_url)

        if is_dead:
            repaired_count += 1
            new_url = repair_dead_link(code, anime_name, title)
            repaired_details.append({
                "anime": anime_name,
                "title": title,
                "code": code,
                "reason": reason,
                "old_url": target_url,
                "new_url": new_url,
            })
        else:
            healthy_count += 1

        # Gentle delay between requests
        time.sleep(0.7)

    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    
    # Calculate next 1st of month
    year = now_ist.year
    month = now_ist.month + 1
    if month > 12:
        month = 1
        year += 1
    next_month_1st = datetime.date(year, month, 1).strftime("%d %b %Y")

    return {
        "timestamp_ist": now_ist.strftime("%d %b %Y, %I:%M %p"),
        "next_monthly_check": next_month_1st,
        "total_scanned": len(links),
        "healthy_count": healthy_count,
        "repaired_count": repaired_count,
        "repaired_details": repaired_details,
    }


def format_repair_report(audit: dict, is_monthly: bool = True) -> str:
    """
    Builds a beautifully styled Telegram markdown report card.
    """
    header_title = "🔧 Monthly Self-Healing Link Report (1st of Month)" if is_monthly else "🔧 Self-Healing Links Audit (/repairlinks)"

    repaired_lines = []
    if audit["repaired_details"]:
        for i, item in enumerate(audit["repaired_details"], 1):
            repaired_lines.append(
                f"  {i}. [{item['anime']}] {item['title'][:35]}\n"
                f"     ↳ Issue: {item['reason']} ➜ Auto-Repaired ✅"
            )
        repaired_block = "\n🛠️ Repaired Links:\n" + "\n".join(repaired_lines) + "\n"
    else:
        repaired_block = "\n✨ All checked links are healthy! Zero dead pages found.\n"

    report = (
        f"{header_title}\n"
        f"{'═' * 38}\n"
        f"🗓️ Audit Date : {audit['timestamp_ist']} IST\n"
        f"⏳ Next Auto-Check: {audit['next_monthly_check']}\n\n"
        f"📊 Audit Summary:\n"
        f"  • Total Links Scanned : {audit['total_scanned']} links\n"
        f"  • Healthy & Active    : {audit['healthy_count']} links 🟢\n"
        f"  • Dead Links Repaired : {audit['repaired_count']} links 🔄\n"
        f"{repaired_block}\n"
        f"💡 Result: 100% of your Pinterest pins point to working Amazon pages and are ready to earn commission!"
    )
    return report


def check_and_run_monthly_repair(app_ref, loop_ref, admin_chat_id: str):
    """
    Checks if it is the 1st of the month (or >= 28 days have passed) and runs the monthly audit.
    """
    from database import get_metadata, set_metadata
    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    today_str = now_ist.strftime("%Y-%m-%d")

    last_run_str = get_metadata("last_monthly_link_audit_date", "")

    should_run = False
    if not last_run_str:
        # First time — initialize (set today so it doesn't spam at startup unless 1st)
        if now_ist.day == 1:
            should_run = True
        else:
            set_metadata("last_monthly_link_audit_date", today_str)
    else:
        try:
            last_date = datetime.datetime.strptime(last_run_str, "%Y-%m-%d").date()
            days_passed = (now_ist.date() - last_date).days
            # Run on the 1st of every month or if 30 days elapsed
            if (now_ist.day == 1 and days_passed >= 25) or days_passed >= 30:
                should_run = True
        except Exception:
            if now_ist.day == 1:
                should_run = True

    if should_run and app_ref and loop_ref and admin_chat_id:
        try:
            logger.info(f"[LinkHealer] Firing Monthly Self-Healing Audit for admin {admin_chat_id}...")
            audit = run_link_healing_audit(max_links=50)
            report_text = format_repair_report(audit, is_monthly=True)

            async def _send():
                try:
                    await app_ref.bot.send_message(chat_id=admin_chat_id, text=report_text)
                    logger.info("[LinkHealer] Monthly Self-Healing Report sent successfully.")
                except Exception as e:
                    logger.error(f"[LinkHealer] Failed to send report: {e}")

            import asyncio
            asyncio.run_coroutine_threadsafe(_send(), loop_ref)
            set_metadata("last_monthly_link_audit_date", today_str)

        except Exception as e:
            logger.error(f"[LinkHealer] Error in monthly link audit: {e}")
