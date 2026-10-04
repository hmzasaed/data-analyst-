"""Comparative analysis: group differences, shares and period-over-period change.

Differences are reported with the test that produced them and its assumptions.
Welch's t-test is used rather than Student's because group variances in business
data are rarely equal, and a non-parametric fallback runs when the normality
assumption is clearly violated.
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

#: Below this many observations per group, a significance test is not reported.
MIN_GROUP_FOR_TEST = 8

#: Normality check threshold: below this p-value the data is treated as non-normal.
NORMALITY_ALPHA = 0.01


def compare_groups(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Compare a measure across groups, with a Welch test and effect size."""
    ctx.guard_row_count()
    group = params["group_by"]
    measure = params["column"]
    ctx.category_column(group)
    ctx.numeric_column(measure)

    distinct = ctx.frame.get_column(group).n_unique()
    if distinct < 2:
        raise ToolExecutionError(
            "comparison needs at least two groups",
            group_by=group,
            distinct=int(distinct),
            retryable=False,
        )
    if distinct > int(params.get("max_groups", 50)):
        raise ToolExecutionError(
            "too many groups to compare",
            group_by=group,
            distinct=int(distinct),
            limit=int(params.get("max_groups", 50)),
            retryable=False,
        )

    frame = (
        ctx.frame.select([group, measure])
        .drop_nulls()
        .group_by(group)
        .agg(
            pl.len().alias("n"),
            pl.col(measure).mean().alias("mean"),
            pl.col(measure).median().alias("median"),
            pl.col(measure).std().alias("std"),
            pl.col(measure).min().alias("min"),
            pl.col(measure).max().alias("max"),
            pl.col(measure).sum().alias("total"),
        )
        .sort(["mean", group], descending=[True, False])
    )
    if frame.height == 0:
        raise ToolExecutionError(
            "no rows remain after dropping nulls", group_by=group, column=measure, retryable=False
        )

    largest = frame.head(1)
    smallest = frame.tail(1)
    outcome = ToolOutcome()

    gap = float(largest["mean"][0]) - float(smallest["mean"][0])
    pooled = float(frame["mean"].std() or 0.0)
    outcome.metric(
        "group_gap_mean",
        gap,
        definition=(
            f"Difference between the highest and lowest group mean of '{measure}' "
            f"({largest[group][0]} vs {smallest[group][0]})."
        ),
        n=int(frame["n"].sum()),
        unit=params.get("unit"),
    )
    outcome.metric(
        "largest_group_mean",
        float(largest["mean"][0]),
        definition=f"Highest mean of '{measure}', for group '{largest[group][0]}'.",
        n=int(largest["n"][0]),
        unit=params.get("unit"),
    )
    outcome.metric(
        "smallest_group_mean",
        float(smallest["mean"][0]),
        definition=f"Lowest mean of '{measure}', for group '{smallest[group][0]}'.",
        n=int(smallest["n"][0]),
        unit=params.get("unit"),
    )
    if pooled > 0:
        outcome.metric(
            "group_gap_in_sd_units",
            gap / pooled,
            definition=(
                "Group gap expressed in standard deviations of the group means "
                "(a rough standardised effect size)."
            ),
            n=int(frame["n"].sum()),
        )

    test = _welch(ctx, group, measure, frame)
    # The statistical metrics belong to the narrow test table, not the summary one.
    outcome.metrics.extend(
        metric.model_copy(update={"table": f"group_test_{group}"}) for metric in test.metrics
    )
    outcome.warnings.extend(test.warnings)

    # Every figure this tool reports is also present in the table it writes, so a
    # reader (or the verifier) can check the number without recomputing it.
    # `shortfall_vs_top_mean` is non-negative, so the worst group's value equals the
    # reported `group_gap_mean` exactly. The summary table is written first so
    # findings cite it in preference to the narrower statistical one.
    best = float(frame["mean"].max())
    result = frame.with_columns(
        (best - pl.col("mean")).alias("shortfall_vs_top_mean"),
        (
            (pl.col("mean") - float(frame["mean"].mean())) / pooled if pooled > 0 else pl.lit(0.0)
        ).alias("z_vs_group_mean"),
    )
    outcome.table(result, f"group_comparison_{group}")
    if test.table is not None:
        # The statistical claim is evidence too, so it is written out with the same
        # p-value and effect size the metrics report.
        outcome.table(test.table, f"group_test_{group}")

    if params.get("chart", True):
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "compare_groups", "group": group, "column": measure}),
                kind=ChartKind.HORIZONTAL_BAR if frame.height > 8 else ChartKind.BAR,
                title=params.get("title") or f"{measure} by {group}",
                series=[
                    ChartSeries(
                        name="mean",
                        x=[str(v) for v in result[group].to_list()],
                        y=[float(v) for v in result["mean"].to_list()],
                    )
                ],
                x_label=group,
                y_label=f"mean {measure}",
                y_unit=params.get("unit"),
                caption=(
                    f"Mean of '{measure}' across {frame.height} {group} values; "
                    f"group sizes range from {int(frame['n'].min())} to {int(frame['n'].max())} rows."
                ),
                caveat=(
                    "Means are sensitive to outliers. Check the accompanying table for group sizes "
                    "and medians before relying on small differences."
                ),
            )
        )
    return outcome


