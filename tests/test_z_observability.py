"""Prometheus endpoint and gateway instrumentation tests."""
from __future__ import annotations

import asyncio
import json
import logging
from uuid import UUID

import pytest

import app.routing as routing
import app.observability as metrics
import app.routes.chat as chat_route
import app.routes.metrics as metrics_route
from app.config import Settings
from app.schemas import ChatCompletionRequest


def _sample(registry, name, labels=None):
    return registry.get_sample_value(name, labels=labels or {}) or 0


def _completion(model):
    return {
        "id": "metric-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    }


def _route_endpoint(client, path):
    return next(
        route.endpoint
        for route in client._transport.app.routes
        if getattr(route, "path", None) == path
    )


async def test_metrics_endpoint_is_opt_in_and_token_protected(client, monkeypatch):
    settings = Settings(_env_file=None, metrics_enabled=False)
    monkeypatch.setattr(metrics_route, "get_settings", lambda: settings)
    assert (await client.get("/metrics")).status_code == 404

    settings = Settings(_env_file=None, metrics_enabled=True)
    monkeypatch.setattr(metrics_route, "get_settings", lambda: settings)
    assert (await client.get("/metrics")).status_code == 200

    settings = Settings(
        _env_file=None, metrics_enabled=True, metrics_token="scrape-test-token"
    )
    monkeypatch.setattr(metrics_route, "get_settings", lambda: settings)
    assert (await client.get("/metrics")).status_code == 401
    assert (
        await client.get(
            "/metrics", headers={"Authorization": "Bearer wrong-token"}
        )
    ).status_code == 401

    response = await client.get(
        "/metrics", headers={"Authorization": "Bearer scrape-test-token"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for metric in (
        "gateway_requests_total",
        "gateway_request_duration_seconds",
        "gateway_ttft_seconds",
        "gateway_tokens_total",
        "gateway_cost_usd_total",
        "gateway_retries_total",
        "gateway_fallbacks_total",
        "gateway_rate_limited_total",
        "gateway_budget_rejected_total",
        "gateway_inflight_requests",
    ):
        assert metric in response.text
    assert "/metrics" not in (await client.get("/openapi.json")).json()["paths"]


async def test_request_id_is_propagated_or_generated(client):
    supplied = await client.get("/health", headers={"X-Request-ID": "trace-1234"})
    assert supplied.headers["X-Request-ID"] == "trace-1234"

    generated = await client.get("/health")
    UUID(generated.headers["X-Request-ID"], version=4)

    invalid = await client.get("/health", headers={"X-Request-ID": "invalid id"})
    UUID(invalid.headers["X-Request-ID"], version=4)


async def test_completion_log_shape_and_secret_redaction(
    client, api_key, monkeypatch, caplog
):
    from app.observability import StructuredFormatter, configure_logging

    endpoint = _route_endpoint(client, "/v1/chat/completions")
    routing_globals = endpoint.__globals__["run_complete"].__globals__
    configure_logging(Settings(_env_file=None, log_level="INFO", log_format="json"))

    async def fake_acomplete(request, model, timeout):
        return _completion(model)

    monkeypatch.setitem(routing_globals, "acomplete", fake_acomplete)
    monkeypatch.setitem(
        routing_globals, "cost_of", lambda model, prompt, completion: 0.001
    )
    monkeypatch.setitem(endpoint.__globals__, "get_settings", lambda: Settings(_env_file=None))

    request_id = "safe-request-id"
    body_secret = "BODY_SECRET_SENTINEL"
    header_secret = "HEADER_SECRET_SENTINEL"
    caplog.set_level(logging.INFO, logger="app.request")
    caplog.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Request-ID": request_id,
            "X-Private-Header": header_secret,
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": body_secret}],
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id

    completion_logs = [
        record for record in caplog.records
        if record.getMessage() == "request_completed"
    ]
    assert len(completion_logs) == 1
    log = completion_logs[0]
    assert log.request_id == request_id
    assert log.provider == "openai"
    assert log.model == "gpt-4o-mini"
    assert log.status == "ok"
    assert isinstance(log.latency_ms, int)
    assert log.tokens == {"input": 2, "output": 3, "total": 5}
    assert log.cost_usd == 0.001
    assert log.attempts == 1
    assert isinstance(log.api_key_id, int)

    serialized = StructuredFormatter().format(log)
    assert set(json.loads(serialized)) >= {
        "timestamp",
        "level",
        "logger",
        "message",
        "request_id",
        "provider",
        "model",
        "status",
        "latency_ms",
        "tokens",
        "cost_usd",
        "attempts",
        "api_key_id",
    }
    assert body_secret not in serialized
    assert header_secret not in serialized
    assert api_key not in serialized
    assert body_secret not in caplog.text
    assert header_secret not in caplog.text
    assert api_key not in caplog.text


async def test_upstream_error_is_logged_but_client_gets_generic_message(
    client, api_key, monkeypatch, caplog
):
    from app.observability import StructuredFormatter

    endpoint = _route_endpoint(client, "/v1/chat/completions")
    routing_globals = endpoint.__globals__["run_complete"].__globals__
    error_text = "upstream-private-diagnostic"
    prompt_secret = "PRIVATE_PROMPT_SENTINEL"

    async def fail_completion(request, model, timeout):
        raise RuntimeError(f"{error_text}: {prompt_secret}")

    monkeypatch.setitem(routing_globals, "acomplete", fail_completion)
    request_id = "correlate-error-1"
    caplog.set_level(logging.INFO, logger="app.request")
    caplog.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Request-ID": request_id,
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt_secret}],
        },
    )
    assert response.status_code == 502
    assert error_text not in response.text
    assert response.json()["error"]["request_id"] == request_id
    assert response.headers["X-Request-ID"] == request_id

    completion_logs = [
        record for record in caplog.records
        if record.getMessage() == "request_completed"
    ]
    assert len(completion_logs) == 1
    serialized = StructuredFormatter().format(completion_logs[0])
    logged_error = json.loads(serialized)["error"]
    assert error_text in logged_error
    assert prompt_secret not in logged_error


