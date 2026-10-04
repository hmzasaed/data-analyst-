"""Uvicorn entry point for the InsightFlow API."""

from __future__ import annotations

import os

import uvicorn
from insightflow_contracts import get_settings


def main() -> None:
    # The CLI is what people run behind the web UI, which needs jobs to run in the
    # background so it can poll the lifecycle. Tests import the app directly instead.
    # An explicit legacy INSIGHTFLOW_INLINE_EXECUTION (docker-compose sets it to hand
    # jobs to the worker) still decides the mode.
    if "INSIGHTFLOW_INLINE_EXECUTION" not in os.environ:
        os.environ.setdefault("INSIGHTFLOW_EXECUTION_MODE", "background")
    settings = get_settings()
    uvicorn.run(
        "insightflow_api.service:app",
        host=os.getenv("INSIGHTFLOW_HOST", "127.0.0.1"),
        port=int(os.getenv("INSIGHTFLOW_PORT", "8000")),
        log_level=settings.log_level.lower(),
        reload=os.getenv("INSIGHTFLOW_RELOAD", "false").lower() in {"1", "true"},
    )


if __name__ == "__main__":
    main()