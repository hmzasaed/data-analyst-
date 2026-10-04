"""Anomaly tools exposed through the analysis registry.

These wrappers exist so anomaly detection goes through the same validation,
execution and audit path as every other analysis. Each one returns an evidence
table of flagged records plus metrics describing how many were evaluated, so the
report can state the flag rate rather than just a list of names.
"""

from __future__ import annotations

import polars as pl
from insightflow_anomaly import AnomalyDetector, DetectionRequest
from insightflow_contracts import (
    AnomalyMethod,
    ChartKind,
    ChartRequest,
    ChartSeries,
    MetricValue,
    derive_id,
)

from insightflow_analytics.context import AnalysisContext, ToolOutcome

TOOL_VERSION = "1"

#: Cap on how many flagged records are written to an evidence table, so one noisy
#: column cannot produce a multi-megabyte artifact.
MAX_EVIDENCE_ROWS = 500


def detect_outliers(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Flag values in one measure that are unusual for the dataset as a whole."""
    ctx.guard_row_count()
    column = params["column"]
    ctx.numeric_column(column)
    extra = dict(params.get("method_options") or {})
    if "contamination" in params:
        extra.setdefault("contamination", params["contamination"])

    request = DetectionRequest(
        column=column,
        method=_method(params.get("method")),
        threshold=params.get("threshold"),
        max_anomalies=int(params.get("max_anomalies", MAX_EVIDENCE_ROWS)),
        label_columns=tuple(params.get("label_columns") or ()),
        analysis_step_id=ctx.step_id,
        extra=extra,
    )
    result = AnomalyDetector().detect(ctx.frame, ctx.snapshot, request)

    outcome = ToolOutcome()
    frame = _evidence_frame(result.anomalies)
    outcome.table(frame, f"anomalies_{column}")
    outcome.metrics.extend(_metrics(result, column))
    outcome.warnings.extend(result.limitations)
    outcome.notes["anomaly_method"] = result.method.value
    outcome.notes["anomaly_result_id"] = f"anom_{result.parameter_digest}"
    _attach_chart(outcome, frame, column, request)
    return outcome


def detect_group_outliers(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Flag values that are unusual *for their own group*, not for the dataset."""
    ctx.guard_row_count()
    column = params["column"]
    groups = list(params["group_by"])
    ctx.numeric_column(column)
    for group in groups:
        ctx.category_column(group)

    request = DetectionRequest(
        column=column,
        method=AnomalyMethod.GROUP_PERCENTILE,
        group_columns=tuple(groups),
        threshold=params.get("threshold"),
        max_anomalies=int(params.get("max_anomalies", MAX_EVIDENCE_ROWS)),
        label_columns=tuple(params.get("label_columns") or ()),
        analysis_step_id=ctx.step_id,
        extra=dict(params.get("method_options") or {}),
    )
    result = AnomalyDetector().detect(ctx.frame, ctx.snapshot, request)

    outcome = ToolOutcome()
    frame = _evidence_frame(result.anomalies)
    outcome.table(frame, f"anomalies_{column}_by_{'_'.join(groups)}")
    outcome.metrics.extend(_metrics(result, column))
    outcome.warnings.extend(result.limitations)
    outcome.notes["anomaly_method"] = result.method.value
    outcome.notes["group_by"] = groups
    _attach_chart(outcome, frame, column, request)
    return outcome


def detect_multivariate_outliers(ctx: AnalysisContext, params: dict) -> ToolOutcome:
    """Flag records that differ from the overall pattern across several measures."""
    ctx.guard_row_count()
    features = list(params["features"])
    for feature in features:
        ctx.numeric_column(feature)
    extra = dict(params.get("method_options") or {})
    if "contamination" in params:
        extra.setdefault("contamination", params["contamination"])

    request = DetectionRequest(
        column=features[0],
        method=AnomalyMethod.ISOLATION_FOREST,
        features=tuple(features),
        max_anomalies=int(params.get("max_anomalies", MAX_EVIDENCE_ROWS)),
        label_columns=tuple(params.get("label_columns") or ()),
        analysis_step_id=ctx.step_id,
        extra=extra,
    )
    result = AnomalyDetector().detect(ctx.frame, ctx.snapshot, request)

    outcome = ToolOutcome()
    frame = _evidence_frame(result.anomalies)
    outcome.table(frame, "anomalies_multivariate")
    outcome.metrics.extend(_metrics(result, features[0]))
    outcome.warnings.extend(result.limitations)
    outcome.notes["anomaly_method"] = result.method.value
    outcome.notes["features"] = features
    return outcome


# --------------------------------------------------------------------- helpers
def _method(name: str | None) -> AnomalyMethod | None:
    """Map the tool's `auto` sentinel onto "let the detector choose"."""
    if not name or name == "auto":
        return None
    return AnomalyMethod(name)


def _evidence_frame(anomalies: list) -> pl.DataFrame:
    """One row per flagged record, with the baseline it was compared against."""
    if not anomalies:
        return pl.DataFrame(
            {
                "anomaly_id": pl.Series([], dtype=pl.String),
                "group": pl.Series([], dtype=pl.String),
                "observed_value": pl.Series([], dtype=pl.Float64),
                "method": pl.Series([], dtype=pl.String),
                "reason": pl.Series([], dtype=pl.String),
                "severity": pl.Series([], dtype=pl.String),
                "baseline_description": pl.Series([], dtype=pl.String),
                "limitation": pl.Series([], dtype=pl.String),
            }
        )
    return pl.DataFrame(
        [
            {
                "anomaly_id": item.anomaly_id,
                "group": item.entity_label,
                "observed_value": item.observed_value,
                "method": item.method.value,
                "severity": item.severity.value,
                "score": item.score,
                "reason": item.reason,
                "baseline_description": item.baseline.description,
                "limitation": item.limitation,
            }
            for item in anomalies
        ]
    )


def _metrics(result, column: str):
    """Summary metrics for an anomaly scan, in the shared `MetricValue` shape."""
    return [
        MetricValue(
            name=f"{column}__anomalies_flagged",
            value=result.flagged_count,
            definition=(
                f"Records flagged as unusual by {result.method.value}, compared against the "
                "baseline stated in each record's explanation."
            ),
            n=result.evaluated_count,
            unit="records",
        ),
        MetricValue(
            name=f"{column}__anomaly_rate_pct",
            value=(result.flagged_count / result.evaluated_count * 100.0)
            if result.evaluated_count
            else None,
            definition=f"Share of evaluated rows flagged by {result.method.value} in '{column}'.",
            n=result.evaluated_count,
            unit="%",
        ),
        MetricValue(
            name=f"{column}__anomaly_method",
            value=result.method.value,
            definition=(
                "Detection method used. The method determines what the result does and does "
                "not mean."
            ),
            n=result.evaluated_count,
        ),
        MetricValue(
            name=f"{column}__anomalies_evaluated",
            value=result.evaluated_count,
            definition="Number of rows that were actually scored.",
            n=result.evaluated_count,
            unit="records",
        ),
    ]


def _attach_chart(
    outcome: ToolOutcome, frame: pl.DataFrame, column: str, request: DetectionRequest
) -> None:
    """A bar chart of the most extreme flagged values, clearly labelled as such."""
    if frame.height == 0:
        return
    top = frame.head(15)
    outcome.charts.append(
        ChartRequest(
            chart_id=derive_id(
                "chart",
                {"tool": "anomaly", "column": column, "method": (request.method or AnomalyMethod.IQR_FENCE).value},
            ),
            kind=ChartKind.HORIZONTAL_BAR,
            title=f"Values flagged by {(request.method or AnomalyMethod.IQR_FENCE).value}",
            series=[
                ChartSeries(
                    name="observed value",
                    x=[str(v) for v in top["group"].to_list()],
                    y=[float(v) for v in top["observed_value"].to_list()],
                )
            ],
            x_label="record or group",
            y_label=column,
            caption=(
                f"{frame.height:,} flagged record(s) from {column}. Shown: {len(top)}. "
                "Detected by "
                f"{frame['method'][0] if frame.height else 'anomaly detection'}."
            ),
            caveat=(
                "A flagged record is unusual relative to the stated baseline. It is not "
                "evidence of an error, and it may be entirely legitimate."
            ),
        )
    )