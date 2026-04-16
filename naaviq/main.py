"""Naaviq — open-source voice provider registry API."""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text

from naaviq.db import engine
from naaviq.limiter import limiter
from naaviq.routers import providers

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — verify DB is reachable
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        log.info("db_connected")
    except Exception as exc:
        log.error("db_connection_failed", error=str(exc))
        raise
    yield
    # Shutdown
    await engine.dispose()


app = FastAPI(
    title="Naaviq Voice Providers",
    description="Open-source voice provider registry — STT/TTS models and voices",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — public read-only API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(providers.router, prefix="/v1")


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok"}