class _TestOutcome:
    def __init__(self) -> None:
        self.metrics = []
        self.warnings: list[str] = []
        self.table: pl.DataFrame | None = None


def _welch(ctx: AnalysisContext, group: str, measure: str, frame: pl.DataFrame) -> _TestOutcome:
    """Welch's t-test on the top two groups, with a normality check."""
    outcome = _TestOutcome()
    top = frame.head(2)
    if frame.height < 2:
        outcome.warnings.append("Only one group remains after filtering; no comparison is possible.")
        return outcome

    a = _values(ctx.frame, group, top[group][0], measure)
    b = _values(ctx.frame, group, top[group][1], measure)

    if min(a.size, b.size) < MIN_GROUP_FOR_TEST:
        outcome.warnings.append(
            f"Groups '{top[group][0]}' (n={a.size}) and '{top[group][1]}' (n={b.size}) are too small "
            f"for a significance test (minimum {MIN_GROUP_FOR_TEST}); only descriptive "
            "differences are reported."
        )
        outcome.metrics.append(
            _metric(
                "group_difference_p_value",
                None,
                "Not computed: at least one group has fewer than "
                f"{MIN_GROUP_FOR_TEST} observations.",
            )
        )
        return outcome

    test_name = "welch_t_test"
    try:
        _, p_value = stats.ttest_ind(a, b, equal_var=False)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ToolExecutionError(
            "group comparison failed", group_by=group, column=measure, cause=str(exc), retryable=False
        ) from exc

    normality_p = min(_normality_p(a), _normality_p(b))
    if normality_p < NORMALITY_ALPHA:
        try:
            _, p_value = stats.mannwhitneyu(a, b, alternative="two-sided")
            test_name = "mann_whitney_u"
            outcome.warnings.append(
                "Values deviate strongly from normality "
                f"(Shapiro p={normality_p:.2g} < {NORMALITY_ALPHA}), so a rank-based test "
                "was used instead of a t-test."
            )
        except ValueError:  # pragma: no cover - defensive
            test_name = "welch_t_test"

    pooled_std = math.sqrt((a.var(ddof=1) + b.var(ddof=1)) / 2) if a.size > 1 and b.size > 1 else 0.0
    cohens_d = (a.mean() - b.mean()) / pooled_std if pooled_std > 0 else None

    outcome.metrics.extend(
        [
            _metric(
                "group_difference_p_value",
                float(p_value),
                f"{test_name} on '{measure}' for groups '{top[group][0]}' and '{top[group][1]}'.",
                n=int(a.size + b.size),
            ),
            _metric(
                "group_difference_cohens_d",
                float(cohens_d) if cohens_d is not None else None,
                "Standardised mean difference between the two highest-mean groups.",
                n=int(a.size + b.size),
            ),
            _metric(
                "group_difference_test",
                test_name,
                "Statistical test used for the reported p-value.",
                n=int(a.size + b.size),
            ),
        ]
    )
    outcome.table = pl.DataFrame(
        [
            {
                "group_a": top[group][0],
                "n_a": int(a.size),
                "mean_a": float(a.mean()),
                "median_a": float(np.median(a)),
            },
            {
                "group_b": top[group][1],
                "n_b": int(b.size),
                "mean_b": float(b.mean()),
                "median_b": float(np.median(b)),
            },
        ]
    ).with_columns(
        pl.lit(test_name).alias("test"),
        pl.lit(float(p_value)).alias("p_value"),
        pl.lit(float(cohens_d) if cohens_d is not None else None).alias("cohens_d"),
    )
    return outcome


def _values(frame: pl.DataFrame, group: str, value: str, measure: str) -> np.ndarray:
    return (
        frame.filter(pl.col(group) == value)
        .get_column(measure)
        .cast(pl.Float64, strict=False)
        .drop_nulls()
        .to_numpy()
        .astype("float64", copy=False)
    )


def _normality_p(values: np.ndarray) -> float:
    """Shapiro-Wilk p-value, or 1.0 when the sample is too small to test."""
    if values.size < 3 or values.size > 5000:
        return 1.0
    with np.errstate(all="ignore"):
        try:
            return float(stats.shapiro(values).pvalue)
        except Exception:  # pragma: no cover - defensive
            return 1.0


