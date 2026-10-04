"""`DatasetProfiler`: the deterministic profile the rest of the system trusts.

No language model is involved. Given the same table, the same profile comes out,
which is what makes "the report matches the data" checkable.
"""

from __future__ import annotations

import numpy as np
import polars as pl
from insightflow_contracts import (
    CategorySummary,
    ColumnProfile,
    DatasetProfile,
    DatasetSnapshot,
    DateSummary,
    InferredType,
    NumericSummary,
    ProfileWarning,
    QualityIssue,
    SemanticRole,
)
from insightflow_ingestion import clean_number_literal

from insightflow_profiling.issues import (
    detect_column_issues,
    detect_table_issues,
)
from insightflow_profiling.semantics import (
    ColumnSemantics,
    detect_pii,
    infer_role,
)
from insightflow_profiling.type_inference import TypeInference, infer_column_type

#: Values this many characters or longer are considered free text, not a label.
MAX_LABEL_LENGTH = 60

_SAMPLE_SIZE = 10


class DatasetProfiler:
    """Profile a normalised string table into a `DatasetProfile`."""

    version = "1"

    def __init__(self, *, sample_size: int = _SAMPLE_SIZE) -> None:
        self.sample_size = sample_size

    def profile(
        self,
        frame: pl.DataFrame,
        snapshot: DatasetSnapshot,
        *,
        original_columns: list[str] | None = None,
    ) -> DatasetProfile:
        
        row_count = frame.height
        columns: list[ColumnProfile] = []
        issues: list[QualityIssue] = []

        original_names = self._original_name_map(frame.columns, original_columns)

        for name in frame.columns:
            series = frame.get_column(name)
            column = self._profile_column(name, series, row_count)
            if name in original_names:
                column.original_name = original_names[name]
            columns.append(column)
            issues.extend(detect_column_issues(column, series))

        issues.extend(detect_table_issues(frame, columns))

        warnings: list[ProfileWarning] = []
        if row_count == 0:
            warnings.append(
                ProfileWarning(code="empty_table", message="The selected table has a header but no data rows.")
            )

        return DatasetProfile(
            dataset_id=snapshot.dataset_id,
            source_file_hash=snapshot.source_file_hash,
            sheet_name=snapshot.sheet_name,
            row_count=row_count,
            column_count=len(columns),
            estimated_memory_bytes=int(frame.estimated_size()),
            columns=columns,
            quality_issues=issues,
            duplicate_row_count=row_count - len(frame.unique()) if row_count else 0,
            time_coverage=self._overall_coverage(columns),
            warnings=warnings,
            profiler_version=self.version,
        )

    # ------------------------------------------------------------- internals
    def _profile_column(self, name: str, series: pl.Series, row_count: int) -> ColumnProfile:
        inference = infer_column_type(series, row_count=row_count)
        non_null = series.drop_nulls()
        unique_count = series.n_unique()

        column = ColumnProfile(
            name=name,
            inferred_type=inference.inferred_type,
            null_count=series.null_count(),
            null_fraction=round(series.null_count() / max(row_count, 1), 4),
            unique_count=unique_count,
            distinct_fraction=round(unique_count / max(row_count, 1), 4),
            sample_values=self._samples(non_null),
            parse_failure_fraction=round(inference.parse_failure_fraction, 4),
            storage_bytes=int(series.estimated_size()),
        )

        semantics = self._semantics(name, series, inference, row_count)
        column.semantic_role = semantics.role
        column.pii_risk = semantics.pii_risk
        column.pii_signals = semantics.pii_signals

        if inference.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}:
            column.numeric = self._numeric_summary(series)
        elif inference.inferred_type in {InferredType.DATE, InferredType.DATETIME}:
            column.temporal = self._date_summary(series, inference.inferred_type)
        if inference.inferred_type in {InferredType.CATEGORY, InferredType.TEXT, InferredType.UNKNOWN}:
            column.category = self._category_summary(series)

        if inference.inferred_type == InferredType.TEXT and non_null.len() and series.dtype is pl.String:
            lengths = non_null.str.len_chars()
            column.avg_length = float(lengths.mean() or 0.0)
            column.max_length = int(lengths.max() or 0)

        return column

    def _semantics(
        self,
        name: str,
        series: pl.Series,
        inference: TypeInference,
        row_count: int,
    ) -> ColumnSemantics:
        pii = detect_pii(name, series)
        role = infer_role(
            name,
            distinct_count=series.n_unique(),
            row_count=row_count,
            inferred_type=inference.inferred_type.value,
        )
        return ColumnSemantics(role=role, pii_risk=pii.pii_risk, pii_signals=pii.pii_signals)

    def _samples(self, non_null: pl.Series) -> list[str]:
        if non_null.len() == 0:
            return []
        # Deterministic sample: the first n distinct values in row order.
        head = non_null.head(min(non_null.len(), self.sample_size * 10))
        return [str(value)[:120] for value in head.unique(maintain_order=True).head(self.sample_size)]

    def _numeric_summary(self, series: pl.Series) -> NumericSummary:
        values = pl.Series(
            series.name,
            [
                clean_number_literal(str(value)) if value is not None else None
                for value in series.drop_nulls().to_list()
            ],
            dtype=pl.String,
        ).cast(pl.Float64, strict=False)
        parsed = values.drop_nans().to_numpy().astype("float64", copy=False)
        count = int(parsed.size)
        if count == 0:
            return NumericSummary(count=0, null_count=int(series.null_count()))

        finite_mask = np.isfinite(parsed)
        array = parsed[finite_mask]
        non_finite_count = count - int(array.size)
        if array.size == 0:
            return NumericSummary(
                count=0, null_count=int(series.null_count()), non_finite_count=non_finite_count
            )

        return NumericSummary(
            count=int(array.size),
            null_count=int(series.null_count()),
            mean=float(np.mean(array)),
            std=float(np.std(array, ddof=1)) if array.size > 1 else 0.0,
            min=float(np.min(array)),
            p05=float(np.percentile(array, 5)),
            p25=float(np.percentile(array, 25)),
            median=float(np.median(array)),
            p75=float(np.percentile(array, 75)),
            p95=float(np.percentile(array, 95)),
            max=float(np.max(array)),
            sum=float(np.sum(array)),
            skewness=float(_skewness(array)),
            kurtosis=float(_kurtosis(array)),
            zero_count=int(np.sum(array == 0)),
            negative_count=int(np.sum(array < 0)),
            non_finite_count=non_finite_count,
        )

    def _date_summary(self, series: pl.Series, kind: InferredType) -> DateSummary:
        cleaned = series.drop_nulls().str.strip_chars()
        parsed: pl.Series | None = None
        for fmt in _DATE_FORMATS_FOR_KIND[kind]:
            try:
                candidate = cleaned.str.strptime(pl.Date, fmt, strict=False)
            except Exception:
                continue
            if candidate.is_not_null().sum() > 0:
                parsed = candidate
                break
        if parsed is None:
            return DateSummary(count=0, null_count=int(series.null_count()))

        valid = parsed.drop_nulls()
        if valid.len() == 0:
            return DateSummary(count=0, null_count=int(series.null_count()))

        minimum = valid.min()
        maximum = valid.max()
        span = (maximum - minimum).days if minimum is not None and maximum is not None else None
        return DateSummary(
            count=int(valid.len()),
            null_count=int(series.null_count()),
            min=str(minimum),
            max=str(maximum),
            distinct_days=int(valid.n_unique()),
            distinct_months=int(valid.dt.month().cast(pl.Utf8).str.slice(0, 7).n_unique()),
            span_days=span,
            has_time_component=kind is InferredType.DATETIME,
            granularity_guess=_granularity(span),
        )

    def _category_summary(self, series: pl.Series) -> CategorySummary:
        if series.dtype != pl.String:
            # Already-typed columns cannot have casing or whitespace variants.
            counts = series.drop_nulls().value_counts(sort=True)
            top = [(str(value), int(count)) for value, count in counts.head(_SAMPLE_SIZE).iter_rows()]
            return CategorySummary(
                top_values=top,
                distinct_count=int(series.n_unique()),
                normalised_forms=int(series.n_unique()),
            )

        non_null = series.drop_nulls()
        if non_null.len() == 0:
            return CategorySummary(distinct_count=0)

        counts = non_null.value_counts(sort=True)
        top = [(str(value), int(count)) for value, count in counts.head(_SAMPLE_SIZE).iter_rows()]

        stripped = non_null.str.strip_chars()
        normalised = stripped.str.to_lowercase()
        distinct_raw = int(non_null.n_unique())
        distinct_stripped = int(stripped.n_unique())
        distinct_normalised = int(normalised.n_unique())

        return CategorySummary(
            top_values=top,
            distinct_count=distinct_raw,
            normalised_forms=distinct_normalised,
            case_variants=distinct_raw - distinct_stripped,
            whitespace_variants=distinct_stripped - distinct_normalised,
        )

    def _overall_coverage(self, columns: list[ColumnProfile]) -> DateSummary | None:
        temporal = [c.temporal for c in columns if c.temporal is not None and c.temporal.count]
        if not temporal:
            return None
        spans = [summary.span_days for summary in temporal if summary.span_days is not None]
        earliest = min((s.min for s in temporal if s.min), default=None)
        latest = max((s.max for s in temporal if s.max), default=None)
        widest = max(spans) if spans else None
        return DateSummary(
            count=sum(s.count for s in temporal),
            null_count=sum(s.null_count for s in temporal),
            min=earliest,
            max=latest,
            span_days=widest,
            granularity_guess=_granularity(widest),
        )

    def _original_name_map(
        self,
        columns: list[str],
        original: list[str] | None,
    ) -> dict[str, str]:
        """Map normalised column names back to the headers the user actually sent."""
        if not original or len(original) != len(columns):
            return {}
        return {
            column: original[index]
            for index, column in enumerate(columns)
            if original[index] != column
        }


