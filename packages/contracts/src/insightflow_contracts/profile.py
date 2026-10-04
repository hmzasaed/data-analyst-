"""`DatasetProfile` and the data-quality issue catalogue.

The profile is the only dataset-derived text that is ever allowed near a language
model, and it contains aggregates and short samples rather than full rows.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    """Base model: reject unknown keys so LLM output cannot inject extra fields."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


class InferredType(StrEnum):
    NUMBER = "number"
    INTEGER = "integer"
    DATE = "date"
    DATETIME = "datetime"
    CATEGORY = "category"
    TEXT = "text"
    BOOLEAN = "boolean"
    UNKNOWN = "unknown"


class SemanticRole(StrEnum):
    """Business meaning inferred from the column name and observed values."""

    IDENTIFIER = "identifier"
    MEASURE = "measure"
    DIMENSION = "dimension"
    TIME = "time"
    GEOGRAPHY = "geography"
    CATEGORY = "category"
    FREE_TEXT = "free_text"
    FLAG = "flag"
    UNKNOWN = "unknown"


class PiiRisk(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class QualityIssueKind(StrEnum):
    MISSING_VALUES = "missing_values"
    DUPLICATE_ROWS = "duplicate_rows"
    DUPLICATE_HEADER = "duplicate_header"
    CONSTANT_COLUMN = "constant_column"
    NEAR_CONSTANT_COLUMN = "near_constant_column"
    MIXED_TYPES = "mixed_types"
    PARSE_FAILURES = "parse_failures"
    INCONSISTENT_CATEGORIES = "inconsistent_categories"
    WHITESPACE_ISSUES = "whitespace_issues"
    CASE_INCONSISTENCY = "case_inconsistency"
    IMPLAUSIBLE_VALUES = "implausible_values"
    OUT_OF_RANGE_VALUES = "out_of_range_values"
    NON_FINITE_NUMBERS = "non_finite_numbers"
    UNBALANCED_TEXT = "unbalanced_text"
    DATETIME_COVERAGE = "datetime_coverage"
    HIGH_CARDINALITY = "high_cardinality"
    UNUSABLE_COLUMN_NAME = "unusable_column_name"
    POSSIBLE_PII = "possible_pii"
    SHEET_MIXED_CONTENT = "sheet_mixed_content"
    EMPTY_FILE = "empty_file"


class QualityIssue(StrictModel):
    """One detectable data-quality problem.

    `severity` drives whether a transformation may be auto-applied.
    """

    kind: QualityIssueKind
    severity: Severity
    column: str | None = None
    description: str
    affected_rows: int = 0
    affected_fraction: float = Field(ge=0.0, le=1.0, default=0.0)
    evidence: dict[str, object] = Field(default_factory=dict)
    recommended_action: str | None = None


class NumericSummary(StrictModel):
    count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    mean: float | None = None
    std: float | None = None
    min: float | None = None
    p05: float | None = None
    p25: float | None = None
    median: float | None = None
    p75: float | None = None
    p95: float | None = None
    max: float | None = None
    sum: float | None = None
    skewness: float | None = None
    kurtosis: float | None = None
    zero_count: int = Field(default=0, ge=0)
    negative_count: int = Field(default=0, ge=0)
    non_finite_count: int = Field(default=0, ge=0)


class DateSummary(StrictModel):
    count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    min: str | None = None
    max: str | None = None
    distinct_days: int | None = Field(default=None, ge=0)
    distinct_months: int | None = Field(default=None, ge=0)
    span_days: int | None = None
    has_time_component: bool = False
    granularity_guess: str | None = None


class CategorySummary(StrictModel):
    top_values: list[tuple[str, int]] = Field(default_factory=list)
    distinct_count: int = Field(default=0, ge=0)
    normalised_forms: int = Field(default=0, ge=0)
    case_variants: int = Field(default=0, ge=0)
    whitespace_variants: int = Field(default=0, ge=0)


class ColumnProfile(StrictModel):
    name: str
    original_name: str | None = None
    inferred_type: InferredType
    semantic_role: SemanticRole = SemanticRole.UNKNOWN
    null_count: int = Field(default=0, ge=0)
    null_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    unique_count: int = Field(default=0, ge=0)
    distinct_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_values: list[str] = Field(default_factory=list)
    pii_risk: PiiRisk = PiiRisk.NONE
    pii_signals: list[str] = Field(default_factory=list)
    numeric: NumericSummary | None = None
    temporal: DateSummary | None = None
    category: CategorySummary | None = None
    avg_length: float | None = None
    max_length: int | None = None
    storage_bytes: int = Field(default=0, ge=0)
    parse_failure_fraction: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("sample_values")
    @classmethod
    def cap_samples(cls, value: list[str]) -> list[str]:
        """Samples go to a model, so the payload must stay bounded."""
        return value[:10]


class ProfileWarning(StrictModel):
    code: str
    message: str
    stage: str | None = None


class DatasetProfile(StrictModel):
    """Everything the planner is allowed to know about the dataset up front."""

    dataset_id: str
    source_file_hash: str
    sheet_name: str | None = None
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    estimated_memory_bytes: int = Field(default=0, ge=0)
    columns: list[ColumnProfile] = Field(default_factory=list)
    quality_issues: list[QualityIssue] = Field(default_factory=list)
    duplicate_row_count: int = Field(default=0, ge=0)
    time_coverage: DateSummary | None = None
    warnings: list[ProfileWarning] = Field(default_factory=list)
    is_exploratory: bool = True
    profiler_version: str = "1"

    def column(self, name: str) -> ColumnProfile | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    @property
    def usable_columns(self) -> list[ColumnProfile]:
        """Columns worth handing to the planner (non-constant, has values)."""
        return [c for c in self.columns if c.unique_count > 1 or c.null_count < self.row_count]

    def of_type(self, *types: InferredType) -> list[ColumnProfile]:
        wanted = set(types)
        return [c for c in self.columns if c.inferred_type in wanted]

    def of_role(self, *roles: SemanticRole) -> list[ColumnProfile]:
        wanted = set(roles)
        return [c for c in self.columns if c.semantic_role in wanted]

    def highest_pii_risk(self) -> PiiRisk:
        order = [PiiRisk.NONE, PiiRisk.LOW, PiiRisk.MEDIUM, PiiRisk.HIGH]
        return max((c.pii_risk for c in self.columns), key=order.index, default=PiiRisk.NONE)


class DatasetSnapshot(StrictModel):
    """Reference to a materialised, immutable version of a table on disk."""

    dataset_id: str
    artifact_id: str
    version: int = Field(ge=1)
    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    columns: list[str] = Field(default_factory=list)
    source_file_hash: str
    sheet_name: str | None = None
    created_from: str = "raw"
    transform_digest: str | None = None
