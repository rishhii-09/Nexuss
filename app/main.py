"""LLM Gateway — OpenAI-compatible multi-provider gateway with usage tracking."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.db import init_db
from app.engine import configure_keys
from app.observability.logging import configure_logging
from app.observability.metrics import configure_model_labels, configure_recent_model_labels
from app.observability.middleware import RequestIDMiddleware
from app.ratelimit import RateLimiter
from app.routes import chat, embeddings, health, keys, metrics, models, stats

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_keys(settings)
    await init_db()
    await configure_recent_model_labels()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings)
    configure_model_labels(settings)
    app = FastAPI(
        title="LLM Gateway",
        version="0.1.0",
        description="OpenAI-compatible gateway across OpenAI, Anthropic, and Gemini "
        "with cost + latency tracking.",
        lifespan=lifespan,
    )
    app.state.rate_limiter = RateLimiter()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIDMiddleware)

    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(embeddings.router)
    app.include_router(models.router)
    app.include_router(keys.router)
    app.include_router(stats.router)
    app.include_router(metrics.router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

        @app.get("/dashboard", include_in_schema=False)
        async def dashboard() -> FileResponse:
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
