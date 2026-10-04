"""Descriptive analysis tools: what is in this data, precisely.

Every metric returned here is a computed number with a definition and a sample
size, so the report can print it without the model re-deriving it.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl
from insightflow_contracts import (
    ChartKind,
    ChartRequest,
    ChartSeries,
    MetricValue,
    ToolExecutionError,
    derive_id,
)
from scipy import stats

from insightflow_analytics.context import AnalysisContext, ToolOutcome

TOOL_VERSION = "1"


def describe_measures(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Count, sum, mean, median, spread and distribution shape for each measure."""
    ctx.guard_row_count()
    columns: list[str] = list(params["columns"])
    if not columns:
        columns = [
            name
            for name, dtype in ctx.frame.schema.items()
            if dtype in {pl.Float64, pl.Float32, pl.Int64, pl.Int32, pl.Int16, pl.Int8}
        ]
    if not columns:
        raise _no_measures()

    outcome = ToolOutcome()
    rows: list[dict] = []

    for column in columns:
        series = ctx.frame.get_column(column).cast(pl.Float64, strict=False).drop_nulls()
        count = series.len()
        if count == 0:
            outcome.warnings.append(f"'{column}' has no non-null values; no statistics computed.")
            continue

        values = series.to_numpy().astype("float64", copy=False)
        finite = values[~np.isinf(values)]
        summary = {
            "column": column,
            "count": int(finite.size),
            "sum": float(finite.sum()),
            "mean": float(finite.mean()),
            "std": float(finite.std(ddof=1)) if finite.size > 1 else 0.0,
            "min": float(finite.min()),
            "p05": float(np.percentile(finite, 5)),
            "p25": float(np.percentile(finite, 25)),
            "median": float(np.median(finite)),
            "p75": float(np.percentile(finite, 75)),
            "p95": float(np.percentile(finite, 95)),
            "max": float(finite.max()),
            "skewness": float(stats.skew(finite)) if finite.size > 2 else 0.0,
            "missing": int(ctx.frame.height - count),
        }
        rows.append(summary)

        outcome.metric(
            f"{column}__count", summary["count"], definition=f"Non-null rows in '{column}'.",
            n=summary["count"], unit="rows",
        )
        outcome.metric(
            f"{column}__mean", summary["mean"], definition=f"Arithmetic mean of '{column}'.",
            n=summary["count"], unit=params.get("unit"),
        )
        outcome.metric(
            f"{column}__median", summary["median"], definition=f"Median of '{column}'.",
            n=summary["count"], unit=params.get("unit"),
        )
        outcome.metric(
            f"{column}__total", summary["sum"], definition=f"Sum of '{column}'.", n=summary["count"],
            unit=params.get("unit"),
        )
        if summary["count"] > 2 and summary["std"] > 0:
            outcome.metric(
                f"{column}__skewness",
                summary["skewness"],
                definition="Sample skewness; 0 means symmetric.",
                n=summary["count"], table="measure_summary",
            )

    if not rows:
        raise _no_measures()

    frame = pl.DataFrame(rows)
    outcome.table(frame, "measure_summary")
    for item in outcome.metrics:
        item.table = "measure_summary"
    return outcome


