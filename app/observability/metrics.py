"""Prometheus metrics and bounded label helpers for gateway operations."""
from __future__ import annotations

from contextlib import contextmanager
from threading import Lock
from typing import Generator

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from sqlalchemy import select

from app.config import Settings
from app.observability.logging import logger
from app.observability.request_context import get_request_id, request_state_var

MAX_MODEL_LABELS = 50
_MAX_KNOWN_MODELS = MAX_MODEL_LABELS - 1
_PROVIDERS = {"openai", "anthropic", "gemini", "groq", "doubleword"}
_STATUSES = {
    "ok",
    "error",
    "partial",
    "client_disconnect",
    "rate_limited",
    "budget_rejected",
}
_RETRY_REASONS = {"rate_limit", "timeout", "server_error", "other"}
_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 15, 30, 60)

# One private registry ensures a single registration point and avoids global
# default-registry collisions when tests create/reload application instances.
REGISTRY = CollectorRegistry()

gateway_requests_total = Counter(
    "gateway_requests_total",
    "Gateway completion and embedding requests by final outcome.",
    ("provider", "model", "status", "streamed"),
    registry=REGISTRY,
)
gateway_request_duration_seconds = Histogram(
    "gateway_request_duration_seconds",
    "Time spent handling a gateway completion or embedding request.",
    ("provider", "model"),
    buckets=_BUCKETS,
    registry=REGISTRY,
)
gateway_ttft_seconds = Histogram(
    "gateway_ttft_seconds",
    "Time from starting the final stream attempt to its first chunk.",
    ("provider", "model"),
    buckets=_BUCKETS,
    registry=REGISTRY,
)
gateway_tokens_total = Counter(
    "gateway_tokens_total",
    "Input and output tokens reported by upstream providers.",
    ("provider", "model", "type"),
    registry=REGISTRY,
)
gateway_cost_usd_total = Counter(
    "gateway_cost_usd_total",
    "Estimated upstream cost in USD.",
    ("provider", "model"),
    registry=REGISTRY,
)
gateway_retries_total = Counter(
    "gateway_retries_total",
    "Upstream retry attempts.",
    ("provider", "reason"),
    registry=REGISTRY,
)
gateway_fallbacks_total = Counter(
    "gateway_fallbacks_total",
    "Transitions between primary and fallback models.",
    ("from_model", "to_model"),
    registry=REGISTRY,
)
gateway_rate_limited_total = Counter(
    "gateway_rate_limited_total",
    "Requests rejected by the gateway rate limiter.",
    registry=REGISTRY,
)
gateway_budget_rejected_total = Counter(
    "gateway_budget_rejected_total",
    "Requests rejected by the monthly budget gate.",
    registry=REGISTRY,
)
gateway_inflight_requests = Gauge(
    "gateway_inflight_requests",
    "Gateway completion and embedding requests currently in progress.",
    registry=REGISTRY,
)


class _ModelLabelLimiter:
    def __init__(self) -> None:
        self._configured_models: set[str] = set()
        self._recent_models: list[str] = []
        self._lock = Lock()

    def configure(self, models: list[str]) -> None:
        with self._lock:
            for model in models:
                if model in self._configured_models:
                    continue
                if len(self._configured_models) >= _MAX_KNOWN_MODELS:
                    break
                self._configured_models.add(model)
            self._recent_models = [
                model for model in self._recent_models
                if model not in self._configured_models
            ][: self._recent_capacity]

    @property
    def _recent_capacity(self) -> int:
        return max(_MAX_KNOWN_MODELS - len(self._configured_models), 0)

    def seed_recent(self, models: list[str]) -> None:
        with self._lock:
            for model in models:
                if model in self._configured_models or model in self._recent_models:
                    continue
                if len(self._recent_models) >= self._recent_capacity:
                    break
                self._recent_models.append(model)

    def remember(self, model: str) -> None:
        if not model:
            return
        with self._lock:
            if model in self._configured_models or model in self._recent_models:
                return
            if len(self._recent_models) < self._recent_capacity:
                self._recent_models.append(model)

    def label(self, model: str) -> str:
        with self._lock:
            if model in self._configured_models or model in self._recent_models:
                return model
            return "other"


