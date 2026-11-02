"""Safe structured and text logging configuration."""
from __future__ import annotations

import json
import logging as std_logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import Settings
from app.observability.request_context import get_request_id

_BEARER_PATTERN = re.compile(r"(?i)(\bbearer\s+)[^\s,;]+")
_AUTH_HEADER_PATTERN = re.compile(
    r"(?i)(\b(?:authorization|proxy-authorization|x-api-key)\b\s*[:=]\s*)"
    r"[^\s,;]+(?:\s+[^\s,;]+)?"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(\b(?:api[_-]?key|admin[_-]?key|password|token)\b\s*[:=]\s*)"
    r"(['\"]?)[^\s,'\";]+"
)
_PROVIDER_KEY_PATTERN = re.compile(
    r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,}|"
    r"AIza[A-Za-z0-9_-]{25,})\b"
)
_configured_secrets: tuple[str, ...] = ()
logger = std_logging.getLogger("app.request")


def _sanitize(value: str) -> str:
    value = _AUTH_HEADER_PATTERN.sub(r"\1[REDACTED]", value)
    value = _BEARER_PATTERN.sub(r"\1[REDACTED]", value)
    value = _SECRET_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", value)
    value = _PROVIDER_KEY_PATTERN.sub("[REDACTED]", value)
    for secret in _configured_secrets:
        if secret:
            value = value.replace(secret, "[REDACTED]")
    return value


def sanitize_error(error: str, *request_payloads: Any) -> str:
    """Keep upstream diagnostics useful without copying request content to logs."""
    for payload in request_payloads:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(exclude_none=True)
        values = [payload]
        while values:
            current = values.pop()
            if isinstance(current, dict):
                values.extend(current.values())
            elif isinstance(current, (list, tuple)):
                values.extend(current)
            elif isinstance(current, str) and current:
                error = error.replace(current, "[REDACTED]")
    return _sanitize(error)[:2000]


class StructuredFormatter(std_logging.Formatter):
    """Single-line JSON formatter that exposes only approved log fields."""

    def format(self, record: std_logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": _sanitize(record.getMessage()),
            "request_id": getattr(record, "request_id", get_request_id()),
        }
        for name in (
            "provider",
            "model",
            "status",
            "latency_ms",
            "tokens",
            "cost_usd",
            "attempts",
            "api_key_id",
            "error",
        ):
            if hasattr(record, name):
                value = getattr(record, name)
                fields[name] = _sanitize(value) if isinstance(value, str) else value
        return json.dumps(fields, separators=(",", ":"), ensure_ascii=True)


class TextFormatter(StructuredFormatter):
    """Single-line key/value formatter for local development."""

    def format(self, record: std_logging.LogRecord) -> str:
        payload = json.loads(super().format(record))
        return " ".join(
            f"{name}={json.dumps(value, separators=(',', ':'), ensure_ascii=True)}"
            for name, value in payload.items()
        )


def configure_logging(settings: Settings) -> None:
    """Configure stdlib root logging with the selected safe single-line format."""
    global _configured_secrets

    _configured_secrets = tuple(
        value
        for name in (
            "openai_api_key",
            "anthropic_api_key",
            "gemini_api_key",
            "groq_api_key",
            "doubleword_api_key",
            "admin_api_key",
            "metrics_token",
        )
        if (value := getattr(settings, name, None))
    )
    level = getattr(std_logging, settings.log_level.upper(), None)
    if not isinstance(level, int):
        raise ValueError(f"Invalid LOG_LEVEL: {settings.log_level}")
    formatter: std_logging.Formatter
    if settings.log_format == "json":
        formatter = StructuredFormatter()
    else:
        formatter = TextFormatter()

    root = std_logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        root.addHandler(std_logging.StreamHandler(sys.stdout))
    for handler in root.handlers:
        handler.setFormatter(formatter)
