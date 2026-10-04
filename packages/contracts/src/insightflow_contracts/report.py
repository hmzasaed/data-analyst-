"""Report document contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from insightflow_contracts.profile import StrictModel


class ReportSectionKind(StrEnum):
    EXECUTIVE_SUMMARY = "executive_summary"
    DATASET_OVERVIEW = "dataset_overview"
    DATA_QUALITY = "data_quality"
    PREPROCESSING = "preprocessing"
    DESCRIPTIVE_STATISTICS = "descriptive_statistics"
    DISTRIBUTIONS = "distributions"
    RELATIONSHIPS = "relationships"
    TRENDS_AND_SEGMENTS = "trends_and_segments"
    FEATURES = "features"
    KEY_FINDINGS = "key_findings"
    ANOMALIES = "anomalies"
    METHODOLOGY = "methodology"
    LIMITATIONS = "limitations"
    REPRODUCIBILITY = "reproducibility"
    NEXT_STEPS = "next_steps"


class ReportTable(StrictModel):
    """A table shown in the report. Cells are pre-formatted text.

    `source_artifact_id` names the CSV holding the unrounded values, so every
    figure in the table can be checked against stored output.
    """

    title: str
    columns: list[str]
    rows: list[list[str]] = Field(default_factory=list)
    note: str | None = None
    source_artifact_id: str | None = None
    #: Columns to right-align (numbers).
    numeric_columns: list[int] = Field(default_factory=list)


class ReportChart(StrictModel):
    artifact_id: str
    title: str
    caption: str | None = None
    #: Half-width charts are laid out two per row.
    wide: bool = True


class ReportKpi(StrictModel):
    label: str
    value: str
    note: str | None = None


class ReportSection(StrictModel):
    kind: ReportSectionKind
    heading: str
    body_markdown: str = ""
    finding_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    order: int = 0
    tables: list[ReportTable] = Field(default_factory=list)
    charts: list[ReportChart] = Field(default_factory=list)
    #: Markdown shown after the tables and charts (e.g. interpretation notes).
    footer_markdown: str = ""


class ReportDocument(StrictModel):
    """Rendered-ready report content. Numbers here must come from artifacts."""

    job_id: str
    dataset_id: str
    title: str
    objective: str
    sections: list[ReportSection] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    is_exploratory: bool = False
    generated_at: str | None = None
    composer_model: str | None = None
    report_version: str = "1"
    source_filename: str | None = None
    kpis: list[ReportKpi] = Field(default_factory=list)

    def section(self, kind: ReportSectionKind) -> ReportSection | None:
        for section in self.sections:
            if section.kind is kind:
                return section
        return None

    def ordered_sections(self) -> list[ReportSection]:
        return sorted(self.sections, key=lambda s: (s.order, s.kind.value))
