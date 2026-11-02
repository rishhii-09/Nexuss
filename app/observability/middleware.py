"""ASGI middleware for request IDs and unhandled gateway completion events."""
from __future__ import annotations

import re
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.observability.metrics import _record_unhandled_gateway_request
from app.observability.request_context import RequestState, request_id_var, request_state_var

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestIDMiddleware:
    """Propagate a safe request ID through async request handling and responses."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        supplied_id = headers.get(b"x-request-id", b"").decode("ascii", "ignore")
        correlation_id = (
            supplied_id if _REQUEST_ID_PATTERN.fullmatch(supplied_id) else str(uuid4())
        )
        request_token = request_id_var.set(correlation_id)
        request_state = RequestState()
        completion_token = request_state_var.set(request_state)
        started = perf_counter()
        response_status = 500

        async def send_with_request_id(message: dict) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"x-request-id"
                ]
                response_headers.append(
                    (b"x-request-id", correlation_id.encode("ascii"))
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            if (
                scope.get("path") in {"/v1/chat/completions", "/v1/embeddings"}
                and not request_state.completed
            ):
                _record_unhandled_gateway_request(
                    status="ok" if 200 <= response_status < 400 else "error",
                    duration_seconds=perf_counter() - started,
                )
            request_state_var.reset(completion_token)
            request_id_var.reset(request_token)
