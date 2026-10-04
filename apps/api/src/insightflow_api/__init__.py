"""InsightFlow API application.

The FastAPI instance is exported here so `insightflow_api:app` works. The
implementation lives in `insightflow_api.service`, deliberately not `app`, so that
importing the module is never shadowed by the application object of the same name.

The export is lazy: `python -m insightflow_api` imports this package before its
`main()` sets the execution-mode default, and an eager import would read the
environment too early.
"""

from typing import Any

__all__ = ["app"]


def __getattr__(name: str) -> Any:
    if name == "app":
        from insightflow_api.service import app

        return app
    raise AttributeError(f"module 'insightflow_api' has no attribute {name!r}")
