"""Tests for MCP authentication helpers."""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from rlm.mcp.auth import (
    format_auth_instructions,
    get_auth_status,
    get_snipara_auth,
    get_snipara_token,
    load_snipara_tokens,
)

UTC = timezone.utc


class TestLoadSniparaTokens:
    """Tests for load_snipara_tokens function."""

    def test_returns_empty_dict_when_file_not_exists(self, tmp_path):
        """Should return empty dict when token file doesn't exist."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            result = load_snipara_tokens()
            assert result == {}

    def test_returns_tokens_when_file_exists(self, tmp_path):
        """Should return token data from file."""
        token_file = tmp_path / "tokens.json"
        token_data = {
            "proj_123": {
                "access_token": "test_token",
                "project_slug": "test-project",
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = load_snipara_tokens()
            assert result == token_data

    def test_returns_empty_dict_on_invalid_json(self, tmp_path):
        """Should return empty dict when JSON is invalid."""
        token_file = tmp_path / "tokens.json"
        token_file.write_text("not valid json")

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = load_snipara_tokens()
            assert result == {}

    def test_returns_empty_dict_on_io_error(self, tmp_path):
        """Should return empty dict on IO error."""
        token_file = tmp_path / "tokens.json"
        token_file.mkdir()  # Create as directory to cause IO error

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = load_snipara_tokens()
            assert result == {}


class TestGetSniparaToken:
    """Tests for get_snipara_token function."""

    def test_returns_none_when_no_tokens(self, tmp_path):
        """Should return None when no tokens exist."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            result = get_snipara_token("proj_123")
            assert result is None

    def test_returns_first_token_when_no_project_specified(self, tmp_path):
        """Should return first token when project_id is None."""
        token_file = tmp_path / "tokens.json"
        token_data = {
            "proj_123": {"access_token": "token1", "project_slug": "project1"},
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = get_snipara_token(None)
            assert result is not None
            assert result["access_token"] == "token1"

    def test_returns_specific_project_token(self, tmp_path):
        """Should return token for specified project."""
        token_file = tmp_path / "tokens.json"
        token_data = {
            "proj_123": {"access_token": "token1"},
            "proj_456": {"access_token": "token2"},
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = get_snipara_token("proj_456")
            assert result is not None
            assert result["access_token"] == "token2"

    def test_returns_none_for_unknown_project(self, tmp_path):
        """Should return None for unknown project ID."""
        token_file = tmp_path / "tokens.json"
        token_data = {"proj_123": {"access_token": "token1"}}
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = get_snipara_token("proj_unknown")
            assert result is None

    def test_returns_valid_non_expired_token(self, tmp_path):
        """Should return token that hasn't expired."""
        token_file = tmp_path / "tokens.json"
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "valid_token",
                "expires_at": future_time,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = get_snipara_token("proj_123")
            assert result is not None
            assert result["access_token"] == "valid_token"

    def test_returns_none_for_expired_token_no_refresh(self, tmp_path):
        """Should return None for expired token when refresh fails."""
        token_file = tmp_path / "tokens.json"
        past_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "expired_token",
                "expires_at": past_time,
                "refresh_token": None,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            with patch("rlm.mcp.auth._try_refresh_token", return_value=None):
                result = get_snipara_token("proj_123")
                assert result is None

    def test_returns_token_without_expires_at(self, tmp_path):
        """Should return token when expires_at is not set."""
        token_file = tmp_path / "tokens.json"
        token_data = {"proj_123": {"access_token": "token_no_expiry"}}
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            result = get_snipara_token("proj_123")
            assert result is not None
            assert result["access_token"] == "token_no_expiry"


class TestGetSniparaAuth:
    """Tests for get_snipara_auth function."""

    def test_returns_oauth_when_valid_token(self, tmp_path):
        """Should return OAuth auth when valid token exists."""
        token_file = tmp_path / "tokens.json"
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "oauth_token",
                "project_slug": "my-project",
                "expires_at": future_time,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            auth_header, project_slug = get_snipara_auth("proj_123")
            assert auth_header == "Bearer oauth_token"
            assert project_slug == "my-project"

    def test_returns_api_key_when_no_oauth(self, tmp_path):
        """Should fall back to API key when no OAuth token."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            with patch.dict(
                os.environ,
                {
                    "SNIPARA_API_KEY": "snp-test_key",
                    "SNIPARA_PROJECT_SLUG": "env-project",
                },
            ):
                auth_header, project_slug = get_snipara_auth()
                assert auth_header == "snp-test_key"
                assert project_slug == "env-project"

    def test_returns_none_when_no_auth(self, tmp_path):
        """Should return None when no auth available."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            with patch.dict(os.environ, {}, clear=True):
                # Clear any existing env vars
                env_backup = {}
                for key in ["SNIPARA_API_KEY", "SNIPARA_PROJECT_SLUG", "SNIPARA_PROJECT_ID"]:
                    if key in os.environ:
                        env_backup[key] = os.environ.pop(key)

                try:
                    auth_header, project_slug = get_snipara_auth()
                    assert auth_header is None
                    assert project_slug is None
                finally:
                    os.environ.update(env_backup)

    def test_uses_project_id_fallback(self, tmp_path):
        """Should use project_id from token when project_slug not set."""
        token_file = tmp_path / "tokens.json"
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "oauth_token",
                "project_id": "proj_123",
                "expires_at": future_time,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            auth_header, project_slug = get_snipara_auth("proj_123")
            assert auth_header == "Bearer oauth_token"
            assert project_slug == "proj_123"


class TestGetAuthStatus:
    """Tests for get_auth_status function."""

    def test_returns_unauthenticated_when_no_auth(self, tmp_path):
        """Should show unauthenticated when no tokens or key."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            env_backup = os.environ.pop("SNIPARA_API_KEY", None)
            try:
                status = get_auth_status()
                assert status["authenticated"] is False
                assert status["oauth_available"] is False
                assert status["api_key_available"] is False
                assert status["auth_method"] is None
            finally:
                if env_backup:
                    os.environ["SNIPARA_API_KEY"] = env_backup

    def test_returns_oauth_authenticated(self, tmp_path):
        """Should show OAuth authentication."""
        token_file = tmp_path / "tokens.json"
        future_time = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "token",
                "project_slug": "my-project",
                "expires_at": future_time,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            env_backup = os.environ.pop("SNIPARA_API_KEY", None)
            try:
                status = get_auth_status()
                assert status["authenticated"] is True
                assert status["oauth_available"] is True
                assert status["auth_method"] == "oauth"
                assert len(status["oauth_projects"]) == 1
                assert status["oauth_projects"][0]["valid"] is True
            finally:
                if env_backup:
                    os.environ["SNIPARA_API_KEY"] = env_backup

    def test_shows_expired_token(self, tmp_path):
        """Should show expired OAuth token status."""
        token_file = tmp_path / "tokens.json"
        past_time = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        token_data = {
            "proj_123": {
                "access_token": "expired",
                "expires_at": past_time,
            }
        }
        token_file.write_text(json.dumps(token_data))

        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", token_file):
            env_backup = os.environ.pop("SNIPARA_API_KEY", None)
            try:
                status = get_auth_status()
                assert status["oauth_projects"][0]["valid"] is False
                assert status["oauth_projects"][0]["status"] == "expired"
            finally:
                if env_backup:
                    os.environ["SNIPARA_API_KEY"] = env_backup

    def test_shows_api_key_authentication(self, tmp_path):
        """Should show API key authentication."""
        with patch("rlm.mcp.auth.SNIPARA_TOKEN_FILE", tmp_path / "nonexistent.json"):
            with patch.dict(os.environ, {"SNIPARA_API_KEY": "snp-test"}):
                status = get_auth_status()
                assert status["authenticated"] is True
                assert status["api_key_available"] is True
                assert status["auth_method"] == "api_key"


class TestFormatAuthInstructions:
    """Tests for format_auth_instructions function."""

    def test_returns_instructions_string(self):
        """Should return non-empty instructions."""
        result = format_auth_instructions()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_oauth_instructions(self):
        """Should include OAuth instructions."""
        result = format_auth_instructions()
        assert "OAuth" in result
        assert "snipara-mcp-login" in result

    def test_includes_api_key_instructions(self):
        """Should include API key instructions."""
        result = format_auth_instructions()
        assert "API Key" in result
        assert "SNIPARA_API_KEY" in result

    def test_includes_config_instructions(self):
        """Should include config file instructions."""
        result = format_auth_instructions()
        assert "snipara-sandbox.toml" in result
