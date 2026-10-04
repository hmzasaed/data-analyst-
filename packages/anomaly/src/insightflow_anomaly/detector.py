"""`AnomalyDetector`: pick a method the data can support, then explain every flag.

Method selection is a function of data type, group sizes and sample size - never a
guess. Each detector returns `AnomalyFinding` records naming the method, the
baseline, the threshold and the limitation, because "this looks odd" is not a
useful thing to hand a business user.

Nothing here labels a record as an error, fraud or misconduct. A flagged record is
a record worth a human look.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import polars as pl
from insightflow_contracts import (
    METHOD_CAVEATS,
    AnomalyFinding,
    AnomalyMethod,
    AnomalyResult,
    BaselineSpec,
    DatasetSnapshot,
    Severity,
    ToolExecutionError,
    content_digest,
)

#: Minimum observations before a distribution-based method is trusted.
MIN_UNIVARIATE_SAMPLE = 20
#: Minimum rows in a group before group-relative scoring is attempted.
MIN_GROUP_SIZE = 8
#: Minimum history length for a rolling baseline.
MIN_ROLLING_WINDOW = 4
#: Isolation forest needs a reasonably wide sample to mean anything.
MIN_MULTIVARIATE_SAMPLE = 50
MIN_MULTIVARIATE_FEATURES = 2

#: Score assigned when the trailing window had no variation at all. Large and finite
#: so severity stays high and the value still serialises as JSON.
ZERO_SPREAD_SCORE = 1000.0

#: Column names the assembler treats specially.
VALUE_COLUMN = "_value"
SCORE_COLUMN = "_score"
ROW_COLUMN = "_row"
FLAG_COLUMN = "_flag"


@dataclass
class DetectionRequest:
    """What to scan. `method=None` means "choose based on the data"."""

    column: str
    method: AnomalyMethod | None = None
    group_columns: tuple[str, ...] = ()
    features: tuple[str, ...] = ()
    threshold: float | None = None
    max_anomalies: int = 100
    label_columns: tuple[str, ...] = ()
    analysis_step_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class AnomalyDetector:
    version = "1"

    def detect(
        self, frame: pl.DataFrame, snapshot: DatasetSnapshot, request: DetectionRequest
    ) -> AnomalyResult:
        self._check_columns(frame, request)
        method = request.method or self.select_method(frame, request)

        if method is AnomalyMethod.ISOLATION_FOREST:
            return self._isolation_forest(frame, snapshot, request)
        if method is AnomalyMethod.ROLLING_MAD:
            return self._rolling_mad(frame, snapshot, request)
        if method is AnomalyMethod.GROUP_PERCENTILE:
            return self._group_relative(frame, snapshot, request)
        if method in {AnomalyMethod.IQR_FENCE, AnomalyMethod.ROBUST_ZSCORE}:
            return self._univariate(frame, snapshot, request, method)
        raise ToolExecutionError(
            "unsupported anomaly method", method=method.value, retryable=False
        )

    # ------------------------------------------------------------- preflight
    def _check_columns(self, frame: pl.DataFrame, request: DetectionRequest) -> None:
        missing = [
            name
            for name in (request.column, *request.features, *request.group_columns)
            if name and name not in frame.columns
        ]
        if missing:
            raise ToolExecutionError(
                "column not present in this dataset version",
                columns=missing,
                available=list(frame.columns)[:50],
                retryable=False,
            )

    # -------------------------------------------------------- method choice
    def select_method(self, frame: pl.DataFrame, request: DetectionRequest) -> AnomalyMethod:
        """Choose the method the data can actually support."""
        if request.features:
            return self._select_multivariate(frame, request)

        values = frame.get_column(request.column).cast(pl.Float64, strict=False).drop_nulls()
        if values.len() < MIN_UNIVARIATE_SAMPLE:
            raise ToolExecutionError(
                "not enough non-null values for statistical anomaly detection",
                column=request.column,
                available=int(values.len()),
                required=MIN_UNIVARIATE_SAMPLE,
                retryable=False,
            )

        if request.group_columns:
            sizes = frame.group_by(list(request.group_columns)).len()
            usable = int((sizes["len"] >= MIN_GROUP_SIZE).sum())
            if usable >= 2:
                return AnomalyMethod.GROUP_PERCENTILE
            return AnomalyMethod.IQR_FENCE

        if _time_column(frame, request) is not None:
            return AnomalyMethod.ROLLING_MAD

        # A symmetric z-score is unreliable when the middle half of the data is
        # squeezed into a small part of the range, which is common for money.
        array = values.to_numpy()
        ratio = _dispersion_ratio(array)
        return AnomalyMethod.IQR_FENCE if (ratio is not None and ratio < 0.05) else AnomalyMethod.ROBUST_ZSCORE

    def _select_multivariate(self, frame: pl.DataFrame, request: DetectionRequest) -> AnomalyMethod:
        if frame.height < MIN_MULTIVARIATE_SAMPLE:
            raise ToolExecutionError(
                "multivariate anomaly detection needs more rows",
                rows=int(frame.height),
                required=MIN_MULTIVARIATE_SAMPLE,
                retryable=False,
            )
        if len(set(request.features)) < MIN_MULTIVARIATE_FEATURES:
            raise ToolExecutionError(
                "isolation forest needs at least two distinct features",
                features=list(request.features),
                retryable=False,
            )
        return AnomalyMethod.ISOLATION_FOREST

    # ------------------------------------------------------------- detectors
    def _univariate(
        self,
        frame: pl.DataFrame,
        snapshot: DatasetSnapshot,
        request: DetectionRequest,
        method: AnomalyMethod,
    ) -> AnomalyResult:
        """Whole-column detection using IQR fences or a robust z-score."""
        column = request.column
        values = frame.get_column(column).cast(pl.Float64, strict=False)
        clean = values.drop_nulls().to_numpy().astype("float64", copy=False)
        if clean.size < MIN_UNIVARIATE_SAMPLE:
            raise ToolExecutionError(
                "not enough non-null values",
                column=column,
                available=int(clean.size),
                required=MIN_UNIVARIATE_SAMPLE,
                retryable=False,
            )

        if method is AnomalyMethod.ROBUST_ZSCORE:
            multiplier = float(request.threshold or 3.5)
            centre = float(np.median(clean))
            scale = float(np.median(np.abs(clean - centre))) * 1.4826
            if scale == 0:
                raise ToolExecutionError(
                    "more than half the values are identical, so no spread can be estimated",
                    column=column,
                    retryable=False,
                )
            lower, upper = centre - multiplier * scale, centre + multiplier * scale
            baseline = BaselineSpec(
                kind="median_mad",
                description=(
                    f"Median of '{column}' plus or minus {multiplier} scaled median absolute "
                    "deviations (MAD x 1.4826)."
                ),
                centre=centre,
                lower_bound=lower,
                upper_bound=upper,
                scale=scale,
                sample_size=int(clean.size),
            )
        else:
            multiplier = float(request.threshold or 1.5)
            q1, q3 = (float(v) for v in np.percentile(clean, [25, 75]))
            spread = q3 - q1
            lower, upper = q1 - multiplier * spread, q3 + multiplier * spread
            baseline = BaselineSpec(
                kind="interquartile_fence",
                description=(
                    f"First and third quartiles of '{column}', extended by {multiplier} times the "
                    "interquartile range on each side."
                ),
                centre=(q1 + q3) / 2,
                lower_bound=lower,
                upper_bound=upper,
                scale=spread,
                sample_size=int(clean.size),
            )

        mask = ((values < lower) | (values > upper)).fill_null(False)
        working = frame.with_row_index(ROW_COLUMN).with_columns(
            pl.Series(VALUE_COLUMN, np.asarray(values.to_list(), dtype="float64")),
            pl.Series(FLAG_COLUMN, mask.fill_null(False)),
        )
        return self._assemble(
            working, snapshot, request, method, column, baseline, multiplier, extra_columns=()
        )

    def _rolling_mad(
        self, frame: pl.DataFrame, snapshot: DatasetSnapshot, request: DetectionRequest
    ) -> AnomalyResult:
        """Compare each point against the trailing window's median and MAD."""
        column = request.column
        window = max(int(request.extra.get("window", 7)), MIN_ROLLING_WINDOW)
        threshold = float(request.threshold or request.extra.get("z_threshold", 3.5))

        time_column = _time_column(frame, request)
        working = frame.with_row_index(ROW_COLUMN)
        if time_column is not None:
            working = working.sort(time_column)

        array = working.get_column(column).cast(pl.Float64, strict=False).to_numpy().astype(
            "float64", copy=False
        )

        scores = np.full(array.shape, np.nan)
        centres = np.full(array.shape, np.nan)
        scales = np.full(array.shape, np.nan)
        zero_spread_windows = 0
        for index in range(window, array.size):
            history = array[index - window : index]
            history = history[~np.isnan(history)]
            if history.size < MIN_ROLLING_WINDOW:
                continue
            centre = float(np.median(history))
            mad = float(np.median(np.abs(history - centre))) * 1.4826
            centres[index] = centre
            current = array[index]
            if mad == 0:
                # A perfectly flat history has no scale to compare against. Any
                # departure from it is the entire signal, so flag it rather than
                # silently skipping the point; a flat point stays unflagged.
                zero_spread_windows += 1
                scales[index] = 0.0
                if not np.isnan(current) and float(current) != centre:
                    scores[index] = ZERO_SPREAD_SCORE
                continue
            scales[index] = mad
            if not np.isnan(current):
                scores[index] = abs(current - centre) / mad

        baseline = BaselineSpec(
            kind="trailing_window",
            description=(
                f"Median and scaled MAD of the {window} observations immediately preceding each "
                f"point of '{column}', ordered by {time_column or 'row order'}."
            ),
            centre=_safe_nan_median(centres),
            scale=_safe_nan_median(scales),
            sample_size=int(np.isfinite(scores).sum()),
        )
        usable = working.head(window)
        scored = working.slice(window)
        working = pl.concat(
            [
                usable.with_columns(
                    pl.Series(VALUE_COLUMN, np.asarray(array[:window], dtype="float64")),
                    pl.lit(False).alias(FLAG_COLUMN),
                    pl.Series(SCORE_COLUMN, scores[:window]),
                ),
                scored.with_columns(
                    pl.Series(VALUE_COLUMN, np.asarray(array[window:], dtype="float64")),
                    pl.Series(FLAG_COLUMN, np.nan_to_num(scores[window:], nan=0.0) > threshold),
                    pl.Series(SCORE_COLUMN, scores[window:]),
                ),
            ],
            how="vertical",
        )
        result = self._assemble(
            working, snapshot, request, AnomalyMethod.ROLLING_MAD, column, baseline, threshold,
            extra_columns=((time_column,) if time_column else ()),
        )
        result.skipped_count = min(window, int(working.height))
        result.limitations.append(
            f"The first {window} point(s) have no trailing history and cannot be scored."
        )
        if zero_spread_windows:
            result.limitations.append(
                f"{zero_spread_windows} point(s) followed a perfectly flat window. With no "
                "variation to compare against, any departure from that level is flagged, so "
                "expect these flags to be easy to explain and easy to dismiss."
            )
        return result

    def _group_relative(
        self, frame: pl.DataFrame, snapshot: DatasetSnapshot, request: DetectionRequest
    ) -> AnomalyResult:
        """Score each record against its own group's distribution."""
        column = request.column
        groups = list(request.group_columns)
        multiplier = float(request.threshold or 1.5)

        sizes = frame.group_by(groups).len().rename({"len": "_group_size"})
        usable_sizes = sizes.filter(pl.col("_group_size") >= MIN_GROUP_SIZE)
        if usable_sizes.height < 2:
            raise ToolExecutionError(
                "too few groups are large enough for a relative comparison",
                min_group_size=MIN_GROUP_SIZE,
                usable_groups=int(usable_sizes.height),
                retryable=False,
            )

        stats = (
            frame.drop_nulls(subset=[column])
            .group_by(groups)
            .agg(
                (pl.col(column).quantile(0.25)).alias("_q1"),
                (pl.col(column).quantile(0.75)).alias("_q3"),
            )
            .with_columns(
                # The multiplier is expressed in IQR units so the group fence means
                # the same thing as the univariate one.
                (pl.col("_q3") - pl.col("_q1")).alias("_iqr")
            )
        )

        working = (
            frame.with_row_index(ROW_COLUMN)
            .join(stats, on=groups, how="left")
            .join(usable_sizes.select(groups), on=groups, how="semi")
            .with_columns(pl.col(column).cast(pl.Float64).alias(VALUE_COLUMN))
            .with_columns(
                (
                    (pl.col(VALUE_COLUMN) < pl.col("_q1") - multiplier * pl.col("_iqr"))
                    | (pl.col(VALUE_COLUMN) > pl.col("_q3") + multiplier * pl.col("_iqr"))
                )
                .fill_null(False)
                .alias(FLAG_COLUMN),
            )
        )

        baseline = BaselineSpec(
            kind="group_interquartile_fence",
            description=(
                f"Within-group quartiles of '{column}', extended by {multiplier} times the "
                f"within-group interquartile range. Groups with fewer than {MIN_GROUP_SIZE} rows, "
                "or with no variation, are skipped."
            ),
            sample_size=int(working.height),
        )
        result = self._assemble(
            working, snapshot, request, AnomalyMethod.GROUP_PERCENTILE, column, baseline,
            multiplier, extra_columns=(*groups, "_q1", "_q3"),
        )
        skipped = max(int(frame.height) - int(working.height), 0)
        result.skipped_count = skipped
        if skipped:
            result.limitations.append(
                f"{skipped:,} row(s) were not scored because their group was too small or had "
                "no variation."
            )
        return result

    def _isolation_forest(
        self, frame: pl.DataFrame, snapshot: DatasetSnapshot, request: DetectionRequest
    ) -> AnomalyResult:
        """Multivariate outliers: 'unlike the rest of the rows', nothing more."""
        try:
            from sklearn.ensemble import IsolationForest
        except ImportError as exc:
            # Some deployments block native extensions. Degrade with a clear
            # message instead of taking the whole job down.
            raise ToolExecutionError(
                "multivariate anomaly detection is unavailable in this deployment",
                method=AnomalyMethod.ISOLATION_FOREST.value,
                cause=type(exc).__name__,
                suggestion=(
                    "Use a univariate method (iqr_fence, robust_zscore or group_percentile) "
                    "for one column at a time."
                ),
                retryable=False,
            ) from exc

        features = [name for name in request.features if name in frame.columns]
        subset = frame.drop_nulls(subset=features)
        if subset.height < MIN_MULTIVARIATE_SAMPLE:
            raise ToolExecutionError(
                "not enough complete rows for multivariate detection",
                rows=int(subset.height),
                required=MIN_MULTIVARIATE_SAMPLE,
                retryable=False,
            )

        matrix = subset.select([pl.col(f).cast(pl.Float64) for f in features]).to_numpy()
        if not np.isfinite(matrix).all():  # pragma: no cover - drop_nulls handles this
            raise ToolExecutionError(
                "feature matrix contains non-finite values", features=features, retryable=False
            )
        if np.allclose(matrix.std(axis=0), 0):
            raise ToolExecutionError(
                "at least one feature has no variation", features=features, retryable=False
            )

        contamination = min(max(float(request.extra.get("contamination", 0.02)), 0.001), 0.2)
        model = IsolationForest(
            n_estimators=int(request.extra.get("n_estimators", 200)),
            contamination=contamination,
            random_state=int(request.extra.get("seed", 0)),
            n_jobs=1,
        )
        model.fit(matrix)
        scores = model.decision_function(matrix)
        predictions = model.predict(matrix)

        working = frame.with_row_index(ROW_COLUMN).join(
            subset.select(features).with_columns(
                pl.Series(SCORE_COLUMN, scores), pl.Series(FLAG_COLUMN, predictions == -1)
            ),
            on=features,
            how="left",
        )

        baseline = BaselineSpec(
            kind="isolation_forest",
            description=(
                f"Isolation forest over {len(features)} features, fitted on {subset.height:,} "
                f"complete rows with a {contamination:.1%} assumed contamination rate."
            ),
            centre=float(np.mean(scores)),
            lower_bound=float(np.percentile(scores, 100 * contamination)),
            scale=float(np.std(scores)),
            sample_size=int(subset.height),
        )
        return self._assemble(
            working, snapshot, request, AnomalyMethod.ISOLATION_FOREST, features[0],
            baseline, contamination, extra_columns=tuple(features),
        )

    # -------------------------------------------------------------- assembly
    def _assemble(
        self,
        frame: pl.DataFrame,
        snapshot: DatasetSnapshot,
        request: DetectionRequest,
        method: AnomalyMethod,
        column: str,
        baseline: BaselineSpec,
        threshold: float | None,
        *,
        extra_columns: tuple[str, ...],
    ) -> AnomalyResult:
        """Turn a flag column into explained, capped anomaly records."""
        caveat = METHOD_CAVEATS[method]
        limit = max(int(request.max_anomalies), 1)
        flagged = frame.filter(pl.col(FLAG_COLUMN).fill_null(False))

        anomalies: list[AnomalyFinding] = []
        for row in flagged.head(limit).iter_rows(named=True):
            observed = _as_float(row.get(VALUE_COLUMN))
            score = _as_float(row.get(SCORE_COLUMN))
            group_label, group_columns = _group_label(row, request.group_columns)
            record_values = {
                name: str(row[name])[:80]
                for name in (*request.label_columns, *extra_columns)
                if name in row and row[name] is not None and name != SCORE_COLUMN
            }
            anomalies.append(
                AnomalyFinding(
                    anomaly_id=content_digest(
                        {
                            "method": method.value,
                            "column": column,
                            "groups": group_columns,
                            "value": observed,
                            "row": row.get(ROW_COLUMN),
                        },
                        length=12,
                    ),
                    method=method,
                    entity_type="group" if request.group_columns else "row",
                    entity_label=group_label,
                    entity_columns=group_columns,
                    observed_value=observed,
                    baseline=baseline,
                    severity=_severity(score, threshold),
                    score=score,
                    threshold=threshold,
                    reason=_reason_for(method, column, observed, baseline, score, threshold, row),
                    limitation=caveat,
                    row_reference=f"row:{row.get(ROW_COLUMN)}",
                    record_values=record_values,
                )
            )

        return AnomalyResult(
            analysis_step_id=request.analysis_step_id,
            method=method,
            dataset_version=snapshot.version,
            target_column=column,
            group_columns=list(request.group_columns),
            anomalies=anomalies,
            evaluated_count=int(frame.height),
            flagged_count=len(anomalies),
            skipped_count=0,
            parameter_digest=content_digest(
                {
                    "method": method.value,
                    "column": column,
                    "groups": list(request.group_columns),
                    "features": list(request.features),
                    "threshold": threshold,
                    "window": request.extra.get("window"),
                    "contamination": request.extra.get("contamination"),
                }
            ),
            limitations=[caveat],
            method_version=self.version,
        )


