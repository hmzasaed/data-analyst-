"""Chart/table consistency checks.

A chart that disagrees with the table it came from is the most damaging kind of
report bug: it looks right. These checks run before a report is published and fail
loudly rather than quietly re-rendering.
"""

from __future__ import annotations

import math

import polars as pl
from insightflow_contracts import ChartKind, ChartRequest


def verify_chart_matches(
    request: ChartRequest,
    *,
    source: pl.DataFrame | None = None,
    value_column: str | None = None,
    tolerance: float = 1e-6,
) -> list[str]:
    """Return a list of problems; an empty list means the chart is trustworthy.

    With `source` provided, the plotted values are compared against the column of
    the evidence table they claim to come from.
    """
    problems: list[str] = []
    problems.extend(_structural_problems(request))

    if source is not None and value_column and request.kind is not ChartKind.HEATMAP:
        problems.extend(_comparison_problems(request, source, value_column, tolerance))
    return problems


def _structural_problems(request: ChartRequest) -> list[str]:
    problems: list[str] = []
    if not request.title.strip():
        problems.append("chart has no title")
    if request.kind is not ChartKind.HEATMAP and not request.series:
        problems.append("chart has no series")

    for series in request.series:
        if series.raw is not None and any(len(group) == 0 for group in series.raw):
            problems.append(f"series '{series.name}' contains an empty raw group")
        plotted = [
            value
            for value in series.y
            if value is not None and not (isinstance(value, float) and math.isnan(value))
        ]
        if series.y and not plotted:
            problems.append(f"series '{series.name}' has no finite values to plot")

    if request.kind is ChartKind.HEATMAP:
        flat = [value for row in (request.matrix or []) for value in row if value is not None]
        if not flat:
            problems.append("heatmap matrix has no values")
        low, high = request.color_limits
        if low >= high:
            problems.append("heatmap colour limits are inverted")
    return problems


def _comparison_problems(
    request: ChartRequest,
    source: pl.DataFrame,
    value_column: str,
    tolerance: float,
) -> list[str]:
    if value_column not in source.columns:
        return [f"evidence table has no column '{value_column}' to check the chart against"]

    if source.height > request.max_categories:
        # A deliberately truncated chart is allowed, provided the caption says so.
        if "Only the first" not in (request.caption or "") and "at most" not in (
            request.caveat or ""
        ):
            return [
                f"chart shows {request.max_categories} of {source.height} rows without saying so"
            ]
        return []

    table_values = sorted(
        float(value)
        for value in source.get_column(value_column).to_list()
        if value is not None
    )
    plotted_values: list[float] = []
    for series in request.series:
        plotted_values.extend(
            float(value)
            for value in series.y
            if value is not None and not (isinstance(value, float) and math.isnan(value))
        )
    plotted_values.sort()

    if len(plotted_values) != len(table_values):
        return [
            f"chart plots {len(plotted_values)} value(s) but the evidence table holds "
            f"{len(table_values)}"
        ]

    mismatches = [
        (left, right)
        for left, right in zip(plotted_values, table_values, strict=True)
        if abs(left - right) > tolerance
    ]
    if mismatches:
        first = mismatches[0]
        return [
            f"chart values differ from the evidence table in {len(mismatches)} place(s); "
            f"first mismatch {first[0]} vs {first[1]}"
        ]
    return []
