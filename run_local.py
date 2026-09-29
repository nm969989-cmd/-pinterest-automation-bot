"""
run_local.py - LOCAL ONLY runner for the Pinterest bot.

Usage:
    python run_local.py

This is the correct way to run the bot on your LOCAL MACHINE.
- DO NOT use this when the bot is deployed on Render (it runs main.py automatically).
- This script sets LOCAL_MODE=true so the bot won't conflict with a Render instance.
- Auto-restarts on crash with exponential backoff.
- Use Ctrl+C to stop.

WHY TWO FILES?
  main.py  → Render runs this (cloud hosting, always on)
  run_local.py → You run this locally for testing

Running both at the same time CAUSES "Conflict: terminated by other getUpdates"
because Telegram only allows ONE bot polling at a time per token.
"""

import os
import sys
import time
import subprocess
from datetime import datetime

# ─── Safety guard: don't accidentally run this on Render ───────────────────
if os.environ.get("RENDER") == "true":
    print("[run_local] ERROR: This is a Render environment. "
          "Render runs main.py directly. Exiting to avoid conflict.")
    sys.exit(1)

# ─── Set local mode flag ────────────────────────────────────────────────────
os.environ["LOCAL_MODE"] = "true"

print("=" * 60)
print("  Animanoizing Bot — LOCAL MODE")
print("  Press Ctrl+C to stop")
print("=" * 60)
print()

# ─── Auto-restart loop with exponential backoff ─────────────────────────────
MAX_RESTARTS  = 10        # Give up after 10 consecutive crashes
BACKOFF_START = 5         # Start with 5s delay after crash
BACKOFF_MAX   = 120       # Cap delay at 2 minutes

restarts   = 0
backoff    = BACKOFF_START

while True:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Starting bot (restart #{restarts})...")
    try:
        result = subprocess.run(
            [sys.executable, "main.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )
        exit_code = result.returncode
    except KeyboardInterrupt:
        print("\n[run_local] Stopped by user (Ctrl+C). Goodbye!")
        sys.exit(0)
    except Exception as e:
        exit_code = -1
        print(f"[run_local] Subprocess error: {e}")

    # Normal exit (0) = user stopped intentionally
    if exit_code == 0:
        print("[run_local] Bot exited cleanly. Stopping.")
        break

    restarts += 1
    if restarts >= MAX_RESTARTS:
        print(f"[run_local] ERROR: Bot crashed {MAX_RESTARTS} times in a row. "
              "Check logs/bot.log for errors. Giving up.")
        sys.exit(1)

    print(f"[run_local] Bot crashed (exit {exit_code}). "
          f"Restarting in {backoff}s... (crash #{restarts}/{MAX_RESTARTS})")
    try:
        time.sleep(backoff)
    except KeyboardInterrupt:
        print("\n[run_local] Stopped by user. Goodbye!")
        sys.exit(0)

    # Exponential backoff
    backoff = min(backoff * 2, BACKOFF_MAX)
