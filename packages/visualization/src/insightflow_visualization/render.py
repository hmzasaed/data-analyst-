"""Deterministic chart rendering.

Charts are rendered from validated `ChartRequest` objects by a headless
matplotlib process. Two properties matter:

* *fidelity* - a chart is drawn from the same series the evidence table holds, so
  a verifier can compare them;
* *determinism* - identical input produces an identical image, which is what makes
  a cached artifact trustworthy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import seaborn as sns
from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    ChartKind,
    ChartRequest,
    ToolExecutionError,
    file_sha256,
)

FIGURE_WIDTH = 9.0
FIGURE_HEIGHT = 5.0
DPI = 140
MAX_CATEGORIES = 60

#: seaborn's colourblind-safe palette, shared with the exploratory charts.
PALETTE: tuple[str, ...] = tuple(sns.color_palette("colorblind").as_hex())

GRID_COLOUR = "#d9dee3"
TEXT_COLOUR = "#1f2933"
MUTED_COLOUR = "#6b7684"

#: PNG metadata that would otherwise vary between runs.
_STABLE_METADATA = {"Software": None, "Creation Time": None}


@dataclass
class RenderResult:
    artifact: ArtifactRef
    warnings: list[str]


class ChartRenderer:
    version = "1"

    def __init__(self, output_dir: str | Path, *, width: float = FIGURE_WIDTH, height: float = FIGURE_HEIGHT):
        self.output_dir = Path(output_dir)
        self.width = width
        self.height = height

    def render(self, request: ChartRequest) -> RenderResult:
        """Render one chart to PNG and return its artifact reference."""
        if request.kind is not ChartKind.HEATMAP and not request.series:
            raise ToolExecutionError("chart has no series", chart_id=request.chart_id, retryable=False)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{request.chart_id}.png"

        from insightflow_visualization.eda import THEME

        with plt.rc_context(THEME):
            return self._render(request, path)

    def _render(self, request: ChartRequest, path: Path) -> RenderResult:
        figure, axes = plt.subplots(figsize=(self.width, self.height))
        warnings: list[str] = []
        try:
            self._style(figure, axes)
            renderer = {
                ChartKind.BAR: self._bar,
                ChartKind.HORIZONTAL_BAR: self._horizontal_bar,
                ChartKind.LINE: self._line,
                ChartKind.AREA: self._area,
                ChartKind.SCATTER: self._scatter,
                ChartKind.HISTOGRAM: self._histogram,
                ChartKind.BOX: self._box,
                ChartKind.HEATMAP: self._heatmap,
            }[request.kind]
            warnings = renderer(axes, request) or []
            axes.set_title(request.title, color=TEXT_COLOUR, fontsize=12, loc="left", pad=12)
            figure.text(
                0.01, 0.01, request.caveat or "", fontsize=7, color=MUTED_COLOUR, wrap=True,
            )
            figure.tight_layout(rect=(0, 0.06 if request.caveat else 0, 1, 1))
            figure.savefig(path, dpi=DPI, metadata=_STABLE_METADATA)
        finally:
            plt.close(figure)

        return RenderResult(
            artifact=ArtifactRef(
                artifact_id=request.chart_id,
                kind=ArtifactKind.CHART_PNG,
                path=str(path),
                byte_size=path.stat().st_size,
                sha256=file_sha256(path),
                mime_type="image/png",
            ),
            warnings=warnings,
        )

    # ------------------------------------------------------------------ chrome
    def _style(self, figure, axes) -> None:
        figure.patch.set_facecolor("white")
        axes.set_facecolor("white")
        for spine in ("top", "right"):
            axes.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            axes.spines[spine].set_color(GRID_COLOUR)
        axes.tick_params(colors=MUTED_COLOUR, labelsize=9)
        axes.yaxis.grid(True, color=GRID_COLOUR, linewidth=0.7)
        axes.set_axisbelow(True)

    # ----------------------------------------------------------------- renderers
    def _bar(self, axes, request: ChartRequest) -> list[str]:
        return self._grouped_bars(axes, request, horizontal=False)

    def _horizontal_bar(self, axes, request: ChartRequest) -> list[str]:
        return self._grouped_bars(axes, request, horizontal=True)

    def _grouped_bars(self, axes, request: ChartRequest, *, horizontal: bool) -> list[str]:
        warnings: list[str] = []
        series = request.series
        categories = [str(v) for v in series[0].x]
        if len(categories) > request.max_categories:
            warnings.append(
                f"Only the first {request.max_categories} of {len(categories)} categories are plotted."
            )
            series = _truncate(series, request.max_categories)
            categories = categories[: request.max_categories]

        count = len(series)
        width = 0.8 / max(count, 1)
        positions = list(range(len(categories)))

        for index, item in enumerate(series):
            values = [_clean(v) for v in item.y[: len(categories)]]
            offsets = [p + (index - (count - 1) / 2) * width for p in positions]
            if horizontal:
                axes.barh(offsets, values, height=width * 0.92, label=item.name, color=_colour(index))
            else:
                axes.bar(offsets, values, width=width * 0.92, label=item.name, color=_colour(index))

        if horizontal:
            axes.set_yticks(positions)
            axes.set_yticklabels(_shorten(categories))
            axes.set_xlabel(request.x_label)
            axes.xaxis.grid(True, color=GRID_COLOUR, linewidth=0.7)
            axes.yaxis.grid(False)
        else:
            axes.set_xticks(positions)
            axes.set_xticklabels(_shorten(categories), rotation=0 if len(categories) <= 8 else 45,
                                 ha="center" if len(categories) <= 8 else "right")
            axes.set_xlabel(request.x_label)
        axes.set_ylabel(request.y_label + (f" ({request.y_unit})" if request.y_unit else ""))
        if count > 1:
            axes.legend(frameon=False, fontsize=8, ncols=min(count, 3))
        return warnings

    def _line(self, axes, request: ChartRequest) -> list[str]:
        for index, item in enumerate(request.series):
            values = [_clean(v) for v in item.y]
            axes.plot(
                range(len(values)),
                [math.nan if v is None else v for v in values],
                label=item.name,
                color=_colour(index),
                linewidth=2,
                marker="o" if len(values) <= 40 else None,
                markersize=3,
            )
        return self._time_axes(axes, request)

    def _area(self, axes, request: ChartRequest) -> list[str]:
        for index, item in enumerate(request.series):
            values = [_clean(v) for v in item.y]
            axes.fill_between(
                range(len(values)),
                [0.0 if v is None else v for v in values],
                [math.nan if v is None else v for v in values],
                alpha=0.25,
                color=_colour(index),
                label=item.name,
            )
        return self._time_axes(axes, request)

    def _scatter(self, axes, request: ChartRequest) -> list[str]:
        for index, item in enumerate(request.series):
            xs = [_number(v) for v in item.x]
            ys = [_number(v) for v in item.y]
            pairs = [(x, y) for x, y in zip(xs, ys, strict=False) if x is not None and y is not None]
            if not pairs:
                continue
            if len(pairs) <= 2:  # a fitted line
                axes.plot(
                    [p[0] for p in pairs], [p[1] for p in pairs],
                    color=_colour(index), linewidth=2, label=item.name,
                )
            else:
                axes.scatter(
                    [p[0] for p in pairs], [p[1] for p in pairs],
                    s=18, alpha=0.6, color=_colour(index), label=item.name, edgecolors="none",
                )
        axes.set_xlabel(request.x_label)
        axes.set_ylabel(request.y_label + (f" ({request.y_unit})" if request.y_unit else ""))
        if len(request.series) > 1:
            axes.legend(frameon=False, fontsize=8)
        return []

    def _histogram(self, axes, request: ChartRequest) -> list[str]:
        """Binned counts, drawn as contiguous bars."""
        item = request.series[0]
        labels = [str(v) for v in item.x]
        values = [_clean(v) or 0.0 for v in item.y]
        axes.bar(range(len(values)), values, width=1.0, color=PALETTE[0], align="edge")
        step = max(1, len(values) // 12)
        axes.set_xticks(list(range(0, len(labels), step)))
        axes.set_xticklabels(_shorten([labels[i] for i in range(0, len(labels), step)]), rotation=45,
                             ha="right", fontsize=8)
        axes.set_xlabel(request.x_label)
        axes.set_ylabel(request.y_label)
        return []

    def _box(self, axes, request: ChartRequest) -> list[str]:
        groups: list[list[float]] = []
        labels: list[str] = []
        for item in request.series:
            raw = item.raw or []
            for index, values in enumerate(raw):
                cleaned = [_number(v) for v in values]
                cleaned = [v for v in cleaned if v is not None]
                if not cleaned:
                    continue
                groups.append(cleaned)
                labels.append(str(item.x[index]) if index < len(item.x) else f"group {index + 1}")

        if not groups:
            raise ToolExecutionError(
                "box plot requires raw observations per category",
                chart_id=request.chart_id,
                retryable=False,
            )

        axes.boxplot(
            groups,
            tick_labels=_shorten(labels),
            patch_artist=True,
            showfliers=True,
            boxprops={"facecolor": PALETTE[0], "alpha": 0.55, "edgecolor": TEXT_COLOUR},
            medianprops={"color": "#d1651a", "linewidth": 1.6},
            whiskerprops={"color": MUTED_COLOUR},
            capprops={"color": MUTED_COLOUR},
            flierprops={
                "marker": "o",
                "markersize": 4,
                "markerfacecolor": "#b5495b",
                "markeredgecolor": "none",
                "alpha": 0.7,
            },
        )
        axes.set_xlabel(request.x_label)
        axes.set_ylabel(request.y_label + (f" ({request.y_unit})" if request.y_unit else ""))
        return []

    def _heatmap(self, axes, request: ChartRequest) -> list[str]:
        if not request.matrix or not request.row_labels or not request.column_labels:
            raise ToolExecutionError(
                "heatmap requires a labelled matrix", chart_id=request.chart_id, retryable=False
            )

        matrix = [[_number(v) for v in row] for row in request.matrix]
        low, high = request.color_limits
        image = axes.imshow(matrix, cmap="RdBu_r", vmin=low, vmax=high, aspect="auto")
        axes.set_xticks(range(len(request.column_labels)))
        axes.set_xticklabels(_shorten(request.column_labels), rotation=45, ha="right", fontsize=8)
        axes.set_yticks(range(len(request.row_labels)))
        axes.set_yticklabels(_shorten(request.row_labels), fontsize=8)
        axes.get_figure().colorbar(image, ax=axes, fraction=0.03, pad=0.02, label=request.y_label)
        return []

    # ------------------------------------------------------------------ helpers
    def _time_axes(self, axes, request: ChartRequest) -> list[str]:
        categories = [str(v) for v in request.series[0].x]
        step = max(1, len(categories) // 12)
        axes.set_xticks(list(range(0, len(categories), step)))
        axes.set_xticklabels(
            _shorten([categories[i] for i in range(0, len(categories), step)]),
            rotation=45, ha="right", fontsize=8,
        )
        axes.set_xlabel(request.x_label)
        axes.set_ylabel(request.y_label + (f" ({request.y_unit})" if request.y_unit else ""))
        if len(request.series) > 1:
            axes.legend(frameon=False, fontsize=8)
        return []


def render_charts(
    requests: list[ChartRequest], output_dir: str | Path
) -> tuple[list[ArtifactRef], list[str]]:
    """Render a batch of charts, returning artifacts and any warnings."""
    renderer = ChartRenderer(output_dir)
    artifacts: list[ArtifactRef] = []
    warnings: list[str] = []
    for request in requests:
        result = renderer.render(request)
        artifacts.append(result.artifact)
        warnings.extend(f"{request.chart_id}: {message}" for message in result.warnings)
    return artifacts, warnings


# --------------------------------------------------------------------- helpers
def _colour(index: int) -> str:
    return PALETTE[index % len(PALETTE)]


def _clean(value) -> float | None:
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(as_float) or math.isinf(as_float) else as_float


def _number(value) -> float | None:
    return _clean(value)


def _shorten(labels: list[str], limit: int = 28) -> list[str]:
    trimmed = []
    for label in labels:
        text = str(label)
        trimmed.append(text if len(text) <= limit else f"{text[: limit - 1]}…")
    return trimmed


def _truncate(series, limit: int):
    from insightflow_contracts import ChartSeries

    return [
        ChartSeries(
            name=item.name,
            x=item.x[:limit],
            y=item.y[:limit],
            raw=item.raw[:limit] if item.raw else None,
            labels=item.labels,
        )
        for item in series
    ]
