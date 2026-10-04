# API and worker image.
#
# The analysis stack (numpy, scipy, matplotlib, polars) and the HTTP stack share
# this image because the API can run the pipeline inline during development. In
# production the worker is the only component that executes analyses, and the API
# image can be built without the sandbox tooling.
# --- Stage 1: build the web UI -------------------------------------------------
FROM node:20-bookworm-slim AS web
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web/ ./
RUN npm run build

# --- Stage 2: API and worker runtime ---------------------------------------------
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN groupadd --gid 10001 insightflow \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin insightflow

WORKDIR /app

# Dependency layer first for build caching.
COPY pyproject.toml uv.lock ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/ingestion/pyproject.toml packages/ingestion/
COPY packages/profiling/pyproject.toml packages/profiling/
COPY packages/cleaning/pyproject.toml packages/cleaning/
COPY packages/analytics/pyproject.toml packages/analytics/
COPY packages/anomaly/pyproject.toml packages/anomaly/
COPY packages/visualization/pyproject.toml packages/visualization/
COPY packages/sandbox/pyproject.toml packages/sandbox/
COPY packages/agents/pyproject.toml packages/agents/
COPY packages/reporting/pyproject.toml packages/reporting/
COPY packages/storage/pyproject.toml packages/storage/
COPY packages/orchestration/pyproject.toml packages/orchestration/
COPY apps/api/pyproject.toml apps/api/
COPY apps/worker/pyproject.toml apps/worker/

RUN uv sync --frozen --no-install-project --no-dev \
    || uv sync --no-install-project --no-dev

COPY prompts prompts/
COPY packages packages/
COPY apps apps/
# The built UI; the API serves it at / when this directory exists.
COPY --from=web /web/dist apps/web/dist

RUN uv sync --frozen --no-dev || uv sync --no-dev

# `uv sync` installs into /app/.venv; without this the system interpreter (which has
# none of the dependencies) would run the CMD below.
ENV VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

ENV INSIGHTFLOW_ARTIFACT_ROOT=/app/artifacts \
    INSIGHTFLOW_ENVIRONMENT=production \
    INSIGHTFLOW_HOST=0.0.0.0 \
    INSIGHTFLOW_PORT=8000 \
    INSIGHTFLOW_WEB_DIST=/app/apps/web/dist \
    PYTHONPATH=/app/packages/contracts/src:/app/packages/ingestion/src:/app/packages/profiling/src:/app/packages/cleaning/src:/app/packages/preprocessing/src:/app/packages/features/src:/app/packages/analytics/src:/app/packages/anomaly/src:/app/packages/visualization/src:/app/packages/sandbox/src:/app/packages/agents/src:/app/packages/reporting/src:/app/packages/storage/src:/app/packages/orchestration/src:/app/apps/api/src:/app/apps/worker/src

RUN mkdir -p /app/artifacts && chown -R insightflow:insightflow /app

USER insightflow
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "insightflow_api"]