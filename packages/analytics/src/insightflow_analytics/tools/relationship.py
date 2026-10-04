"""Relationship analysis: correlation, cross-tabulation and simple regression.

Correlation is reported with its sample size and a warning whenever n is small
enough that the coefficient is unstable. Regression is only offered with an
explanation of what it does and does not license.
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

#: Correlation below this sample size is reported but explicitly marked unstable.
MIN_CORRELATION_SAMPLE = 20
#: Minimum rows for a regression to be attempted at all.
MIN_REGRESSION_ROWS = 12


def correlation_matrix(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Pearson correlation between numeric columns, with a sample-size caveat."""
    ctx.guard_row_count()
    columns: list[str] = list(params["columns"])
    for column in columns:
        ctx.numeric_column(column)
    if len(columns) < 2:
        raise ToolExecutionError(
            "correlation needs at least two numeric columns",
            provided=columns,
            retryable=False,
        )

    clean = ctx.frame.select(columns).drop_nulls()
    n = clean.height
    outcome = ToolOutcome()
    if n < 3:
        raise ToolExecutionError(
            "not enough complete rows to correlate", complete_rows=int(n), retryable=False
        )

    matrix = np.full((len(columns), len(columns)), np.nan)
    for i, left in enumerate(columns):
        for j, right in enumerate(columns):
            if j < i:
                matrix[i, j] = matrix[j, i]
                continue
            a = clean.get_column(left).cast(pl.Float64).to_numpy()
            b = clean.get_column(right).cast(pl.Float64).to_numpy()
            if np.std(a) == 0 or np.std(b) == 0:
                continue
            matrix[i, j] = float(np.corrcoef(a, b)[0, 1])

    frame = pl.DataFrame(
        {columns[i]: [None if math.isnan(v) else float(v) for v in matrix[i]] for i in range(len(columns))},
        orient="row",
    ).with_columns(pl.Series("column", columns))
    outcome.table(frame, "correlation_matrix")

    pairs = _strongest_pairs(columns, matrix, limit=int(params.get("limit", 10)))
    for left, right, value in pairs:
        outcome.metrics.append(
            MetricValue(
                name=f"correlation__{left}__{right}",
                value=value,
                definition=(
                    f"Pearson correlation between '{left}' and '{right}', on complete rows only."
                ),
                n=int(n),
            )
        )
    strongest = pairs[0] if pairs else None
    if strongest:
        outcome.metrics.append(
            MetricValue(
                name="strongest_correlation",
                value=strongest[2],
                definition=(
                    f"Largest absolute correlation found: '{strongest[0]}' and '{strongest[1]}'."
                ),
                n=int(n),
            )
        )
    if n < MIN_CORRELATION_SAMPLE:
        outcome.warnings.append(
            f"Correlations are computed on {n} complete rows, below the {MIN_CORRELATION_SAMPLE} "
            "rows usually needed for a stable coefficient. Treat these numbers as exploratory."
        )
    outcome.warnings.append(
        "Correlation measures association only. It does not establish that either variable "
        "causes the other, and a high correlation can arise from a shared third factor."
    )

    if params.get("chart", True) and len(columns) > 1:
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "correlation_matrix", "columns": columns}),
                kind=ChartKind.HEATMAP,
                title="Pairwise correlation",
                series=[],
                matrix=[[None if math.isnan(v) else float(v) for v in row] for row in matrix],
                row_labels=columns,
                column_labels=columns,
                color_limits=(-1.0, 1.0),
                x_label=columns[0] if columns else "",
                y_label="Pearson r (-1 to 1)",
                caption=f"Computed on {n:,} complete rows across {len(columns)} numeric columns.",
                caveat="Pearson r assumes a roughly linear relationship and is sensitive to outliers.",
            )
        )
    return outcome


def _strongest_pairs(
    columns: list[str], matrix: np.ndarray, *, limit: int
) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(columns)):
        for j in range(i + 1, len(columns)):
            value = matrix[i, j]
            if not math.isnan(value):
                pairs.append((columns[i], columns[j], float(value)))
    pairs.sort(key=lambda item: abs(item[2]), reverse=True)
    return pairs[:limit]