async def test_stream_emits_exactly_one_completion_log(
    client, api_key, monkeypatch, caplog
):
    endpoint = _route_endpoint(client, "/v1/chat/completions")
    routing_globals = endpoint.__globals__["open_stream"].__globals__
    async def fake_stream(request, model, timeout):
        yield {"choices": [{"index": 0, "delta": {"content": "hello"}}]}
        yield {"choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

    monkeypatch.setitem(routing_globals, "astream", fake_stream)
    request_id = "single-stream-completion"
    caplog.set_level(logging.INFO, logger="app.request")
    caplog.clear()
    response = await client.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "X-Request-ID": request_id,
        },
        json={
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
    )
    assert response.status_code == 200
    assert "[DONE]" in response.text
    completion_logs = [
        record for record in caplog.records
        if record.getMessage() == "request_completed"
    ]
    assert len(completion_logs) == 1
    assert completion_logs[0].request_id == request_id
    assert completion_logs[0].status == "ok"


async def test_request_error_retry_and_fallback_metrics(client, api_key, monkeypatch):
    aliases = {
        "success-model": "gpt-4o-mini",
        "error-model": "gpt-4o-mini",
        "retry-model": "gpt-4o-mini",
        "primary-model": "gpt-4o-mini",
    }
    settings = Settings(
        _env_file=None, max_retries=1, model_aliases=json.dumps(aliases)
    )
    monkeypatch.setattr(chat_route, "get_settings", lambda: settings)
    attempts = {}

    async def fake_acomplete(request, model, timeout):
        attempts[request.model] = attempts.get(request.model, 0) + 1
        if request.model == "error-model":
            raise RuntimeError("upstream unavailable")
        if request.model == "retry-model" and attempts[request.model] == 1:
            raise RuntimeError("retry")
        if request.model == "primary-model" and model == "gpt-4o-mini":
            raise RuntimeError("primary unavailable")
        return _completion(model)

    monkeypatch.setattr(routing, "acomplete", fake_acomplete)
    monkeypatch.setattr(
        routing, "is_retryable", lambda exc: str(exc) == "retry"
    )
    monkeypatch.setattr(routing, "cost_of", lambda model, prompt, completion: 0.02)
    headers = {"Authorization": f"Bearer {api_key}"}

    success_labels = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "status": "ok",
        "streamed": "false",
    }
    input_labels = {"provider": "openai", "model": "gpt-4o-mini", "type": "input"}
    output_labels = {"provider": "openai", "model": "gpt-4o-mini", "type": "output"}
    duration_labels = {"provider": "openai", "model": "gpt-4o-mini"}
    before_success = _sample(
        metrics.REGISTRY, "gateway_requests_total", success_labels
    )
    before_input = _sample(metrics.REGISTRY, "gateway_tokens_total", input_labels)
    before_output = _sample(metrics.REGISTRY, "gateway_tokens_total", output_labels)
    before_cost = _sample(
        metrics.REGISTRY,
        "gateway_cost_usd_total",
        {"provider": "openai", "model": "gpt-4o-mini"},
    )
    before_duration = _sample(
        metrics.REGISTRY, "gateway_request_duration_seconds_count", duration_labels
    )
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o-mini", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert _sample(metrics.REGISTRY, "gateway_requests_total", success_labels) == (
        before_success + 1
    )
    assert _sample(metrics.REGISTRY, "gateway_tokens_total", input_labels) == (
        before_input + 2
    )
    assert _sample(metrics.REGISTRY, "gateway_tokens_total", output_labels) == (
        before_output + 3
    )
    assert _sample(
        metrics.REGISTRY,
        "gateway_cost_usd_total",
        {"provider": "openai", "model": "gpt-4o-mini"},
    ) == pytest.approx(before_cost + 0.02)
    assert _sample(
        metrics.REGISTRY, "gateway_request_duration_seconds_count", duration_labels
    ) == before_duration + 1

    error_labels = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "status": "error",
        "streamed": "false",
    }
    before_error = _sample(metrics.REGISTRY, "gateway_requests_total", error_labels)
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "error-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 502
    assert _sample(metrics.REGISTRY, "gateway_requests_total", error_labels) == (
        before_error + 1
    )

    retry_labels = {"provider": "openai", "reason": "other"}
    before_retry = _sample(metrics.REGISTRY, "gateway_retries_total", retry_labels)
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "retry-model", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert response.status_code == 200
    assert _sample(metrics.REGISTRY, "gateway_retries_total", retry_labels) == (
        before_retry + 1
    )

    fallback_labels = {
        "from_model": "gpt-4o-mini",
        "to_model": "gemini/gemini-1.5-flash",
    }
    before_fallback = _sample(
        metrics.REGISTRY, "gateway_fallbacks_total", fallback_labels
    )
    response = await client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "primary-model",
            "messages": [{"role": "user", "content": "hi"}],
            "fallback_models": ["gemini-1.5-flash"],
        },
    )
    assert response.status_code == 200
    assert _sample(metrics.REGISTRY, "gateway_fallbacks_total", fallback_labels) == (
        before_fallback + 1
    )