def _metric(
    name: str,
    value: float | int | str | None,
    definition: str,
    n: int | None = None,
    unit: str | None = None,
    table: str | None = None,
) -> MetricValue:
    return MetricValue(
        name=name, value=value, definition=definition, n=n, unit=unit, table=table
    )


def share_of_total(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Contribution of each group to the overall total, in percent."""
    ctx.guard_row_count()
    group = params["group_by"]
    measure = params["column"]
    ctx.category_column(group)
    ctx.numeric_column(measure)

    totals = ctx.frame.get_column(measure).cast(pl.Float64, strict=False).drop_nulls()
    grand_total = float(totals.sum())
    if math.isclose(grand_total, 0.0):
        raise ToolExecutionError(
            "total is zero, so contribution shares are undefined",
            column=measure,
            retryable=False,
        )

    frame = (
        ctx.frame.select([group, measure])
        .drop_nulls()
        .group_by(group)
        .agg(
            pl.col(measure).sum().alias("total"),
            pl.len().alias("n"),
        )
        .with_columns((pl.col("total") / grand_total).alias("share"))
        .sort(["total", group], descending=[True, False])
    )
    frame = frame.with_columns(pl.col("share").cum_sum().alias("cumulative_share"))
    # Percentages are reported as metrics and written into the same table so that
    # each claimed figure can be checked against its artifact without rescaling.
    frame = frame.with_columns(
        (pl.col("share") * 100.0).alias("share_pct"),
        (pl.col("cumulative_share") * 100.0).alias("cumulative_share_pct"),
        pl.lit(grand_total).alias("observed_grand_total"),
    )

    outcome = ToolOutcome()
    outcome.table(frame, f"contribution_{group}")
    outcome.metric(
        "grand_total", grand_total, definition=f"Sum of '{measure}' across all analysed rows.",
        n=int(totals.len()), unit=params.get("unit"), table=f"contribution_{group}",
    )
    top = frame.head(min(3, frame.height))
    outcome.metric(
        "top3_share",
        float(top["share"].sum()) * 100.0,
        definition=f"Share of total '{measure}' contributed by the top {len(top)} '{group}' values.",
        n=int(top["n"].sum()),
        unit="%",
        table=f"contribution_{group}",
    )
    outcome.metrics.append(
        _metric(
            "contribution_concentration",
            float(top["cumulative_share"].max()) * 100.0,
            f"Cumulative share of '{measure}' held by the top {len(top)} '{group}' values.",
            n=int(top["n"].sum()),
            unit="%",
            table=f"contribution_{group}",
        )
    )
    if params.get("chart", True):
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "share_of_total", "group": group, "column": measure}),
                kind=ChartKind.HORIZONTAL_BAR if frame.height > 8 else ChartKind.BAR,
                title=params.get("title") or f"Share of total {measure} by {group}",
                series=[
                    ChartSeries(
                        name="share",
                        x=[str(v) for v in frame[group].to_list()],
                        y=[float(v) * 100 for v in frame["share"]],
                    )
                ],
                x_label=group,
                y_label=f"share of total {measure}",
                y_unit="%",
                caption=f"{len(frame)} groups; shares sum to 100% of the analysed rows.",
                caveat="Groups with null measures are excluded from both numerator and denominator.",
            )
        )
    return outcome


def missingness_by_group(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Compare data completeness across groups - often the real reason totals differ."""
    ctx.guard_row_count()
    group = params["group_by"]
    ctx.category_column(group)

    frame = (
        ctx.frame.group_by(group)
        .agg(pl.len().alias("rows"))
        .sort(["rows", group], descending=[True, False])
    )
    outcome = ToolOutcome()
    rows = []
    for value in frame[group].to_list():
        subset = ctx.frame.filter(pl.col(group) == value)
        rows.append(
            {
                group: value,
                "rows": subset.height,
                "cells": subset.width * subset.height,
                "missing_cells": sum(
                    int(subset.get_column(name).null_count()) for name in subset.columns
                ),
            }
        )
    detail = pl.DataFrame(rows).with_columns(
        (pl.col("missing_cells") / pl.col("cells")).alias("missing_share")
    )
    outcome.table(detail, f"missingness_by_{group}")
    if len(detail):
        worst = detail.sort("missing_share", descending=True).head(1)
        outcome.metrics.append(
            _metric(
                "worst_group_missing_share",
                float(worst["missing_share"][0]) * 100.0,
                f"Highest share of missing cells within a single '{group}' value.",
                n=len(detail),
            )
        )
    return outcome