def cross_tabulation(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Two-way counts of a measure between two categorical columns."""
    ctx.guard_row_count()
    row_dim = params["row_dimension"]
    column_dim = params["column_dimension"]
    measure = params.get("measure")
    ctx.category_column(row_dim)
    ctx.category_column(column_dim)

    if measure:
        ctx.numeric_column(measure)

    aggregations = [pl.len().alias("rows")]
    if measure:
        aggregations.append(pl.col(measure).sum().alias("measure_total"))
        aggregations.append(pl.col(measure).mean().alias("measure_mean"))
    value_field = "measure_total" if measure else "rows"

    frame = (
        ctx.frame.group_by([row_dim, column_dim])
        .agg(aggregations)
        .sort([row_dim, column_dim])
    )
    outcome = ToolOutcome()
    outcome.table(frame, f"crosstab_{row_dim}_x_{column_dim}")

    pivoted = frame.pivot(on=column_dim, index=row_dim, values=value_field, aggregate_function="first")
    outcome.metrics.append(
        MetricValue(
            name="crosstab_cell_count",
            value=int(frame.height),
            definition=f"Non-empty combinations of '{row_dim}' and '{column_dim}'.",
            n=int(ctx.frame.height),
            unit="cells",
        )
    )
    if len(pivoted.columns) > 2 and len(pivoted) > 1:
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "cross_tabulation", "row": row_dim, "column": column_dim}),
                kind=ChartKind.BAR,
                title=params.get("title") or f"{value_field} by {row_dim} and {column_dim}",
                series=[
                    ChartSeries(
                        name=str(name),
                        x=[str(v) for v in pivoted[row_dim].to_list()],
                        y=[_to_float(v) for v in pivoted[name].to_list()],
                    )
                    for name in pivoted.columns
                    if name != row_dim
                ][:10],
                x_label=row_dim,
                y_label=value_field,
                y_unit=params.get("unit"),
                caption=f"{len(pivoted)} {row_dim} values across {len(pivoted.columns) - 1} {column_dim} values.",
                caveat="At most 10 series are plotted; the table artifact holds the full cross-tabulation.",
            )
        )
    return outcome


def simple_regression(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Ordinary least squares of one measure on another, with assumption checks."""
    response = params["response"]
    predictor = params["predictor"]
    ctx.numeric_column(response)
    ctx.numeric_column(predictor)

    frame = ctx.frame.select([response, predictor]).drop_nulls()
    n = frame.height
    if n < MIN_REGRESSION_ROWS:
        raise ToolExecutionError(
            "too few complete rows for a regression",
            required=MIN_REGRESSION_ROWS,
            available=int(n),
            retryable=False,
        )

    y = frame.get_column(response).cast(pl.Float64).to_numpy()
    x = frame.get_column(predictor).cast(pl.Float64).to_numpy()
    if np.std(x) == 0:
        raise ToolExecutionError(
            "predictor has no variation", predictor=predictor, retryable=False
        )

    result = stats.linregress(x, y)
    fitted = result.intercept + result.slope * x
    residuals = y - fitted
    rmse = float(np.sqrt(np.mean(residuals**2)))

    outcome = ToolOutcome()
    outcome.metrics.extend(
        [
            MetricValue(
                name="regression_slope",
                value=float(result.slope),
                definition=(
                    f"Change in '{response}' per unit of '{predictor}' from an ordinary "
                    "least-squares fit."
                ),
                n=int(n),
            ),
            MetricValue(
                name="regression_intercept",
                value=float(result.intercept),
                definition=f"Predicted '{response}' when '{predictor}' is zero.",
                n=int(n),
            ),
            MetricValue(
                name="regression_r_squared",
                value=float(result.rvalue**2),
                definition="Share of variation in the response explained by the predictor.",
                n=int(n),
            ),
            MetricValue(
                name="regression_p_value",
                value=float(result.pvalue),
                definition=(
                    "Two-sided p-value for a zero slope. It says nothing about whether the "
                    "relationship is causal."
                ),
                n=int(n),
            ),
            MetricValue(
                name="regression_rmse",
                value=rmse,
                definition="Root mean squared error of the fit, in the response's own units.",
                n=int(n),
            ),
        ]
    )
    outcome.table(
        pl.DataFrame({predictor: x, response: y, "fitted": fitted, "residual": residuals}),
        f"regression_{predictor}_on_{response}",
    )

    # Residual normality: a violation means the standard error is unreliable.
    if 3 <= n <= 5000:
        residual_p = float(stats.shapiro(residuals).pvalue)
        if residual_p < 0.01:
            outcome.warnings.append(
                f"Residuals are not normally distributed (Shapiro p={residual_p:.2g}); the reported "
                "standard errors should be treated as approximate."
            )
    outcome.warnings.append(
        "This is an association between two columns. Omitting a confounding variable, or the "
        "predictor being measured after the response, would both produce the same numbers, so "
        "the slope must not be read as an effect."
    )

    if params.get("chart", True):
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "simple_regression", "predictor": predictor, "response": response}),
                kind=ChartKind.SCATTER,
                title=params.get("title") or f"{response} vs {predictor}",
                series=[
                    ChartSeries(
                        name="observed",
                        x=[float(v) for v in x],
                        y=[float(v) for v in y],
                    ),
                    ChartSeries(
                        name="fitted line",
                        x=[float(x.min()), float(x.max())],
                        y=[
                            float(result.intercept + result.slope * x.min()),
                            float(result.intercept + result.slope * x.max()),
                        ],
                    ),
                ],
                x_label=predictor,
                y_label=response,
                y_unit=params.get("unit"),
                caption=f"Ordinary least squares on {n:,} complete rows.",
                caveat=(
                    "The line summarises association. Overlapping points are hidden; the row count "
                    "above is the true sample size."
                ),
            )
        )
    return outcome


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(as_float) else as_float
