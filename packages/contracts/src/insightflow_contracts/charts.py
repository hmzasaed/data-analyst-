"""Chart request contracts.

Analysis tools emit *chart requests* (data plus labels), never images. Rendering is
a separate, deterministic step in `insightflow_visualization`, which means a chart
can be re-rendered, inspected or checked against its source table without re-running
the analysis.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from insightflow_contracts.profile import StrictModel


class ChartKind(StrEnum):
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    LINE = "line"
    AREA = "area"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    BOX = "box"
    HEATMAP = "heatmap"


class ChartSeries(StrictModel):
    name: str
    x: list[str | float | int | None] = Field(default_factory=list)
    y: list[float | int | None] = Field(default_factory=list)
    #: Raw observations per category, used only by box plots. Keeping the raw
    #: values means the plot can be re-derived instead of trusted.
    raw: list[list[float]] | None = None
    labels: dict[str, str] = Field(
        default_factory=dict,
        description="Optional display label overrides, keyed by raw x value.",
    )

    @model_validator(mode="after")
    def _aligned(self) -> ChartSeries:
        if len(self.x) != len(self.y):
            raise ValueError(
                f"series '{self.name}' has {len(self.x)} x values but {len(self.y)} y values"
            )
        if self.raw is not None and self.x and len(self.raw) != len(self.x):
            raise ValueError(
                f"series '{self.name}' has {len(self.x)} categories but "
                f"{len(self.raw)} raw value groups"
            )
        return self

    @property
    def requires_raw(self) -> bool:
        return self.raw is None


class ChartRequest(StrictModel):
    """One chart to render, with everything needed to label it correctly."""

    chart_id: str
    kind: ChartKind
    title: str
    series: list[ChartSeries] = Field(default_factory=list)
    x_label: str = ""
    y_label: str = ""
    y_unit: str | None = None
    #: Heatmaps carry a labelled matrix rather than a flat series, so the plot
    #: never has to reconstruct structure by parsing strings.
    matrix: list[list[float | None]] | None = None
    row_labels: list[str] | None = None
    column_labels: list[str] | None = None
    color_limits: tuple[float, float] = (-1.0, 1.0)
    source_artifact_id: str | None = None
    analysis_step_id: str | None = None
    caption: str | None = None
    #: Free-text statement of what the chart does and does not show.
    caveat: str | None = None
    max_categories: int = Field(default=30, ge=1, le=200)

    @model_validator(mode="after")
    def _valid(self) -> ChartRequest:
        """An unlabelled axis is how a report ends up asserting the wrong unit."""
        if self.y_unit and not self.y_label:
            raise ValueError("chart with a y unit must carry a y_label")
        if not self.title.strip():
            raise ValueError("chart title must not be blank")
        if self.kind is ChartKind.HEATMAP:
            if not (self.matrix and self.row_labels and self.column_labels):
                raise ValueError("heatmap requires matrix, row_labels and column_labels")
            if len(self.matrix) != len(self.row_labels):
                raise ValueError("heatmap matrix row count must match row_labels")
            if any(len(row) != len(self.column_labels) for row in self.matrix):
                raise ValueError("heatmap matrix column count must match column_labels")
        elif not self.series:
            raise ValueError(f"{self.kind.value} charts require at least one series")
        return self

    def point_count(self) -> int:
        return sum(len(s.y) for s in self.series)
