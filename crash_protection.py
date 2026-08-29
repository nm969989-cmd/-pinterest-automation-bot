"""
Render Free Tier Crash Protection
===================================
Protections for running on Render free tier (512MB RAM, ephemeral disk):

1. Global exception handler — catches all unhandled crashes
2. Thread watchdog — monitors & auto-restarts dead background threads
3. Disk cleanup — deletes old downloaded/processed images (prevents disk full)
4. Memory guard — logs memory usage, warns before OOM kill
5. Graceful shutdown — saves to JSONBin before dying
6. Signal handlers — clean exit on SIGTERM (Render sends this on restart)
"""

import os
import gc
import sys
import time
import signal
import shutil
import threading
import datetime
from logger import get_logger

logger = get_logger(__name__)

# ── Disk cleanup ─────────────────────────────────────────────────────────────

def cleanup_old_files(max_age_hours: int = 6):
    """
    Delete images older than max_age_hours from downloads/ and processed/.
    Render free tier has limited disk — keep it clean.
    """
    cutoff = time.time() - (max_age_hours * 3600)
    removed = 0
    for folder in ["downloads", "processed"]:
        if not os.path.exists(folder):
            continue
        for fname in os.listdir(folder):
            fpath = os.path.join(folder, fname)
            try:
                if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath)
                    removed += 1
            except Exception:
                pass
    if removed:
        logger.info(f"[Cleanup] Removed {removed} old image files (>{max_age_hours}h old).")
    return removed


def get_disk_usage_mb(folder: str = ".") -> float:
    """Returns disk usage of a folder in MB."""
    total = 0
    for dirpath, _, filenames in os.walk(folder):
        for f in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except Exception:
                pass
    return total / (1024 * 1024)


# ── Memory monitoring ─────────────────────────────────────────────────────────

def get_memory_mb() -> float:
    """Returns current process memory usage in MB."""
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1]) / 1024
        except Exception:
            pass
    return 0.0


# ── Thread watchdog ───────────────────────────────────────────────────────────

_watched_threads: list[dict] = []


def register_thread(name: str, factory_fn, *args, **kwargs):
    """
    Register a thread to be monitored. If it dies, the watchdog restarts it.
    factory_fn must return a running threading.Thread.
    """
    _watched_threads.append({
        "name":       name,
        "factory":    factory_fn,
        "args":       args,
        "kwargs":     kwargs,
        "thread":     None,
        "restarts":   0,
    })
    logger.info(f"[Watchdog] Registered thread: {name}")


def _start_watchdog():
    """
    Background watchdog: checks every 60s if registered threads are alive.
    Restarts dead threads automatically.
    """
    def _loop():
        while True:
            time.sleep(60)
            for entry in _watched_threads:
                t = entry.get("thread")
                if t is None or not t.is_alive():
                    if entry["restarts"] > 0:
                        logger.warning(
                            f"[Watchdog] Thread '{entry['name']}' died — restarting "
                            f"(restart #{entry['restarts']})..."
                        )
                    try:
                        new_thread = entry["factory"](*entry["args"], **entry["kwargs"])
                        entry["thread"] = new_thread
                        entry["restarts"] += 1
                    except Exception as e:
                        logger.error(f"[Watchdog] Failed to restart '{entry['name']}': {e}")

    t = threading.Thread(target=_loop, daemon=True, name="Watchdog")
    t.start()
    logger.info("[Watchdog] Thread watchdog started.")


# ── Periodic health check ─────────────────────────────────────────────────────

def _start_health_monitor():
    """
    Logs memory/disk usage every 30 minutes.
    Triggers cleanup if disk > 200MB or memory > 400MB.
    """
    def _loop():
        while True:
            time.sleep(1800)  # every 30 min
            try:
                mem_mb  = get_memory_mb()
                disk_mb = get_disk_usage_mb("downloads") + get_disk_usage_mb("processed")

                logger.info(
                    f"[Health] Memory: {mem_mb:.1f}MB | "
                    f"Image disk: {disk_mb:.1f}MB"
                )

                # Auto-cleanup if disk getting full
                if disk_mb > 100:
                    logger.warning(f"[Health] Disk usage {disk_mb:.1f}MB — running cleanup...")
                    cleanup_old_files(max_age_hours=2)
                    gc.collect()

                # Warn if memory high (Render free = 512MB)
                if mem_mb > 400:
                    logger.warning(
                        f"[Health] Memory {mem_mb:.1f}MB is high! "
                        f"Render limit is 512MB. Running GC..."
                    )
                    gc.collect()

            except Exception as e:
                logger.error(f"[Health] Monitor error: {e}")

    t = threading.Thread(target=_loop, daemon=True, name="HealthMonitor")
    t.start()
    logger.info("[Health] Health monitor started (checks every 30 min).")


# ── Graceful shutdown ─────────────────────────────────────────────────────────

def _setup_signal_handlers():
    """
    Handle SIGTERM (Render sends this before killing the container).
    Saves state to JSONBin before exiting.
    """
    def _graceful_exit(signum, frame):
        logger.info("[Shutdown] SIGTERM received — saving state to JSONBin...")
        try:
            from jsonbin_sync import save_cloud_state
            save_cloud_state()
            logger.info("[Shutdown] State saved. Exiting cleanly.")
        except Exception as e:
            logger.error(f"[Shutdown] Failed to save state: {e}")
        sys.exit(0)

    try:
        signal.signal(signal.SIGTERM, _graceful_exit)
        signal.signal(signal.SIGINT,  _graceful_exit)
        logger.info("[Shutdown] Signal handlers registered (SIGTERM + SIGINT).")
    except Exception as e:
        logger.warning(f"[Shutdown] Could not register signal handlers: {e}")


# ── Global exception hook ─────────────────────────────────────────────────────

def _setup_global_exception_hook():
    """Catch all unhandled exceptions and log them before crashing."""
    def _hook(exc_type, exc_value, exc_tb):
        import traceback
        logger.critical(
            "UNHANDLED EXCEPTION — saving state before crash!\n"
            + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        )
        try:
            from jsonbin_sync import save_cloud_state
            save_cloud_state()
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)

    sys.excepthook = _hook
    logger.info("[Protection] Global exception hook installed.")


# ── Main entry point ──────────────────────────────────────────────────────────

def init_crash_protection():
    """
    Call once at startup. Sets up all Render free-tier crash protections.
    """
    logger.info("[Protection] Initializing Render crash protection...")

    _setup_signal_handlers()
    _setup_global_exception_hook()
    _start_watchdog()
    _start_health_monitor()

    # Initial cleanup of any leftover files from previous run
    cleanup_old_files(max_age_hours=1)

    logger.info("[Protection] All crash protections active.")
