"""Time-series analysis: aggregation to a grain, trend, growth and change points.

Two rules run through everything here:

* a time series with too few periods cannot support trend or seasonality claims, so
  the tools state that rather than producing a coefficient anyway;
* growth rates are undefined when the previous period is zero or missing, and are
  reported as null with an explicit count of how often that happened.
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

from insightflow_analytics.context import (
    AnalysisContext,
    ToolOutcome,
    is_temporal_dtype,
    is_year_series,
    year_to_date,
)

TOOL_VERSION = "1"

#: Minimum periods before a trend line is fitted at all.
MIN_PERIODS_FOR_TREND = 4
#: Minimum periods before growth rates are reported as meaningful.
MIN_PERIODS_FOR_GROWTH = 3
#: Minimum periods before a seasonal pattern is claimed.
MIN_PERIODS_FOR_SEASONALITY = 24

GRAIN_TRUNCATION = {
    "daily": pl.col("_period").dt.truncate("1d"),
    "weekly": pl.col("_period").dt.truncate("1w"),
    "monthly": pl.col("_period").dt.truncate("1mo"),
    "quarterly": pl.col("_period").dt.truncate("1q"),
    "yearly": pl.col("_period").dt.truncate("1y"),
}


def _period_expr(column: str) -> pl.Expr:
    dtype_kind = column
    if dtype_kind not in GRAIN_TRUNCATION:  # pragma: no cover - guarded by validator
        raise ToolExecutionError("unsupported date column", column=column, retryable=False)
    return pl.col(column)


def time_series_aggregate(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Aggregate a measure over time at a chosen grain, with period-over-period change."""
    ctx.guard_row_count()
    date_column = params["date_column"]
    grain = params["grain"]
    measures: list[str] = list(params.get("measures") or [])

    ctx.require_columns(date_column)
    frame = ctx.frame
    dtype = frame.schema[date_column]
    year_column = is_year_series(frame.get_column(date_column))
    if year_column:
        frame = frame.with_columns(year_to_date(date_column))
        dtype = frame.schema[date_column]
        if grain != "yearly":
            # A year carries no finer detail; a monthly grain would only relabel years.
            grain = "yearly"
    elif dtype == pl.String:
        frame = frame.with_columns(pl.col(date_column).str.to_datetime(strict=False))
        dtype = frame.schema[date_column]
    if not is_temporal_dtype(dtype):
        raise ToolExecutionError(
            "date column could not be interpreted",
            column=date_column,
            dtype=str(dtype),
            retryable=False,
        )

    for measure in measures:
        ctx.numeric_column(measure)

    working = frame.with_columns(pl.col(date_column).cast(pl.Datetime).alias("_period"))
    truncated = GRAIN_TRUNCATION[grain]
    working = working.with_columns(truncated.alias("_period"))

    aggregations = [pl.len().alias("rows")]
    for measure in measures:
        aggregations.extend(
            [
                pl.col(measure).sum().alias(f"{measure}_total"),
                pl.col(measure).mean().alias(f"{measure}_mean"),
                pl.col(measure).median().alias(f"{measure}_median"),
                pl.col(measure).null_count().alias(f"{measure}_missing"),
            ]
        )

    grouped = working.group_by("_period").agg(aggregations).sort("_period")
    if grouped.height == 0:
        raise ToolExecutionError(
            "no rows after parsing the date column", column=date_column, retryable=False
        )

    primary = measures[0] if measures else None
    if primary:
        grouped = grouped.with_columns(
            pl.col(f"{primary}_total").cast(pl.Float64).shift(1).alias("_previous"),
            pl.col(f"{primary}_total").cast(pl.Float64).alias("_current"),
        )
        grouped = grouped.with_columns(
            pl.when(pl.col("_previous").abs() < 1e-12)
            .then(pl.lit(None, dtype=pl.Float64))
            .otherwise((pl.col("_current") - pl.col("_previous")) / pl.col("_previous").abs())
            .alias(f"{primary}_change_pct")
        )

    keep = ["_period", "rows"]
    for measure in measures:
        keep.extend(
            [f"{measure}_total", f"{measure}_mean", f"{measure}_median", f"{measure}_missing"]
        )
    if primary:
        keep.append(f"{primary}_change_pct")
    result = grouped.select(keep)

    outcome = ToolOutcome()
    outcome.table(result, f"trend_{grain}")
    if year_column and grain != params["grain"]:
        outcome.warnings.append(
            f"'{date_column}' holds whole years, so it was aggregated yearly rather than "
            f"{params['grain']}."
        )

    outcome.metrics.append(
        MetricValue(
            name="period_count",
            value=int(grouped.height),
            definition=f"Number of {grain} periods present in the data.",
            n=int(ctx.frame.height),
            unit="periods",
        )
    )
    if grouped.height < MIN_PERIODS_FOR_TREND:
        outcome.warnings.append(
            f"Only {grouped.height} {grain} period(s) are present; trend, growth and seasonality "
            "cannot be assessed. The series is shown as-is."
        )

    if primary:
        totals = grouped.get_column(f"{primary}_total").cast(pl.Float64).to_list()
        first_valid = next((v for v in totals if v is not None and not math.isnan(v)), None)
        last_valid = next((v for v in reversed(totals) if v is not None and not math.isnan(v)), None)
        outcome.metrics.append(
            MetricValue(
                name=f"{primary}__first_period_total",
                value=first_valid,
                definition=f"First non-null {grain} total of '{primary}'.",
                n=int(grouped.height),
                unit=params.get("unit"),
            )
        )
        outcome.metrics.append(
            MetricValue(
                name=f"{primary}__last_period_total",
                value=last_valid,
                definition=f"Most recent non-null {grain} total of '{primary}'.",
                n=int(grouped.height),
                unit=params.get("unit"),
            )
        )
        change = _overall_change(first_valid, last_valid)
        outcome.metrics.append(
            MetricValue(
                name=f"{primary}__overall_change_pct",
                value=change,
                definition=(
                    f"Change in '{primary}' total between the first and last non-null "
                    f"{grain} period."
                ),
                n=int(grouped.height),
                unit="%",
            )
        )
        outcome.charts.append(
            ChartRequest(
                chart_id=derive_id("chart", {"tool": "time_series", "column": primary, "grain": grain}),
                kind=ChartKind.LINE,
                title=params.get("title") or f"{primary} by {grain}",
                series=[
                    ChartSeries(
                        name=primary,
                        x=[_label(p) for p in grouped["_period"].to_list()],
                        y=[_nan_to_none(v) for v in grouped[f"{primary}_total"].to_list()],
                    )
                ],
                x_label=grain,
                y_label=primary,
                y_unit=params.get("unit"),
                caption=(
                    f"{grouped.height} {grain} periods covering "
                    f"{_label(grouped['_period'][0])} to {_label(grouped['_period'][-1])}."
                ),
                caveat=(
                    "Periods with missing measures are plotted as gaps, not as zero. "
                    "Totals are affected by the number of rows in each period."
                ),
            )
        )
    return outcome


