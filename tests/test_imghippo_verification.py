"""
tests/test_imghippo_verification.py — Unit Tests for Imghippo Verification & Parsing
"""

from unittest.mock import patch, MagicMock
from imghippo_uploader import verify_imghippo_token, get_imghippo_info


def test_verify_imghippo_token_empty_key():
    with patch("imghippo_uploader._get_api_key", return_value=""):
        assert verify_imghippo_token() is False


def test_verify_imghippo_token_success_with_updated_api_response():
    # Real Imghippo v1 API response format when API key is valid
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "success": False,
        "status": 400,
        "message": "Please provide at least one file"
    }

    with patch("imghippo_uploader._get_api_key", return_value="mock_valid_key"), \
         patch("requests.post", return_value=mock_resp):
        assert verify_imghippo_token() is True


def test_verify_imghippo_token_invalid_key():
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.json.return_value = {
        "success": False,
        "status": 401,
        "message": "Invalid API key"
    }

    with patch("imghippo_uploader._get_api_key", return_value="mock_invalid_key"), \
         patch("requests.post", return_value=mock_resp):
        assert verify_imghippo_token() is False


def test_get_imghippo_info():
    with patch("imghippo_uploader._get_api_key", return_value="0123456789abcdef0123"), \
         patch("imghippo_uploader.verify_imghippo_token", return_value=True):
        info = get_imghippo_info()
        assert info["service"] == "Imghippo"
        assert info["status"] == "Active"
        assert info["api_key_masked"].startswith("012345...")

