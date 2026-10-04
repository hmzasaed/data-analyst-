"""Verification contracts: how a claim earns the right to appear in a report."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from insightflow_contracts.profile import StrictModel


class VerificationStatus(StrEnum):
    PASS = "pass"  # noqa: S105
    PASS_WITH_WARNING = "pass_with_warning"  # noqa: S105
    FAIL = "fail"
    SKIPPED = "skipped"


class VerificationCheck(StrictModel):
    check_id: str
    name: str
    status: VerificationStatus
    detail: str
    finding_id: str | None = None
    expected: str | None = None
    actual: str | None = None
    tolerance: float | None = None


class VerificationResult(StrictModel):
    """Aggregate verification outcome for a set of findings.

    `rejected_finding_ids` is what the report composer filters on: a finding not
    in `verified_finding_ids` and not in `rejected_finding_ids` is rendered with a
    visible 'awaiting verification' banner instead of being dropped silently.
    """

    verified_finding_ids: list[str] = Field(default_factory=list)
    rejected_finding_ids: list[str] = Field(default_factory=list)
    checks: list[VerificationCheck] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    summary: str | None = None
    verifier_model: str | None = None
    verifier_version: str = "1"

    @property
    def failed_checks(self) -> list[VerificationCheck]:
        return [check for check in self.checks if check.status is VerificationStatus.FAIL]

    @property
    def passed(self) -> bool:
        return not self.failed_checks

    @property
    def warning_checks(self) -> list[VerificationCheck]:
        return [c for c in self.checks if c.status is VerificationStatus.PASS_WITH_WARNING]
