"""Regression tests for the code-review fixes (phases 1-3)."""
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _update(chat_id):
    msg = SimpleNamespace(reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(effective_chat=SimpleNamespace(id=chat_id), message=msg)


@pytest.fixture
def tg(monkeypatch):
    import telegram_bot
    monkeypatch.setitem(telegram_bot._state, "admin_chat_id", None)
    monkeypatch.delenv("TELEGRAM_ADMIN_CHAT_ID", raising=False)
    return telegram_bot


# -- Phase 2: Telegram auth ---------------------------------------------------
def test_is_admin_fails_closed_when_unconfigured(tg):
    assert tg._is_admin(_update(111)) is False


def test_is_admin_matches_env_only(tg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    assert tg._is_admin(_update(42)) is True
    assert tg._is_admin(_update(43)) is False


def test_start_cannot_hijack_admin(tg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    upd = _update(999)
    asyncio.run(tg.cmd_start(upd, MagicMock()))
    upd.message.reply_text.assert_not_called()
    assert tg._state["admin_chat_id"] is None


def test_start_does_not_register_admin_when_unconfigured(tg):
    upd = _update(999)
    asyncio.run(tg.cmd_start(upd, MagicMock()))
    # Echoes the chat id so the owner can configure it, but never self-registers.
    upd.message.reply_text.assert_called_once()
    assert tg._state["admin_chat_id"] is None


def test_callback_ignored_for_non_admin(tg, monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_ID", "42")
    tg._pending_approvals["k"] = {"title": "t"}
    query = SimpleNamespace(
        data="post:k", answer=AsyncMock(), edit_message_caption=AsyncMock()
    )
    upd = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=7))
    asyncio.run(tg.handle_approval_callback(upd, MagicMock()))
    query.edit_message_caption.assert_not_called()
    assert "k" in tg._pending_approvals  # not consumed
    tg._pending_approvals.pop("k", None)


# -- Phase 2: log redaction ---------------------------------------------------
def test_logger_redacts_bot_token():
    from logger import redact_secrets
    url = "https://api.telegram.org/bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getUpdates"
    out = redact_secrets(f"HTTPSConnectionPool: {url}")
    assert "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" not in out
    assert "bot<REDACTED>" in out


# -- Phase 1: no hardcoded secrets -------------------------------------------
_SECRET_FRAGMENTS = [
    "089b87f80b4640d6", "1506c60298ac2533", "6d207e02198a847a",
    "LsGYl3Y6qYncpDAD", "PrpAMecPmUx1IQDv", "rp+18TLVQhsYO9qQ",
    "9b67b0c368ad153c", "3bcc44eb9d2baef6",
]


@pytest.mark.parametrize("fragment", _SECRET_FRAGMENTS)
def test_no_hardcoded_secret_in_source(fragment):
    offenders = [
        p.name for p in ROOT.glob("*.py") if fragment in p.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


# -- Phase 3: crashers --------------------------------------------------------
def test_amazon_search_has_app_base_url_at_module_level():
    import amazon_search
    assert hasattr(amazon_search, "APP_BASE_URL")


def test_keep_alive_defines_logger():
    import keep_alive
    assert hasattr(keep_alive, "logger")


def test_scheduler_supervisor_restarts_after_crash(monkeypatch):
    import scheduler as sched
    monkeypatch.setattr(sched.time, "sleep", lambda s: None)
    s = sched.PinScheduler()
    s.is_running = True
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        s.is_running = False

    monkeypatch.setattr(s, "_worker_loop_inner", flaky)
    s._worker_loop()
    assert calls["n"] == 2


def test_check_db_is_import_safe():
    import importlib
    mod = importlib.import_module("check_db")  # must not execute the inspector
    assert callable(mod.main)