async def test_rate_limit_and_budget_rejection_metrics(client, monkeypatch, caplog):
    async def fake_acomplete(request, model, timeout):
        return _completion(model)

    monkeypatch.setattr(routing, "acomplete", fake_acomplete)
    monkeypatch.setattr(routing, "cost_of", lambda model, prompt, completion: 0.01)
    admin = {"Authorization": "Bearer test-admin"}
    rate_key = await client.post(
        "/admin/keys",
        headers=admin,
        json={"name": "metric-rate-limit", "rate_limit_per_min": 1},
    )
    assert rate_key.status_code == 201
    headers = {"Authorization": f"Bearer {rate_key.json()['api_key']}"}
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert (
        await client.post("/v1/chat/completions", headers=headers, json=payload)
    ).status_code == 200
    before_rate_limit = _sample(metrics.REGISTRY, "gateway_rate_limited_total")
    caplog.set_level(logging.INFO, logger="app.request")
    caplog.clear()
    limited = await client.post("/v1/chat/completions", headers=headers, json=payload)
    assert limited.status_code == 429
    assert _sample(metrics.REGISTRY, "gateway_rate_limited_total") == (
        before_rate_limit + 1
    )
    rate_limit_logs = [
        record for record in caplog.records
        if record.getMessage() == "request_completed"
    ]
    assert len(rate_limit_logs) == 1
    assert rate_limit_logs[0].status == "rate_limited"

    budget_key = await client.post(
        "/admin/keys",
        headers=admin,
        json={"name": "metric-budget", "monthly_budget_usd": 0.005},
    )
    assert budget_key.status_code == 201
    budget_headers = {"Authorization": f"Bearer {budget_key.json()['api_key']}"}
    budget_payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
    }
    assert (
        await client.post(
            "/v1/chat/completions", headers=budget_headers, json=budget_payload
        )
    ).status_code == 200
    before_budget = _sample(metrics.REGISTRY, "gateway_budget_rejected_total")
    caplog.clear()
    rejected = await client.post(
        "/v1/chat/completions", headers=budget_headers, json=budget_payload
    )
    assert rejected.status_code == 402
    assert _sample(metrics.REGISTRY, "gateway_budget_rejected_total") == (
        before_budget + 1
    )
    budget_logs = [
        record for record in caplog.records
        if record.getMessage() == "request_completed"
    ]
    assert len(budget_logs) == 1
    assert budget_logs[0].status == "budget_rejected"


