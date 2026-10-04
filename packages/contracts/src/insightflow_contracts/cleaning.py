"""Cleaning proposals and their audit trail.

Design rules encoded here:

* nothing is ever deleted implicitly - there is no `drop_rows` transform;
* every transform declares whether it is reversible and how to reverse it;
* anything the user has not approved stays `recommended` / `needs_review`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from insightflow_contracts.profile import Severity, StrictModel


class ReviewLevel(StrEnum):
    AUTOMATIC = "automatic"
    RECOMMENDED = "recommended"
    NEEDS_REVIEW = "needs_review"


class TransformKind(StrEnum):
    TRIM_WHITESPACE = "trim_whitespace"
    NORMALISE_CASE = "normalise_case"
    COLLAPSE_INTERNAL_WHITESPACE = "collapse_internal_whitespace"
    PARSE_DATETIME = "parse_datetime"
    PARSE_NUMBER = "parse_number"
    PARSE_BOOLEAN = "parse_boolean"
    DROP_EXACT_DUPLICATE_ROWS = "drop_exact_duplicate_rows"
    RENAME_COLUMN = "rename_column"
    FLAG_OUTLIER = "flag_outlier"
    FILL_NULL_WITH_CONSTANT = "fill_null_with_constant"
    FILL_NULL_WITH_MEDIAN = "fill_null_with_median"
    CLIP_RANGE = "clip_range"
    MASK_PII = "mask_pii"
    NORMALISE_CATEGORIES = "normalise_categories"
    KEEP_ORIGINAL_COLUMN = "keep_original_column"


#: Transforms the MVP is allowed to run without asking the user.
SAFE_AUTOMATIC: frozenset[TransformKind] = frozenset(
    {
        TransformKind.TRIM_WHITESPACE,
        TransformKind.NORMALISE_CASE,
        TransformKind.COLLAPSE_INTERNAL_WHITESPACE,
        TransformKind.PARSE_DATETIME,
        TransformKind.PARSE_NUMBER,
        TransformKind.PARSE_BOOLEAN,
        TransformKind.DROP_EXACT_DUPLICATE_ROWS,
    }
)

#: Transforms that must never be applied automatically.
NEVER_AUTOMATIC: frozenset[TransformKind] = frozenset(
    {
        TransformKind.FILL_NULL_WITH_MEDIAN,
        TransformKind.FILL_NULL_WITH_CONSTANT,
        TransformKind.CLIP_RANGE,
    }
)


class CleaningOperation(StrictModel):
    operation_id: str
    kind: TransformKind
    column: str | None = None
    parameters: dict[str, object] = Field(default_factory=dict)
    reason: str
    affected_rows: int = Field(default=0, ge=0)
    review_level: ReviewLevel = ReviewLevel.RECOMMENDED
    reversible: bool = True
    inverse_kind: TransformKind | None = None
    inverse_parameters: dict[str, object] = Field(default_factory=dict)
    inverse_note: str | None = None
    issue_kind: str | None = None
    impact_note: str | None = None
    accepted: bool | None = None

    @model_validator(mode="after")
    def _review_level_is_consistent(self) -> CleaningOperation:
        if self.kind in NEVER_AUTOMATIC and self.review_level is ReviewLevel.AUTOMATIC:
            raise ValueError(f"{self.kind.value} may never be automatic")
        if self.review_level is ReviewLevel.AUTOMATIC and self.kind not in SAFE_AUTOMATIC:
            raise ValueError(f"{self.kind.value} is not in the automatic allowlist")
        if self.accepted is True and self.review_level is not ReviewLevel.AUTOMATIC:
            return self
        return self

    @property
    def effective_status(self) -> ReviewLevel:
        if self.accepted is None:
            return self.review_level
        return ReviewLevel.AUTOMATIC if self.accepted else ReviewLevel.NEEDS_REVIEW


class CleaningPlan(StrictModel):
    """Proposed transformations for one dataset version."""

    dataset_id: str
    dataset_version: int = Field(ge=1)
    operations: list[CleaningOperation] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_file_hash: str | None = None

    def for_review(self) -> list[CleaningOperation]:
        return [op for op in self.operations if op.accepted is not True]

    def approved(self) -> list[CleaningOperation]:
        return [
            op
            for op in self.operations
            if op.accepted is True
            or (op.accepted is None and op.review_level is ReviewLevel.AUTOMATIC)
        ]

    def summary(self) -> CleaningPlanSummary:
        rows_touched = sum(op.affected_rows for op in self.approved())
        return CleaningPlanSummary(
            total_operations=len(self.operations),
            automatic=len([o for o in self.operations if o.review_level is ReviewLevel.AUTOMATIC]),
            needs_review=len([o for o in self.operations if o.review_level is not ReviewLevel.AUTOMATIC]),
            accepted=len(self.approved()),
            rows_touched=rows_touched,
        )


class CleaningPlanSummary(StrictModel):
    total_operations: int = 0
    automatic: int = 0
    needs_review: int = 0
    accepted: int = 0
    rows_touched: int = 0


class ChangeLogEntry(StrictModel):
    """One applied change, as shown in the before/after table in the report."""

    operation_id: str
    kind: TransformKind
    column: str | None = None
    rows_before: int = Field(ge=0)
    rows_after: int = Field(ge=0)
    rows_affected: int = Field(ge=0)
    null_count_before: int | None = Field(default=None, ge=0)
    null_count_after: int | None = Field(default=None, ge=0)
    reason: str
    reversible: bool
    review_level: ReviewLevel
    severity: Severity = Severity.LOW
    examples_before: list[str] = Field(default_factory=list)
    examples_after: list[str] = Field(default_factory=list)
    inverse_note: str | None = None

    @model_validator(mode="after")
    def _row_counts_are_consistent(self) -> ChangeLogEntry:
        if self.rows_affected < 0:
            raise ValueError("rows_affected cannot be negative")
        return self


class CleaningExecution(StrictModel):
    """Result of running an approved subset of a `CleaningPlan`."""

    dataset_id: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    applied: list[ChangeLogEntry] = Field(default_factory=list)
    rejected: list[CleaningOperation] = Field(default_factory=list)
    snapshot_artifact_id: str | None = None
    cleaned_artifact_id: str | None = None
    transform_digest: str | None = None

    @model_validator(mode="after")
    def _version_advances(self) -> CleaningExecution:
        if self.to_version <= self.from_version:
            raise ValueError("cleaned dataset version must advance past the source version")
        return self

    @property
    def total_rows_affected(self) -> int:
        return sum(entry.rows_affected for entry in self.applied)
