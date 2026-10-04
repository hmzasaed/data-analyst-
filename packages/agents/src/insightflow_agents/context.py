"""Building the model-facing view of a dataset.

This is the privacy boundary. A profile, a handful of aggregates and a short,
redacted sample are safe to send to a third-party model; rows are not. The
`ModelContext` built here is the only thing agents are allowed to include in a
prompt, and it is assembled under a redaction policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from insightflow_contracts import (
    ColumnProfile,
    DatasetProfile,
    MetricValue,
    PiiRisk,
    RedactionPolicy,
    default_policy,
)

#: Free-text columns are never sampled for a model: a note field is exactly where
#: prompt injection and accidental personal data live.
FREE_TEXT_ROLES = {"free_text"}


@dataclass
class ModelContext:
    """Everything about a dataset that may leave the process."""

    dataset_id: str
    row_count: int
    column_count: int
    sheet_name: str | None = None
    time_coverage: str | None = None
    columns: list[dict[str, Any]] = field(default_factory=list)
    quality_issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    redactions_applied: int = 0
    rows_included: int = 0

    def to_json(self, *, indent: int | None = None) -> str:
        import json

        return json.dumps(_jsonable(self), indent=indent, default=str, sort_keys=True)

    def summary_line(self) -> str:
        coverage = f", {self.time_coverage}" if self.time_coverage else ""
        return (
            f"{self.row_count:,} rows x {self.column_count} columns{coverage} "
            f"({len(self.columns)} profiled)"
        )


def build_model_context(
    profile: DatasetProfile,
    *,
    metrics: list[MetricValue] | None = None,
    policy: RedactionPolicy | None = None,
    max_sample_values: int = 5,
) -> ModelContext:
    """Assemble a redacted, size-bounded view of `profile` for a model."""
    active = policy or default_policy()
    context = ModelContext(
        dataset_id=profile.dataset_id,
        row_count=profile.row_count,
        column_count=profile.column_count,
        sheet_name=profile.sheet_name,
        time_coverage=_coverage_line(profile),
        warnings=[warning.message for warning in profile.warnings],
    )

    for column in profile.columns:
        context.columns.append(_column_view(column, active, max_sample_values))

    for issue in profile.quality_issues:
        context.quality_issues.append(
            {
                "kind": issue.kind.value,
                "severity": issue.severity.value,
                "column": issue.column,
                "description": active.scrub(issue.description, column=issue.column),
                "affected_fraction": issue.affected_fraction,
            }
        )

    for metric in metrics or []:
        context.metrics.append(
            {
                "name": metric.name,
                "value": metric.value,
                "unit": metric.unit,
                "definition": metric.definition,
                "n": metric.n,
            }
        )

    context.redactions_applied = sum(
        1 for column in context.columns if column.get("pii_risk") in {"medium", "high"}
    )
    return context


def _column_view(
    column: ColumnProfile, policy: RedactionPolicy, max_samples: int
) -> dict[str, Any]:
    samples: list[str] = []
    is_free_text = column.semantic_role.value in FREE_TEXT_ROLES

    for value in column.sample_values[:max_samples]:
        if is_free_text and policy.redact_free_text_samples:
            samples.append("[free text withheld]")
            continue
        scrubbed = policy.scrub(
            value, column=column.name, pii_risk=column.pii_risk.value
        )
        if column.pii_risk in {PiiRisk.HIGH} and policy.mask_high_pii_columns:
            scrubbed = f"[{column.name} withheld: {column.pii_risk.value} PII risk]"
        samples.append(scrubbed[:80])

    return {
        "name": column.name,
        "type": column.inferred_type.value,
        "role": column.semantic_role.value,
        "null_share": column.null_fraction,
        "distinct": column.unique_count,
        "pii_risk": column.pii_risk.value,
        "parse_failure_share": column.parse_failure_fraction,
        "sample_values": samples,
    }


def _coverage_line(profile: DatasetProfile) -> str | None:
    coverage = profile.time_coverage
    if coverage is None or not coverage.min or not coverage.max:
        return None
    span = f", {coverage.span_days} days" if coverage.span_days is not None else ""
    grain = f", {coverage.granularity_guess} grain" if coverage.granularity_guess else ""
    return f"{coverage.min} to {coverage.max}{span}{grain}"


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, float | int | str | bool) or value is None:
        return value
    return str(value)