import pytest
from unittest.mock import patch
import config
from auth_preflight import run_auth_preflight


def test_auth_preflight_disables_arena_on_401():
    with patch.object(config, "ARENA_ENABLED", True):
        with patch("arena_uploader.verify_arena_token", return_value=False):
            report = run_auth_preflight()
            assert report.get("arena") == "auto_disabled_401"
            assert config.ARENA_ENABLED is False


def test_auth_preflight_keeps_arena_enabled_on_success():
    with patch.object(config, "ARENA_ENABLED", True):
        with patch("arena_uploader.verify_arena_token", return_value=True):
            report = run_auth_preflight()
            assert report.get("arena") == "verified"
            assert config.ARENA_ENABLED is True


def test_auth_preflight_disables_pixelfed_on_401():
    with patch.object(config, "PIXELFED_ENABLED", True):
        with patch("pixelfed_uploader.verify_pixelfed_token", return_value=False):
            report = run_auth_preflight()
            assert report.get("pixelfed") == "auto_disabled_401"
            assert config.PIXELFED_ENABLED is False


def test_auth_preflight_keeps_pixelfed_enabled_on_success():
    with patch.object(config, "PIXELFED_ENABLED", True):
        with patch("pixelfed_uploader.verify_pixelfed_token", return_value=True):
            report = run_auth_preflight()
            assert report.get("pixelfed") == "verified"
            assert config.PIXELFED_ENABLED is True

