# The sandbox image is deliberately minimal and has no network access at runtime.
#
# It contains only the Python standard library plus the analysis packages that
# generated code is permitted to import. There is no shell, no compiler, no
# package manager and no network client, so there is nothing for generated code to
# reach for even if the AST checks were bypassed.
FROM python:3.11-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MPLBACKEND=Agg

# Non-root user; the runtime passes the same uid via --user as well.
RUN groupadd --gid 10001 sandbox \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin sandbox

WORKDIR /opt/insightflow

# Dependencies first, so a code change does not invalidate the dependency layer.
COPY pyproject.toml ./
COPY packages/contracts/pyproject.toml packages/contracts/
COPY packages/ingestion/pyproject.toml packages/ingestion/
COPY packages/analytics/pyproject.toml packages/analytics/
COPY packages/anomaly/pyproject.toml packages/anomaly/
COPY packages/visualization/pyproject.toml packages/visualization/

RUN pip install --no-cache-dir \
        "polars>=1.17" "numpy>=2.1" "scipy>=1.14" "matplotlib>=3.9" "pyarrow>=18.0"

COPY packages/contracts/src packages/contracts/src
COPY packages/ingestion/src packages/ingestion/src
COPY packages/anomaly/src packages/anomaly/src
COPY packages/visualization/src packages/visualization/src
COPY packages/analytics/src packages/analytics/src

ENV PYTHONPATH=/opt/insightflow/packages/contracts/src:/opt/insightflow/packages/ingestion/src:/opt/insightflow/packages/anomaly/src:/opt/insightflow/packages/visualization/src:/opt/insightflow/packages/analytics/src

# `/work/input` is bind-mounted read-only and `/work/output` read-write by the
# runner. The runner passes the harness, the safe API and the generated code in
# `input`, so nothing else needs to be writable.
RUN mkdir -p /work/input /work/output && chown -R sandbox:sandbox /work

USER sandbox
WORKDIR /work

# Default command; the runner overrides it with the harness path.
CMD ["python", "-I", "-B", "input/run.py"]