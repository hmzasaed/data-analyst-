"""Typed error taxonomy.

Every failure the platform can produce is one of these, so the orchestrator can
map errors onto retry / fail-safe / ask-the-user policies without string
matching. `retryable` is a property rather than a field because retry policy
depends on where the error came from, not on the class alone.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class InsightFlowError(Exception):
    """Base class for all InsightFlow failures.

    `detail` is a stable machine-readable dict safe to log and to show in the
    audit trail. It must never contain dataset cell values.
    """

    code: str = "insightflow_error"

    def __init__(self, message: str, **detail: Any) -> None:
        super().__init__(message)
        self.message = message
        self.detail: dict[str, Any] = detail

    @property
    def retryable(self) -> bool:
        return False

    def to_payload(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"[{self.code}] {self.message}"


class UnsupportedInputError(InsightFlowError):
    """The uploaded file or requested question is outside supported scope.

    The platform must fail loudly with this error rather than degrade into a
    plausible-looking but fabricated answer.
    """

    code = "unsupported_input"

    def __init__(self, message: str, *, suggestions: list[str] | None = None, **detail: Any):
        super().__init__(message, suggestions=suggestions or [], **detail)
        self.suggestions = suggestions or []


class IngestionError(InsightFlowError):
    """A file could not be safely parsed."""

    code = "ingestion_error"


class PlanRejectedError(InsightFlowError):
    """A proposed plan failed schema or capability validation before execution."""

    code = "plan_rejected"

    @property
    def violations(self) -> list[str]:
        return list(self.detail.get("violations", []))


class ToolExecutionError(InsightFlowError):
    """A registered analysis tool failed.

    Retryable because most tool failures are transient (resource pressure,
    degenerate input such as a single-row group).
    """

    code = "tool_execution_error"

    @property
    def retryable(self) -> bool:
        return bool(self.detail.get("retryable", True))


class SandboxViolationError(InsightFlowError):
    """Generated code attempted a forbidden operation.

    Never retried: a violation indicates the code or the environment is hostile.
    """

    code = "sandbox_violation"

    @property
    def retryable(self) -> bool:
        return False


class VerificationFailedError(InsightFlowError):
    """A report claim failed verification and must not be published."""

    code = "verification_failed"

    def __init__(self, message: str, failed_checks: list[BaseModel] | None = None, **detail: Any):
        super().__init__(message, failed_checks=len(failed_checks or []), **detail)
        self.failed_checks = failed_checks or []
