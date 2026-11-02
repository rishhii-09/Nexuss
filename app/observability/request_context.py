"""Request-scoped context shared by middleware, logging, and instrumentation."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class RequestState:
    completed: bool = False


request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
request_state_var: ContextVar[RequestState | None] = ContextVar(
    "request_state", default=None
)


def get_request_id() -> str:
    return request_id_var.get()
