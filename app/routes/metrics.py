"""Prometheus exposition endpoint."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import get_settings
from app.observability.metrics import REGISTRY

router = APIRouter(tags=["metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    settings = get_settings()
    if not settings.metrics_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    if settings.metrics_token:
        authorization = request.headers.get("authorization", "")
        parts = authorization.split()
        if (
            len(parts) != 2
            or parts[0].lower() != "bearer"
            or not secrets.compare_digest(
                parts[1].encode("utf-8"), settings.metrics_token.encode("utf-8")
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or missing metrics token.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