_DATE_FORMATS_FOR_KIND: dict[InferredType, tuple[str, ...]] = {
    InferredType.DATE: ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y", "%Y%m%d"),
    InferredType.DATETIME: (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M",
    ),
}


def _skewness(array: np.ndarray) -> float:
    mean = float(np.mean(array))
    std = float(np.std(array))
    if std == 0:
        return 0.0
    return float(np.mean(((array - mean) / std) ** 3))


def _kurtosis(array: np.ndarray) -> float:
    mean = float(np.mean(array))
    std = float(np.std(array))
    if std == 0:
        return 0.0
    return float(np.mean(((array - mean) / std) ** 4) - 3.0)


def _granularity(span_days: int | None) -> str | None:
    if span_days is None:
        return None
    if span_days <= 14:
        return "daily"
    if span_days <= 120:
        return "weekly"
    if span_days <= 1200:
        return "monthly"
    return "quarterly_or_coarser"


def profile_frame(
    frame: pl.DataFrame,
    snapshot: DatasetSnapshot,
    *,
    original_columns: list[str] | None = None,
) -> DatasetProfile:
    """Convenience wrapper around `DatasetProfiler` for one-off use."""
    return DatasetProfiler().profile(frame, snapshot, original_columns=original_columns)


def role_summary(columns: list[ColumnProfile]) -> dict[SemanticRole, list[str]]:
    summary: dict[SemanticRole, list[str]] = {}
    for column in columns:
        summary.setdefault(column.semantic_role, []).append(column.name)
    return summary