_model_labels = _ModelLabelLimiter()


def configure_model_labels(settings: Settings) -> None:
    """Reserve labels for configured aliases and fallback-chain models."""
    from app.engine import normalize_model

    aliases = settings.model_alias_map
    configured = {
        normalize_model(model, aliases)
        for model in aliases.values()
        if isinstance(model, str) and model
    }
    for key, chain in settings.fallback_chain_map.items():
        if not key.endswith("/*"):
            configured.add(normalize_model(key, aliases))
        configured.update(normalize_model(model, aliases) for model in chain)
    _model_labels.configure(sorted(configured))


async def configure_recent_model_labels() -> None:
    """Seed bounded model labels from the most recent request-log entries."""
    from app.db import SessionLocal
    from app.models import RequestLog

    async with SessionLocal() as session:
        result = await session.execute(
            select(RequestLog.model)
            .order_by(RequestLog.created_at.desc())
            .limit(MAX_MODEL_LABELS * 20)
        )
        recent_models = list(dict.fromkeys(result.scalars().all()))
    _model_labels.seed_recent(recent_models)


def _provider_label(provider: str) -> str:
    return provider if provider in _PROVIDERS else "other"


def model_label(model: str) -> str:
    """Return a known normalized model label or the bounded ``other`` label."""
    return _model_labels.label(model)


def _retry_reason(reason: str) -> str:
    return reason if reason in _RETRY_REASONS else "other"


def record_request(
    *,
    provider: str,
    model: str,
    status: str,
    streamed: bool,
    duration_seconds: float,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int | None = None,
    cost_usd: float = 0.0,
    attempts: int = 0,
    api_key_id: int | None = None,
    error: str | None = None,
) -> None:
    request_state = request_state_var.get()
    if request_state is not None and request_state.completed:
        return
    if request_state is not None:
        request_state.completed = True

    provider = _provider_label(provider)
    _model_labels.remember(model)
    model = model_label(model)
    status = status if status in _STATUSES else "error"
    gateway_requests_total.labels(
        provider, model, status, str(streamed).lower()
    ).inc()
    gateway_request_duration_seconds.labels(provider, model).observe(
        max(duration_seconds, 0.0)
    )
    if input_tokens > 0:
        gateway_tokens_total.labels(provider, model, "input").inc(input_tokens)
    if output_tokens > 0:
        gateway_tokens_total.labels(provider, model, "output").inc(output_tokens)
    if cost_usd > 0:
        gateway_cost_usd_total.labels(provider, model).inc(cost_usd)
    logger.info(
        "request_completed",
        extra={
            "request_id": get_request_id(),
            "provider": provider,
            "model": model,
            "status": status,
            "latency_ms": max(int(duration_seconds * 1000), 0),
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": (
                    input_tokens + output_tokens
                    if total_tokens is None
                    else total_tokens
                ),
            },
            "cost_usd": cost_usd,
            "attempts": attempts,
            "api_key_id": api_key_id,
            "error": error,
        },
    )


def record_retry(provider: str, reason: str) -> None:
    gateway_retries_total.labels(
        _provider_label(provider), _retry_reason(reason)
    ).inc()


def record_fallback(from_model: str, to_model: str) -> None:
    _model_labels.remember(from_model)
    _model_labels.remember(to_model)
    gateway_fallbacks_total.labels(
        model_label(from_model), model_label(to_model)
    ).inc()


def record_ttft(provider: str, model: str, seconds: float) -> None:
    _model_labels.remember(model)
    gateway_ttft_seconds.labels(
        _provider_label(provider), model_label(model)
    ).observe(max(seconds, 0.0))


def record_rate_limited() -> None:
    gateway_rate_limited_total.inc()


def record_budget_rejected() -> None:
    gateway_budget_rejected_total.inc()


@contextmanager
def inflight() -> Generator[None, None, None]:
    gateway_inflight_requests.inc()
    try:
        yield
    finally:
        gateway_inflight_requests.dec()


def _record_unhandled_gateway_request(
    *, status: str, duration_seconds: float
) -> None:
    record_request(
        provider="unknown",
        model="other",
        status=status,
        streamed=False,
        duration_seconds=duration_seconds,
    )