async def test_stream_metrics_record_partial_and_client_disconnect(
    client, api_key, monkeypatch
):
    settings = Settings(_env_file=None, max_retries=0)
    request = ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    first_chunk = {"choices": [{"index": 0, "delta": {"content": "partial"}}]}

    async def failing_stream(request, model, timeout):
        yield first_chunk
        raise RuntimeError("stream interrupted")

    monkeypatch.setattr(routing, "astream", failing_stream)
    monkeypatch.setattr(routing, "cost_of", lambda model, prompt, completion: 0.0)
    labels = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "status": "partial",
        "streamed": "true",
    }
    before_partial = _sample(metrics.REGISTRY, "gateway_requests_total", labels)
    ttft_labels = {"provider": "openai", "model": "gpt-4o-mini"}
    before_ttft = _sample(
        metrics.REGISTRY, "gateway_ttft_seconds_count", ttft_labels
    )
    handle = await routing.open_stream(request, settings, {}, 1)
    assert _sample(
        metrics.REGISTRY, "gateway_ttft_seconds_count", ttft_labels
    ) == before_ttft + 1
    assert _sample(metrics.REGISTRY, "gateway_inflight_requests") == 1
    body = routing.stream_body(handle, request, 1)
    assert (await body.__anext__()).startswith("data: ")
    with pytest.raises(RuntimeError, match="stream interrupted"):
        await body.__anext__()
    assert _sample(metrics.REGISTRY, "gateway_requests_total", labels) == (
        before_partial + 1
    )

    async def open_stream(request, model, timeout):
        yield first_chunk
        yield {"choices": [{"index": 0, "delta": {"content": "later"}}]}

    monkeypatch.setattr(routing, "astream", open_stream)
    labels = {
        "provider": "gemini",
        "model": "gemini/gemini-1.5-flash",
        "status": "client_disconnect",
        "streamed": "true",
    }
    request = ChatCompletionRequest(
        model="gemini-1.5-flash",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    before_disconnect = _sample(metrics.REGISTRY, "gateway_requests_total", labels)
    handle = await routing.open_stream(request, settings, {}, 1)
    body = routing.stream_body(handle, request, 1)
    assert (await body.__anext__()).startswith("data: ")
    await body.aclose()
    assert _sample(metrics.REGISTRY, "gateway_requests_total", labels) == (
        before_disconnect + 1
    )
    assert _sample(metrics.REGISTRY, "gateway_inflight_requests") == 0


async def test_stream_open_client_disconnect_is_counted(client, monkeypatch):
    settings = Settings(_env_file=None, max_retries=0)
    request = ChatCompletionRequest(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )

    async def stalled_stream(request, model, timeout):
        await asyncio.Event().wait()
        yield {}

    monkeypatch.setattr(routing, "astream", stalled_stream)
    labels = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "status": "client_disconnect",
        "streamed": "true",
    }
    before = _sample(metrics.REGISTRY, "gateway_requests_total", labels)
    task = asyncio.create_task(routing.open_stream(request, settings, {}, 1))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert _sample(metrics.REGISTRY, "gateway_requests_total", labels) == before + 1
    assert _sample(metrics.REGISTRY, "gateway_inflight_requests") == 0


def test_model_label_cardinality_is_capped():
    from app.observability import (
        MAX_MODEL_LABELS,
        _ModelLabelLimiter,
    )

    limiter = _ModelLabelLimiter()
    concrete_labels = [f"provider/model-{index}" for index in range(MAX_MODEL_LABELS)]
    limiter.configure(concrete_labels[:2])
    limiter.seed_recent(concrete_labels[2:])
    labels = {limiter.label(model) for model in concrete_labels}
    labels.add(limiter.label("provider/overflow"))
    assert len(labels) == MAX_MODEL_LABELS
    assert limiter.label("provider/overflow") == "other"
    assert limiter.label("provider/model-0") == "provider/model-0"


def test_unconfigured_models_need_recent_request_admission():
    from app.observability import _ModelLabelLimiter

    limiter = _ModelLabelLimiter()
    limiter.configure(["openai/configured-model"])
    assert limiter.label("openai/configured-model") == "openai/configured-model"
    assert limiter.label("openai/unknown-model") == "other"
    limiter.remember("openai/recent-model")
    assert limiter.label("openai/recent-model") == "openai/recent-model"