def trend_line(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Fit a linear trend to an already-aggregated series and report the slope."""
    frame = ctx.frame
    date_column = params["date_column"]
    measure = params["column"]
    ctx.require_columns(date_column)
    ctx.numeric_column(measure)

    ordered = frame.sort(date_column)
    values = ordered.get_column(measure).cast(pl.Float64, strict=False).drop_nulls().to_numpy()
    if values.size < MIN_PERIODS_FOR_TREND:
        raise ToolExecutionError(
            "too few periods to fit a trend",
            required=MIN_PERIODS_FOR_TREND,
            available=int(values.size),
            retryable=False,
        )

    x = np.arange(values.size, dtype="float64")
    slope, intercept, r_value, p_value, std_err = stats.linregress(x, values)

    outcome = ToolOutcome()
    outcome.metrics.extend(
        [
            MetricValue(
                name="trend_slope_per_period",
                value=float(slope),
                definition=(
                    f"Average change in '{measure}' per period from an ordinary least-squares fit."
                ),
                n=int(values.size),
                unit=params.get("unit"),
            ),
            MetricValue(
                name="trend_slope_p_value",
                value=float(p_value),
                definition="Two-sided p-value for a zero slope. Association only, not causation.",
                n=int(values.size),
            ),
            MetricValue(
                name="trend_r_squared",
                value=float(r_value**2),
                definition="Share of period-to-period variation explained by the linear fit.",
                n=int(values.size),
            ),
            MetricValue(
                name="trend_slope_standard_error",
                value=float(std_err),
                definition="Standard error of the fitted slope.",
                n=int(values.size),
            ),
        ]
    )

    fitted = intercept + slope * x
    outcome.table(
        pl.DataFrame(
            {
                "period": x.astype("int64"),
                params["date_column"]: ordered.get_column(date_column).to_list(),
                "observed": values,
                "fitted": fitted,
            }
        ),
        "trend_fit",
    )
    if r_value**2 < 0.3:
        outcome.warnings.append(
            f"The linear trend explains only {r_value**2:.0%} of the variation in '{measure}'; "
            "treat the slope as descriptive noise rather than a directional change."
        )
    outcome.warnings.append(
        "A linear trend assumes a constant direction of change; seasonality and structural "
        "breaks are not modelled."
    )
    outcome.charts.append(
        ChartRequest(
            chart_id=derive_id("chart", {"tool": "trend_line", "column": measure}),
            kind=ChartKind.LINE,
            title=f"{measure} with fitted trend",
            series=[
                ChartSeries(
                    name="observed",
                    x=[_label(p) for p in ordered.get_column(date_column).to_list()],
                    y=[_nan_to_none(v) for v in values],
                ),
                ChartSeries(
                    name="fitted trend",
                    x=[_label(p) for p in ordered.get_column(date_column).to_list()],
                    y=[float(v) for v in fitted],
                ),
            ],
            x_label=params.get("x_label", "period"),
            y_label=measure,
            y_unit=params.get("unit"),
            caption=f"Ordinary least squares over {values.size} observations.",
            caveat="The fitted line is a description of the observed shape, not an explanation.",
        )
    )
    return outcome


def growth_rates(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Period-over-period growth, with an explicit count of undefined periods."""
    date_column = params["date_column"]
    measure = params["column"]
    ctx.require_columns(date_column)
    ctx.numeric_column(measure)

    ordered = ctx.frame.sort(date_column).select([date_column, measure])
    current = ordered.get_column(measure).cast(pl.Float64, strict=False)
    previous = current.shift(1)

    growth = pl.when(previous.abs() < 1e-12).then(pl.lit(None, dtype=pl.Float64)).otherwise(
        (current - previous) / previous.abs()
    )
    frame = ordered.with_columns(growth.alias("growth")).with_columns(
        pl.col("growth").mul(100).alias("growth_pct")
    )

    valid = frame["growth"].drop_nulls()
    outcome = ToolOutcome()
    outcome.table(frame, "growth_rates")

    undefined = int(frame.height - valid.len() - 1) if frame.height else 0
    outcome.metrics.extend(
        [
            MetricValue(
                name="growth_periods_computed",
                value=int(valid.len()),
                definition=f"Periods where '{measure}' changed against a non-zero previous value.",
                n=int(frame.height),
                unit="periods",
            ),
            MetricValue(
                name="growth_periods_undefined",
                value=max(undefined, 0),
                definition=(
                    "Periods excluded from the growth calculation because the previous value was "
                    "missing or zero."
                ),
                n=int(frame.height),
                unit="periods",
            ),
            MetricValue(
                name="mean_growth_pct",
                value=float(valid.mean() * 100) if valid.len() else None,
                definition=f"Mean period-over-period growth of '{measure}'.",
                n=int(valid.len()),
                unit="%",
            ),
        ]
    )
    if undefined > 0:
        outcome.warnings.append(
            f"{undefined} period(s) had a missing or zero previous value and are excluded from "
            "growth statistics."
        )
    if valid.len() >= MIN_PERIODS_FOR_GROWTH:
        outcome.metrics.append(
            MetricValue(
                name="growth_volatility_sd_pct",
                value=float(valid.std() * 100),
                definition="Standard deviation of period-over-period growth.",
                n=int(valid.len()),
                unit="%",
            )
        )
    return outcome


def moving_average(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Rolling mean over an ordered series, keeping NaN where history is short."""
    date_column = params["date_column"]
    measure = params["column"]
    window = int(params.get("window", 3))
    ctx.require_columns(date_column)
    ctx.numeric_column(measure)

    ordered = ctx.frame.sort(date_column).select([date_column, measure])
    with_rolling = ordered.with_columns(
        pl.col(measure)
        .cast(pl.Float64, strict=False)
        .rolling_mean(window_size=window, min_periods=1)
        .alias("rolling_mean")
    )

    outcome = ToolOutcome()
    outcome.table(with_rolling, "moving_average")
    outcome.metrics.append(
        MetricValue(
            name="moving_average_window",
            value=window,
            definition="Number of trailing periods included in each rolling mean.",
            n=int(ordered.height),
            unit="periods",
        )
    )
    series = ordered.get_column(measure).cast(pl.Float64, strict=False)
    outcome.metrics.append(
        MetricValue(
            name="series_volatility_sd",
            value=float(series.std()) if series.len() > 1 else None,
            definition=f"Standard deviation of '{measure}' across all periods.",
            n=int(series.len()),
            unit=params.get("unit"),
        )
    )
    if window > len(ordered):
        outcome.warnings.append(
            f"The window ({window}) is longer than the series ({len(ordered)}), so the rolling "
            "mean is effectively the overall mean."
        )
    return outcome


def seasonality_strength(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Report whether the series is long enough to assess seasonality, and assess it."""
    date_column = params["date_column"]
    measure = params["column"]
    ctx.require_columns(date_column)
    ctx.numeric_column(measure)

    ordered = ctx.frame.sort(date_column)
    values = ordered.get_column(measure).cast(pl.Float64, strict=False).drop_nulls().to_numpy()

    outcome = ToolOutcome()
    if values.size < MIN_PERIODS_FOR_SEASONALITY:
        outcome.metrics.append(
            MetricValue(
                name="seasonality_assessed",
                value=0,
                definition="Seasonality was not assessed: the series is too short.",
                n=int(values.size),
            )
        )
        outcome.warnings.append(
            f"Separating a seasonal pattern needs at least {MIN_PERIODS_FOR_SEASONALITY} periods "
            f"of history; this series has {values.size}. No seasonal conclusion is offered."
        )
        outcome.table(
            pl.DataFrame({"period": np.arange(values.size), "observed": values}), "seasonality_input"
        )
        return outcome

    # Classical decomposition: centre the series, then measure how much of the
    # remaining variation repeats every 12 periods (annual seasonality).
    centred = values - values.mean()
    period = int(params.get("seasonal_period", 12))
    lagged = centred[period:]
    base = centred[:-period]
    if base.size < 3:
        outcome.warnings.append("Insufficient overlap for a seasonal lag comparison.")
        return outcome

    correlation = float(np.corrcoef(base, lagged)[0, 1])
    if math.isnan(correlation):
        correlation = 0.0

    outcome.metrics.append(
        MetricValue(
            name="seasonal_lag_correlation",
            value=correlation,
            definition=(
                f"Correlation between the series and itself {period} periods earlier. "
                "High values indicate a repeating pattern."
            ),
            n=int(base.size),
        )
    )
    outcome.metrics.append(
        MetricValue(
            name="seasonal_amplitude",
            value=float(np.max(np.abs(centred)) - np.min(np.abs(centred))),
            definition="Peak-to-trough spread of the centred series.",
            n=int(values.size),
            unit=params.get("unit"),
        )
    )
    outcome.warnings.append(
        "Seasonality is inferred from a repeating pattern in the data. It does not explain "
        "why the pattern exists."
    )
    return outcome


def _overall_change(first_value: float | None, last_value: float | None) -> float | None:
    """Percentage change between two period totals, or None if undefined."""
    if first_value is None or last_value is None:
        return None
    if abs(first_value) < 1e-12:
        return None
    return (last_value - first_value) / abs(first_value) * 100.0


def _label(period) -> str:
    if period is None:
        return ""
    return str(period)[:10]


def _nan_to_none(value) -> float | None:
    if value is None:
        return None
    as_float = float(value)
    return None if math.isnan(as_float) or math.isinf(as_float) else as_float
