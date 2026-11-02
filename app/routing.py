"""Request orchestration: retries, fallback chain, cost calc, and usage logging.

Policy: for each candidate model we retry on transient errors up to
``max_retries``; on persistent failure we fall back to the next candidate.
Candidates = the primary model plus the request's ``fallback_models``, or — when
the client sends none — the server-side ``FALLBACK_CHAINS`` default for that
model. Streaming commits to a provider only once its first chunk arrives, so
fallback still works pre-stream.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from app.config import Settings
from app.db import SessionLocal
from app.engine import (
    acomplete,
    aembed,
    astream,
    cost_of,
    is_retryable,
    normalize_model,
    provider_of,
)
from app.models import RequestLog
from app.observability.logging import sanitize_error
from app.observability.metrics import (
    inflight,
    record_fallback,
    record_request,
    record_retry,
    record_ttft,
)
from app.schemas import ChatCompletionRequest, EmbeddingRequest


class UpstreamError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _status_of(exc: Exception) -> int:
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else 502


def _retry_reason(exc: Exception) -> str:
    status_code = getattr(exc, "status_code", None)
    if status_code == 429:
        return "rate_limit"
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    if isinstance(status_code, int) and status_code >= 500:
        return "server_error"
    return "other"


def _candidates(
    request: ChatCompletionRequest | EmbeddingRequest,
    settings: Settings,
    aliases: dict[str, str],
    *,
    wildcard: bool = True,
) -> list[str]:
    """Primary model + fallbacks, including explicit ``groq/model`` names,
    deduped on their normalized form.

    Client-supplied ``fallback_models`` win outright; otherwise the server-side
    ``FALLBACK_CHAINS`` entry for the normalized primary applies — exact model
    key first, then the ``provider/*`` wildcard. Embeddings opt out of the
    wildcard (``wildcard=False``) so a provider-wide chat chain never routes an
    embedding request to a chat model.
    """
    primary = normalize_model(request.model, aliases)
    fallbacks = list(request.fallback_models or [])
    if not fallbacks:
        chains = settings.fallback_chain_map
        fallbacks = list(chains.get(primary) or [])
        if not fallbacks and wildcard:
            fallbacks = list(chains.get(f"{provider_of(primary)}/*") or [])
    models = [request.model]
    seen = {primary}
    for m in fallbacks:
        normalized = normalize_model(m, aliases)
        if normalized not in seen:
            seen.add(normalized)
            models.append(m)
    return models


async def _log(**fields) -> None:
    async with SessionLocal() as session:
        session.add(RequestLog(**fields))
        await session.commit()


def _usage_fields(usage: dict | None) -> tuple[int, int, int]:
    usage = usage or {}
    pt = usage.get("prompt_tokens") or 0
    ct = usage.get("completion_tokens") or 0
    tt = usage.get("total_tokens") or (pt + ct)
    return pt, ct, tt


# --- Non-streaming -------------------------------------------------------
async def _run_complete(
    request: ChatCompletionRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> dict:
    request_start = time.perf_counter()
    attempts = 0
    last_exc: Exception | None = None
    candidates = _candidates(request, settings, aliases)
    final_model = normalize_model(request.model, aliases)

    for candidate_index, raw_model in enumerate(candidates):
        model = normalize_model(raw_model, aliases)
        final_model = model
        for attempt in range(settings.max_retries + 1):
            attempts += 1
            try:
                resp = await acomplete(request, model, settings.request_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if is_retryable(exc) and attempt < settings.max_retries:
                    record_retry(provider_of(model), _retry_reason(exc))
                    continue
                break  # try next candidate model

            duration_seconds = time.perf_counter() - request_start
            latency_ms = int(duration_seconds * 1000)
            pt, ct, tt = _usage_fields(resp.get("usage"))
            provider = provider_of(model)
            cost = cost_of(model, pt, ct)
            record_request(
                provider=provider,
                model=model,
                status="ok",
                streamed=False,
                duration_seconds=duration_seconds,
                input_tokens=pt,
                output_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                attempts=attempts,
                api_key_id=api_key_id,
            )
            await _log(
                api_key_id=api_key_id,
                provider=provider,
                model=model,
                requested_model=request.model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                latency_ms=latency_ms,
                streamed=False,
                status="ok",
                attempts=attempts,
            )
            resp["gateway"] = {
                "provider": provider,
                "resolved_model": model,
                "cost_usd": cost,
                "latency_ms": latency_ms,
                "attempts": attempts,
            }
            return resp
        if candidate_index + 1 < len(candidates):
            record_fallback(model, normalize_model(candidates[candidate_index + 1], aliases))

    message = sanitize_error(
        str(last_exc) if last_exc else "all candidates failed",
        request,
    )
    duration_seconds = time.perf_counter() - request_start
    record_request(
        provider=provider_of(final_model),
        model=final_model,
        status="error",
        streamed=False,
        duration_seconds=duration_seconds,
        attempts=attempts,
        api_key_id=api_key_id,
        error=message,
    )
    await _log(
        api_key_id=api_key_id,
        provider=provider_of(final_model),
        model=final_model,
        requested_model=request.model,
        status="error",
        attempts=attempts,
        error=message,
    )
    raise UpstreamError(message, _status_of(last_exc) if last_exc else 502)


async def run_complete(
    request: ChatCompletionRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> dict:
    with inflight():
        return await _run_complete(request, settings, aliases, api_key_id)


# --- Streaming -----------------------------------------------------------
class _StreamHandle:
    def __init__(self, gen, first, model, attempts, start, ttft_start):
        self.gen = gen
        self.first = first
        self.model = model
        self.attempts = attempts
        self.start = start
        self.ttft_start = ttft_start
        self.inflight_context = None


async def _open_stream(
    request: ChatCompletionRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> _StreamHandle:
    request_start = time.perf_counter()
    attempts = 0
    last_exc: Exception | None = None
    candidates = _candidates(request, settings, aliases)
    final_model = normalize_model(request.model, aliases)
    attempt_start = request_start

    for candidate_index, raw_model in enumerate(candidates):
        model = normalize_model(raw_model, aliases)
        final_model = model
        for attempt in range(settings.max_retries + 1):
            attempts += 1
            attempt_start = time.perf_counter()
            gen = astream(request, model, settings.request_timeout_seconds)
            try:
                first = await gen.__anext__()
            except StopAsyncIteration:
                return _StreamHandle(
                    None, None, model, attempts, request_start, attempt_start
                )
            except asyncio.CancelledError:
                record_request(
                    provider=provider_of(model),
                    model=model,
                    status="client_disconnect",
                    streamed=True,
                    duration_seconds=time.perf_counter() - request_start,
                    attempts=attempts,
                    api_key_id=api_key_id,
                )
                raise
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await gen.aclose()
                if is_retryable(exc) and attempt < settings.max_retries:
                    record_retry(provider_of(model), _retry_reason(exc))
                    continue
                break
            record_ttft(provider_of(model), model, time.perf_counter() - attempt_start)
            return _StreamHandle(
                gen, first, model, attempts, request_start, attempt_start
            )
        if candidate_index + 1 < len(candidates):
            record_fallback(model, normalize_model(candidates[candidate_index + 1], aliases))

    message = sanitize_error(
        str(last_exc) if last_exc else "all candidates failed",
        request,
    )
    duration_seconds = time.perf_counter() - request_start
    record_request(
        provider=provider_of(final_model),
        model=final_model,
        status="error",
        streamed=True,
        duration_seconds=duration_seconds,
        attempts=attempts,
        api_key_id=api_key_id,
        error=message,
    )
    await _log(
        api_key_id=api_key_id,
        provider=provider_of(final_model),
        model=final_model,
        requested_model=request.model,
        status="error",
        attempts=attempts,
        error=message,
    )
    raise UpstreamError(message, _status_of(last_exc) if last_exc else 502)


async def open_stream(
    request: ChatCompletionRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> _StreamHandle:
    context = inflight()
    context.__enter__()
    try:
        handle = await _open_stream(request, settings, aliases, api_key_id)
    except BaseException as exc:
        context.__exit__(type(exc), exc, exc.__traceback__)
        raise
    handle.inflight_context = context
    return handle


async def stream_body(
    handle: _StreamHandle,
    request: ChatCompletionRequest,
    api_key_id: int,
) -> AsyncIterator[str]:
    """Yields SSE lines for an already-resolved stream and logs usage at the end.
    Call ``open_stream`` first so total failures raise before the response starts."""
    pt = ct = tt = 0

    def capture(chunk: dict) -> None:
        nonlocal pt, ct, tt
        if chunk.get("usage"):
            pt, ct, tt = _usage_fields(chunk["usage"])

    request_status = "ok"
    error: BaseException | None = None
    error_info = (None, None, None)
    completed = False
    observed_chunks: list[dict] = []
    try:
        if handle.first is not None:
            capture(handle.first)
            observed_chunks.append(handle.first)
            yield f"data: {json.dumps(handle.first)}\n\n"
        if handle.gen is not None:
            async for chunk in handle.gen:
                capture(chunk)
                observed_chunks.append(chunk)
                yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"
        completed = True
    except asyncio.CancelledError as exc:
        request_status = "client_disconnect"
        error = exc
        error_info = (type(exc), exc, exc.__traceback__)
        raise
    except GeneratorExit as exc:
        request_status = "client_disconnect"
        error = exc
        error_info = (type(exc), exc, exc.__traceback__)
        raise
    except Exception as exc:
        request_status = "partial"
        error = exc
        error_info = (type(exc), exc, exc.__traceback__)
        raise
    finally:
        duration_seconds = time.perf_counter() - handle.start
        latency_ms = int(duration_seconds * 1000)
        cost = cost_of(handle.model, pt, ct)
        metric_status = "ok" if completed else request_status
        record_request(
            provider=provider_of(handle.model),
            model=handle.model,
            status=metric_status,
            streamed=True,
            duration_seconds=duration_seconds,
            input_tokens=pt,
            output_tokens=ct,
            total_tokens=tt or (pt + ct),
            cost_usd=cost,
            attempts=handle.attempts,
            api_key_id=api_key_id,
            error=(
                None
                if completed
                else sanitize_error(
                    str(error or "stream interrupted"),
                    request,
                    observed_chunks,
                )
            ),
        )
        try:
            await _log(
                api_key_id=api_key_id,
                provider=provider_of(handle.model),
                model=handle.model,
                requested_model=request.model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt or (pt + ct),
                cost_usd=cost,
                latency_ms=latency_ms,
                streamed=True,
                status="ok" if completed else "error",
                attempts=handle.attempts,
                error=None if completed else str(error or "stream interrupted")[:2000],
            )
        finally:
            if handle.inflight_context is not None:
                handle.inflight_context.__exit__(*error_info)


# --- Embeddings ----------------------------------------------------------
async def _run_embed(
    request: EmbeddingRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> dict:
    """Embeddings with the same retry + fallback + logging policy as completions.

    Embedding usage reports only prompt tokens (no completion), so completion
    cost is zero; ``cost_of`` handles that. Logged to ``RequestLog`` like any
    other request, so it shows up in /api/stats unchanged.
    """
    request_start = time.perf_counter()
    attempts = 0
    last_exc: Exception | None = None
    candidates = _candidates(request, settings, aliases, wildcard=False)
    final_model = normalize_model(request.model, aliases)

    for candidate_index, raw_model in enumerate(candidates):
        model = normalize_model(raw_model, aliases)
        final_model = model
        for attempt in range(settings.max_retries + 1):
            attempts += 1
            try:
                resp = await aembed(request, model, settings.request_timeout_seconds)
            except Exception as exc:  # noqa: BLE001 — classified below
                last_exc = exc
                if is_retryable(exc) and attempt < settings.max_retries:
                    record_retry(provider_of(model), _retry_reason(exc))
                    continue
                break  # try next candidate model

            duration_seconds = time.perf_counter() - request_start
            latency_ms = int(duration_seconds * 1000)
            pt, ct, tt = _usage_fields(resp.get("usage"))
            provider = provider_of(model)
            cost = cost_of(model, pt, ct)
            record_request(
                provider=provider,
                model=model,
                status="ok",
                streamed=False,
                duration_seconds=duration_seconds,
                input_tokens=pt,
                output_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                attempts=attempts,
                api_key_id=api_key_id,
            )
            await _log(
                api_key_id=api_key_id,
                provider=provider,
                model=model,
                requested_model=request.model,
                prompt_tokens=pt,
                completion_tokens=ct,
                total_tokens=tt,
                cost_usd=cost,
                latency_ms=latency_ms,
                streamed=False,
                status="ok",
                attempts=attempts,
            )
            resp["gateway"] = {
                "provider": provider,
                "resolved_model": model,
                "cost_usd": cost,
                "latency_ms": latency_ms,
                "attempts": attempts,
            }
            return resp
        if candidate_index + 1 < len(candidates):
            record_fallback(model, normalize_model(candidates[candidate_index + 1], aliases))

    message = sanitize_error(
        str(last_exc) if last_exc else "all candidates failed",
        request,
    )
    duration_seconds = time.perf_counter() - request_start
    record_request(
        provider=provider_of(final_model),
        model=final_model,
        status="error",
        streamed=False,
        duration_seconds=duration_seconds,
        attempts=attempts,
        api_key_id=api_key_id,
        error=message,
    )
    await _log(
        api_key_id=api_key_id,
        provider=provider_of(final_model),
        model=final_model,
        requested_model=request.model,
        status="error",
        attempts=attempts,
        error=message,
    )
    raise UpstreamError(message, _status_of(last_exc) if last_exc else 502)


async def run_embed(
    request: EmbeddingRequest,
    settings: Settings,
    aliases: dict[str, str],
    api_key_id: int,
) -> dict:
    with inflight():
        return await _run_embed(request, settings, aliases, api_key_id)