def rank_categories(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Rank a categorical column by a measure, with share and cumulative share."""
    ctx.guard_row_count()
    group = params["group_by"]
    measure = params.get("measure")
    limit = int(params.get("limit", 20))

    group_expr = ctx.category_column(group)
    aggregations = [pl.len().alias("row_count")]
    if measure:
        ctx.numeric_column(measure)
        aggregations.append(pl.col(measure).sum().alias("measure_total"))
        aggregations.append(pl.col(measure).mean().alias("measure_mean"))
        aggregations.append(pl.col(measure).median().alias("measure_median"))
        aggregations.append(pl.col(measure).null_count().alias("measure_missing"))

    sort_by = "measure_total" if measure else "row_count"
    grouped = (
        ctx.frame.group_by(group_expr.alias(group))
        .agg(aggregations)
        .sort([sort_by, group], descending=[True, False])
        .head(limit)
    )

    # Share is computed against the whole analysed dataset, not just the ranked
    # rows, so a truncated table still sums to a truthful fraction of the total.
    if measure:
        denominator = float(ctx.frame.get_column(measure).cast(pl.Float64, strict=False).sum() or 0.0)
        denom_label = f"total {measure}"
    else:
        denominator = float(ctx.frame.height)
        denom_label = "total rows"
    denominator = denominator if abs(denominator) > 1e-12 else 1.0

    result = grouped.with_columns(
        (pl.col(sort_by).cast(pl.Float64) / denominator).alias("share")
    ).with_columns(
        pl.col("share").cum_sum().alias("cumulative_share"),
        # Percentages are reported as metrics and written into the same table so
        # every claimed figure can be checked from its artifact without rescaling.
        (pl.col("share") * 100.0).alias("share_pct"),
        # The denominator is reported as a metric, so it is written into the same
        # table: every figure this tool claims must be checkable from its artifact.
        pl.lit(denominator).alias("observed_total"),
    )

    outcome = ToolOutcome()
    outcome.table(result, f"ranking_{group}")
    outcome.metric(
        f"{group}__distinct_values",
        ctx.frame.get_column(group).n_unique(),
        definition=f"Number of distinct values in '{group}'.",
        n=ctx.frame.height,
        unit="values",
        table=f"ranking_{group}",
    )
    # Named `observed_total`, not `<measure>__total`: this is the grand total used as
    # the share denominator, which is a different quantity from `describe_measures`'s
    # per-column total of the same name.
    outcome.metric(
        f"{measure}__observed_total" if measure else "row_count__total",
        denominator
        if not measure
        else float(ctx.frame.get_column(measure).cast(pl.Float64, strict=False).sum() or 0.0),
        definition=f"Denominator used for shares: {denom_label}.",
        n=ctx.frame.height,
        unit=params.get("unit"),
        table=f"ranking_{group}",
    )
    if len(result):
        outcome.metric(
            f"{group}__top_share",
            float(result["share"][0]) * 100.0,
            definition=f"Share of {denom_label} held by the top-ranked '{group}' value.",
            n=len(result),
            unit="%",
        )

    if params.get("chart", True) and len(result):
        y_field = sort_by
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "rank_categories", "group": group, "measure": measure}),
                kind=(
                    ChartKind.HORIZONTAL_BAR
                    if len(result) > 8
                    else ChartKind.BAR
                ),
                title=params.get("title") or f"{measure or 'Rows'} by {group}",
                series=[
                    ChartSeries(
                        name=measure or "rows",
                        x=[str(v) for v in result[group].to_list()],
                        y=[float(v) for v in result[y_field].to_list()],
                    )
                ],
                x_label=group,
                y_label=measure or "row count",
                y_unit=params.get("unit"),
                caption=f"Top {len(result)} of {ctx.frame.get_column(group).n_unique()} distinct '{group}' values.",
                caveat="Shares are computed on the analysed rows only; filtered-out rows are not included.",
            )
        )
    return outcome


def missingness_profile(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Per-column missingness and uniqueness, for the data-quality section."""
    columns: list[str] = list(params.get("columns") or ctx.frame.columns)
    rows = []
    for column in columns:
        series = ctx.frame.get_column(column)
        rows.append(
            {
                "column": column,
                "row_count": ctx.frame.height,
                "missing": int(series.null_count()),
                "missing_share": round(series.null_count() / max(ctx.frame.height, 1), 4),
                # Reported percentages must exist in the table so a reader can
                # check them without recomputing the scale.
                "missing_pct": round(series.null_count() / max(ctx.frame.height, 1) * 100.0, 4),
                "distinct": int(series.n_unique()),
            }
        )
    outcome = ToolOutcome()
    frame = pl.DataFrame(rows).sort("missing_share", descending=True)
    outcome.table(frame, "missingness")

    # With no missing values at all, report that plainly rather than naming an
    # arbitrary "worst" column.
    incomplete = frame.filter(pl.col("missing") > 0)
    if incomplete.height == 0:
        outcome.metric(
            "worst_missing_column",
            None,
            definition="Column with the highest share of missing values.",
            n=len(rows),
        )
        outcome.metric(
            "worst_missing_share",
            0.0,
            definition="Share of rows missing in the least complete column.",
            n=len(rows),
            unit="%",
        )
        outcome.warnings.append("No column has missing values.")
        return outcome

    worst = incomplete.head(1)
    outcome.metric(
        "worst_missing_column",
        str(worst["column"][0]),
        definition="Column with the highest share of missing values.",
        n=len(rows),
    )
    outcome.metric(
        "worst_missing_share",
        float(worst["missing_share"][0]) * 100.0,
        definition="Share of rows missing in the least complete column.",
        n=len(rows),
        unit="%",
    )
    return outcome


def value_distribution(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Histogram-style bin counts for one measure."""
    ctx.guard_row_count()
    column = params["column"]
    ctx.numeric_column(column)
    bins = int(params.get("bins", 30))

    values = ctx.frame.get_column(column).cast(pl.Float64, strict=False).drop_nulls()
    if values.len() == 0:
        raise _empty_column(column)

    minimum, maximum = float(values.min()), float(values.max())
    if math.isclose(minimum, maximum):
        raise _degenerate(column, minimum)

    frame = (
        ctx.frame.select(pl.col(column).cast(pl.Float64, strict=False).alias("value"))
        .drop_nulls()
        .with_columns(
            (((pl.col("value") - minimum) / (maximum - minimum) * bins).floor())
            .clip(0, bins - 1)
            .cast(pl.Int32)
            .alias("bin")
        )
        .group_by("bin")
        .agg(pl.len().alias("count"))
        .sort("bin")
        .with_columns(
            (pl.col("bin") * (maximum - minimum) / bins + minimum).alias("lower"),
            ((pl.col("bin") + 1) * (maximum - minimum) / bins + minimum).alias("upper"),
        )
    )

    outcome = ToolOutcome()
    # The reported summary figures are written into the same table as the bins, so
    # each one can be checked against the artifact it is cited from.
    outcome.table(
        frame.with_columns(
            pl.lit(minimum).alias("observed_min"),
            pl.lit(maximum).alias("observed_max"),
            pl.lit(float(stats.skew(values.to_numpy()))).alias("observed_skewness"),
        ),
        f"distribution_{column}",
    )
    outcome.metric(
        f"{column}__min", minimum, definition=f"Smallest observed value of '{column}'.", n=values.len()
    )
    outcome.metric(
        f"{column}__max", maximum, definition=f"Largest observed value of '{column}'.", n=values.len()
    )
    outcome.metric(
        f"{column}__skewness",
        float(stats.skew(values.to_numpy())),
        definition="Sample skewness; large values mean a long right tail.",
        n=values.len(),
    )
    bounds = frame.select("lower", "upper").rows()
    outcome.charts.append(
        ChartRequest(
            chart_id=derive_id("chart", {"tool": "value_distribution", "column": column}),
            kind=ChartKind.HISTOGRAM,
            title=f"Distribution of {column}",
            series=[
                ChartSeries(
                    name="rows",
                    x=[f"{lower:.4g}-{upper:.4g}" for lower, upper in bounds],
                    y=[float(count) for count in frame["count"]],
                )
            ],
            x_label=column,
            y_label="rows",
            caption=f"{values.len():,} non-null values across {bins} equal-width bins.",
            caveat="Bins are equal width; a skewed measure is easier to read as a log scale, which is not shown.",
        )
    )
    return outcome


def distribution_by_group(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Box-plot style summary of a measure per group, from the raw observations.

    Group medians can hide everything about a distribution, so the chart carries
    the underlying values rather than pre-computed five-number summaries.
    """
    ctx.guard_row_count()
    group = params["group_by"]
    measure = params["column"]
    ctx.category_column(group)
    ctx.numeric_column(measure)

    subset = ctx.frame.select([group, measure]).drop_nulls()
    if subset.height == 0:
        raise _empty_column(measure)

    grouped = (
        subset.group_by(group)
        .agg(pl.col(measure))
        .sort(measure)
        .head(int(params.get("max_groups", 12)))
    )
    if grouped.height < 2:
        raise ToolExecutionError(
            "at least two groups are needed for a distribution comparison",
            group_by=group,
            distinct=int(grouped.height),
            retryable=False,
        )

    labels = [str(v) for v in grouped[group].to_list()]
    raw = [[float(v) for v in values] for values in grouped[measure].to_list()]

    summary = pl.DataFrame(
        {
            group: labels,
            "n": [len(values) for values in raw],
            "min": [min(values) for values in raw],
            "q25": [_quantile(values, 0.25) for values in raw],
            "median": [_quantile(values, 0.5) for values in raw],
            "q75": [_quantile(values, 0.75) for values in raw],
            "max": [max(values) for values in raw],
        }
    ).sort(["median", group], descending=[True, False])

    outcome = ToolOutcome()
    outcome.table(summary, f"distribution_by_{group}")
    outcome.metric(
        f"{group}__median_spread",
        float(summary["median"].max() - summary["median"].min()),
        definition=f"Spread between the highest and lowest group median of '{measure}'.",
        n=int(summary["n"].sum()),
        unit=params.get("unit"),
    )
    outcome.metrics.append(
        _metric(
            f"{group}__widest_spread",
            str(summary[group][0]),
            definition=f"Group with the widest range of '{measure}'.",
            n=int(summary["n"][0]),
        )
    )
    outcome.warnings.append(
        "Group sizes differ in this comparison; a wide box may reflect a small sample "
        "rather than a genuinely variable group."
    )
    outcome.charts.append(
        ChartRequest(
            chart_id=derive_id("chart", {"tool": "distribution_by_group", "group": group}),
            kind=ChartKind.BOX,
            title=f"Distribution of {measure} by {group}",
            series=[
                ChartSeries(
                    name=measure,
                    x=labels,
                    y=[_quantile(values, 0.5) for values in raw],
                    raw=raw,
                )
            ],
            x_label=group,
            y_label=measure,
            y_unit=params.get("unit"),
            caption=(
                f"{grouped.height} {group} values, {subset.height:,} observations in total."
            ),
            caveat=(
                "Boxes show the interquartile range and the line the median. Group sizes "
                "differ, so visual width is not comparable across groups."
            ),
        )
    )
    return outcome


def _quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise _empty_column("values")
    position = fraction * (len(ordered) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _metric(
    name: str,
    value: float | int | str | None,
    definition: str,
    n: int | None = None,
) -> MetricValue:
    return MetricValue(name=name, value=value, definition=definition, n=n)


def _no_measures() -> ToolExecutionError:
    return ToolExecutionError(
        "no numeric columns are available for descriptive statistics",
        retryable=False,
        suggestion="Parse or supply at least one numeric column.",
    )


def _empty_column(column: str) -> ToolExecutionError:
    return ToolExecutionError(
        "column has no non-null values",
        column=column,
        retryable=False,
        suggestion="Impute, filter or choose a different column.",
    )


def _degenerate(column: str, value: float) -> ToolExecutionError:
    return ToolExecutionError(
        "column has a single distinct value; no distribution to analyse",
        column=column,
        value=value,
        retryable=False,
    )