# --------------------------------------------------------------------- helpers
def _reason_for(
    method: AnomalyMethod,
    column: str,
    observed: float | None,
    baseline: BaselineSpec,
    score: float | None,
    threshold: float | None,
    row: dict,
) -> str:
    """A sentence naming the method, the value, and the comparison it failed."""
    if method is AnomalyMethod.ROLLING_MAD:
        multiple = f" ({score:.1f}x the threshold)" if score and threshold else ""
        if baseline.scale is not None and baseline.scale == 0:
            return (
                f"'{column}' departs from a trailing window that had no variation at all "
                f"(level {_fmt(baseline.centre)}), which is unusual for that window."
            )
        return (
            f"'{column}' deviates{multiple} from the median of the preceding window "
            f"(trailing centre {_fmt(baseline.centre)}, scaled MAD {_fmt(baseline.scale)})."
        )
    if method is AnomalyMethod.ISOLATION_FOREST:
        return (
            "This record is further from the pattern formed by the other rows than the "
            f"{float(threshold or 0.02):.1%} of rows the model was configured to expect."
        )
    if method is AnomalyMethod.GROUP_PERCENTILE:
        # Bounds are per group, so the concrete comparison is read off this row.
        q1, q3 = _as_float(row.get("_q1")), _as_float(row.get("_q3"))
        multiplier = float(threshold or 1.5)
        spread = (q3 - q1) if (q1 is not None and q3 is not None) else None
        lower = q1 - multiplier * spread if (q1 is not None and spread is not None) else None
        upper = q3 + multiplier * spread if (q3 is not None and spread is not None) else None
        direction = "below" if _is_below(observed, baseline) else "above"
        return (
            f"'{column}' is {direction} the range typical for this group: {_fmt(observed)} "
            f"against {_fmt(lower)} to {_fmt(upper)} (group quartiles "
            f"{_fmt(q1)} to {_fmt(q3)})."
        )
    direction = "below" if _is_below(observed, baseline) else "above"
    return (
        f"'{column}' is {direction} the expected range for {baseline.kind.replace('_', ' ')}: "
        f"{_fmt(observed)} against {_fmt(baseline.lower_bound)} to {_fmt(baseline.upper_bound)}, "
        f"derived from {baseline.sample_size or 0:,} value(s)."
    )


