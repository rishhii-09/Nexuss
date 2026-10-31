"""Settings tests — the Docker-secret / *_FILE key loader."""
from __future__ import annotations

from app.config import Settings


def test_key_file_overrides_inline_key(tmp_path):
    secret = tmp_path / "doubleword.key"
    secret.write_text("sk-from-file\n")  # trailing newline must be stripped
    settings = Settings(
        _env_file=None,  # hermetic: ignore the developer's local .env
        doubleword_api_key="sk-inline-should-lose",
        doubleword_api_key_file=str(secret),
    )
    assert settings.doubleword_api_key == "sk-from-file"


def test_groq_key_is_optional_and_loaded_from_file(tmp_path):
    secret = tmp_path / "groq.key"
    secret.write_text("groq-from-file\n")
    settings = Settings(
        _env_file=None,
        groq_api_key_file=str(secret),
        openai_api_key=None,
        anthropic_api_key=None,
        gemini_api_key=None,
        anthropic_api_key_file=None,
        doubleword_api_key_file=None,
    )
    assert settings.groq_api_key == "groq-from-file"
    assert settings.anthropic_api_key is None
    assert settings.doubleword_api_key is None


def test_missing_key_file_leaves_inline_key(tmp_path):
    settings = Settings(
        _env_file=None,
        doubleword_api_key="sk-inline",
        doubleword_api_key_file=str(tmp_path / "does-not-exist.key"),
    )
    assert settings.doubleword_api_key == "sk-inline"


def test_empty_key_file_does_not_clobber(tmp_path):
    secret = tmp_path / "empty.key"
    secret.write_text("   \n")
    settings = Settings(
        _env_file=None,
        anthropic_api_key="sk-inline",
        anthropic_api_key_file=str(secret),
    )
    assert settings.anthropic_api_key == "sk-inline"


def test_admin_key_file_overrides_inline_key(tmp_path):
    secret = tmp_path / "admin.key"
    secret.write_text("admin-from-file\n")  # trailing newline must be stripped
    settings = Settings(
        _env_file=None,
        admin_api_key="admin-inline-should-lose",
        admin_api_key_file=str(secret),
    )
    assert settings.admin_api_key == "admin-from-file"
