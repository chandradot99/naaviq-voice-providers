FROM python:3.12-slim

# uv for fast dep resolution + lockfile-based installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dep manifests first — better layer caching, rebuilds only when deps change
COPY pyproject.toml uv.lock ./

# Application code
COPY naaviq/ naaviq/
COPY alembic/ alembic/
COPY alembic.ini ./

# Non-root user, owns /app before uv sync so the .venv is writable at runtime
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Base deps only — no dev, no sync extras (public API doesn't need anthropic/provider SDKs)
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Shell form so $PORT expands at runtime (Railway injects PORT)
CMD ["sh", "-c", "exec uv run uvicorn naaviq.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