def _severity(score: float | None, threshold: float | None) -> Severity:
    """How far past the threshold a point sits, expressed as severity."""
    if score is None or threshold is None or threshold <= 0:
        return Severity.MEDIUM
    ratio = abs(score) / threshold
    if ratio >= 2.0:
        return Severity.HIGH
    if ratio >= 1.5:
        return Severity.MEDIUM
    return Severity.LOW


def _is_below(observed: float | None, baseline: BaselineSpec) -> bool:
    if observed is None:
        return False
    return baseline.lower_bound is not None and observed < baseline.lower_bound


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(as_float) or math.isinf(as_float) else as_float


def _safe_nan_median(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return float(np.median(finite)) if finite.size else None


def _fmt(value: float | None) -> str:
    if value is None:
        return "undefined"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.4g}"


def _group_label(row: dict, groups: tuple[str, ...]) -> tuple[str, dict[str, str]]:
    columns = {name: str(row[name]) for name in groups if row.get(name) is not None}
    if not columns:
        return f"row {row.get(ROW_COLUMN, '?')}", {}
    return " / ".join(f"{name}={value}" for name, value in columns.items()), columns


def _dispersion_ratio(array: np.ndarray) -> float | None:
    spread = float(np.max(array) - np.min(array))
    if spread == 0:
        return None
    q1, q3 = np.percentile(array, [25, 75])
    return float(q3 - q1) / spread


def _time_column(frame: pl.DataFrame, request: DetectionRequest) -> str | None:
    """A temporal column to order by, preferring the target column itself."""
    temporal = [
        name
        for name, dtype in frame.schema.items()
        if _is_temporal_dtype(dtype) and name != request.column
    ]
    if _is_temporal_dtype(frame.schema.get(request.column)):
        return request.column
    return temporal[0] if temporal else None


def _is_temporal_dtype(dtype: object) -> bool:
    checker = getattr(dtype, "is_temporal", None)
    return bool(checker()) if callable(checker) else False
