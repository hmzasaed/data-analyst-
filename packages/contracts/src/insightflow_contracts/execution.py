"""Execution results, artifact references and tool specifications."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from insightflow_contracts.profile import StrictModel


class ArtifactKind(StrEnum):
    RAW_FILE = "raw_file"
    DATASET_SNAPSHOT = "dataset_snapshot"
    DATASET_CSV = "dataset_csv"
    TABLE_CSV = "table_csv"
    TABLE_JSON = "table_json"
    CHART_PNG = "chart_png"
    CHART_SVG = "chart_svg"
    REPORT_HTML = "report_html"
    REPORT_MARKDOWN = "report_markdown"
    REPORT_PDF = "report_pdf"
    REPORT_JSON = "report_json"
    CODE = "code"
    LOG = "log"
    AUDIT = "audit"


class MetricValue(StrictModel):
    """A single computed number with full provenance.

    `unit` and `definition` exist so a report can never present a bare number whose
    meaning is guessable only from context.
    """

    name: str
    value: float | int | str | None
    unit: str | None = None
    definition: str | None = None
    n: int | None = Field(default=None, description="Sample size behind the value.")
    precision: int = Field(default=4, ge=0, le=12)
    #: Step that produced this metric. Two steps may legitimately compute the same
    #: metric name with different values, so evidence is matched on both.
    source_step: str | None = None
    #: Evidence table this metric is derived from. Without this a metric cannot be
    #: checked against the artifact it cites, so it is set for every metric.
    table: str | None = None


class ResourceUsage(StrictModel):
    wall_seconds: float | None = Field(default=None, ge=0)
    peak_memory_bytes: int | None = Field(default=None, ge=0)
    cpu_seconds: float | None = Field(default=None, ge=0)


class ArtifactRef(StrictModel):
    artifact_id: str
    kind: ArtifactKind
    path: str
    row_count: int | None = Field(default=None, ge=0)
    byte_size: int | None = Field(default=None, ge=0)
    sha256: str | None = None
    mime_type: str | None = None
    created_at: str | None = None

    @model_validator(mode="after")
    def _csv_needs_rows(self) -> ArtifactRef:
        if self.kind is ArtifactKind.TABLE_CSV and self.row_count is None:
            raise ValueError("table_csv artifacts must record row_count")
        return self


class ToolSpec(StrictModel):
    """Registry metadata: what a tool accepts, produces and costs."""

    name: str
    version: str
    analysis_type: str
    summary: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = Field(default_factory=dict)
    allowed_dataset_refs: list[str] = Field(default_factory=list)
    max_rows: int = Field(default=5_000_000, ge=1)
    max_columns: int = Field(default=200, ge=1)
    timeout_seconds: int = Field(default=120, ge=1, le=1800)
    mutates_data: bool = False
    requires_user_approval: bool = False
    requires_sandbox: bool = False
    deterministic: bool = True
    tags: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def snake_case_name(cls, value: str) -> str:
        if not value.replace("_", "").isalnum() or not value.islower():
            raise ValueError("tool names must be lowercase snake_case")
        return value


class ExecutionResult(StrictModel):
    """Outcome of one analysis step, always tied to the dataset version used."""

    step_id: str
    tool_name: str
    tool_version: str
    dataset_version: int = Field(ge=1)
    status: str = Field(pattern="^(succeeded|failed|skipped)$")
    metrics: list[MetricValue] = Field(default_factory=list)
    tables: list[ArtifactRef] = Field(default_factory=list)
    charts: list[ArtifactRef] = Field(default_factory=list)
    records: list[ArtifactRef] = Field(default_factory=list)
    code: str | None = None
    query: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    usage: ResourceUsage = Field(default_factory=ResourceUsage)
    tool_digest: str | None = None

    @model_validator(mode="after")
    def _failed_results_explain(self) -> ExecutionResult:
        if self.status == "failed" and not self.errors:
            raise ValueError("failed ExecutionResult must record at least one error")
        return self

    def metric(self, name: str) -> MetricValue | None:
        for item in self.metrics:
            if item.name == name:
                return item
        return None
