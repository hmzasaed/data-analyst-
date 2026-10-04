"""Optional preprocessing of the data every reported figure is computed from.

Every operation here changes numbers in the report, so each one is opt-in, logged
with the exact count of values or rows it touched, and limited to columns where
it is meaningful: identifiers, time axes, free text and personal data are left
alone, because imputing or capping them would invent facts.
"""

from __future__ import annotations

import polars as pl
from insightflow_contracts import (
    ColumnProfile,
    DatasetProfile,
    PreprocessingOptions,
    PreprocessingStep,
    SemanticRole,
)

#: Roles that make a column a candidate for categorical imputation.
_CATEGORICAL_ROLES = {
    SemanticRole.DIMENSION,
    SemanticRole.CATEGORY,
    SemanticRole.GEOGRAPHY,
    SemanticRole.FLAG,
}
_EXCLUDED_ROLES = {SemanticRole.IDENTIFIER, SemanticRole.TIME, SemanticRole.FREE_TEXT}
CONSTANT_FILL = "Unknown"


def _profiled(profile: DatasetProfile | None) -> dict[str, ColumnProfile]:
    return {column.name: column for column in (profile.columns if profile else [])}


def _is_personal(column: ColumnProfile | None) -> bool:
    return column is not None and column.pii_risk.value in {"medium", "high"}


def analysis_measures(frame: pl.DataFrame, profile: DatasetProfile | None) -> list[str]:
    """Numeric columns that measure something (not ids, years or personal data)."""
    columns = _profiled(profile)
    measures = []
    for name, dtype in frame.schema.items():
        if not dtype.is_numeric():
            continue
        column = columns.get(name)
        if column is not None and (column.semantic_role in _EXCLUDED_ROLES or _is_personal(column)):
            continue
        measures.append(name)
    return measures


def analysis_categoricals(frame: pl.DataFrame, profile: DatasetProfile | None) -> list[str]:
    """Grouping columns: text or categorical, with a categorical role, not personal."""
    columns = _profiled(profile)
    names = []
    for name, dtype in frame.schema.items():
        if dtype not in {pl.String, pl.Categorical, pl.Boolean} and not isinstance(dtype, pl.Enum):
            continue
        column = columns.get(name)
        if column is None or _is_personal(column) or column.semantic_role not in _CATEGORICAL_ROLES:
            continue
        names.append(name)
    return names


def preprocess_for_analysis(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    options: PreprocessingOptions,
) -> tuple[pl.DataFrame, list[PreprocessingStep], list[str]]:
    """Apply the analysis-scope options. Returns the frame, the log and warnings."""
    steps: list[PreprocessingStep] = []
    warnings: list[str] = []
    measures = analysis_measures(frame, profile)
    categoricals = analysis_categoricals(frame, profile)

    if options.missing_numeric == "drop_rows":
        frame, step = _drop_incomplete_rows(frame, measures)
        if step:
            steps.append(step)
            if frame.height == 0:
                warnings.append(
                    "Dropping rows with a missing measure removed every row; no analysis can run."
                )
    elif options.missing_numeric != "none":
        for name in measures:
            frame, step = _fill_numeric(frame, name, options.missing_numeric)
            if step:
                steps.append(step)

    if options.missing_categorical != "none":
        for name in categoricals:
            frame, step = _fill_categorical(frame, name, options.missing_categorical)
            if step:
                steps.append(step)

    if options.outliers == "cap_iqr":
        for name in measures:
            frame, step = _cap_iqr(frame, name)
            if step:
                steps.append(step)

    return frame, steps, warnings


# --------------------------------------------------------------------- steps
def _drop_incomplete_rows(
    frame: pl.DataFrame, measures: list[str]
) -> tuple[pl.DataFrame, PreprocessingStep | None]:
    if not measures:
        return frame, None
    kept = frame.drop_nulls(subset=measures)
    removed = frame.height - kept.height
    if not removed:
        return frame, None
    return kept, PreprocessingStep(
        step_id="analysis_drop_incomplete_rows",
        scope="analysis",
        action="drop_incomplete_rows",
        detail=(
            f"Removed {removed:,} of {frame.height:,} rows with a missing value in any of "
            f"{len(measures)} measure column(s)."
        ),
        rows_affected=removed,
    )


def _fill_numeric(
    frame: pl.DataFrame, name: str, strategy: str
) -> tuple[pl.DataFrame, PreprocessingStep | None]:
    missing = frame.get_column(name).null_count()
    if not missing:
        return frame, None
    series = frame.get_column(name).cast(pl.Float64)
    if series.drop_nulls().len() == 0:
        return frame, None
    value = {"median": series.median, "mean": series.mean}.get(strategy, lambda: 0.0)()
    value = float(value or 0.0)
    dtype = frame.schema[name]
    fill = pl.lit(round(value)).cast(dtype) if dtype.is_integer() else pl.lit(value).cast(dtype)
    label = {"median": "median", "mean": "mean", "zero": "zero"}[strategy]
    return frame.with_columns(pl.col(name).fill_null(fill)), PreprocessingStep(
        step_id=f"analysis_fill_{strategy}_{name}",
        scope="analysis",
        action=f"fill_missing_{strategy}",
        column=name,
        detail=f"Filled {missing:,} missing value(s) with the {label} ({_fmt(value)}).",
        rows_affected=missing,
    )


def _fill_categorical(
    frame: pl.DataFrame, name: str, strategy: str
) -> tuple[pl.DataFrame, PreprocessingStep | None]:
    missing = frame.get_column(name).null_count()
    if not missing:
        return frame, None
    dtype = frame.schema[name]
    as_text = frame.get_column(name).cast(pl.String)
    if strategy == "mode":
        counts = as_text.drop_nulls().value_counts(sort=False)
        if counts.height == 0:
            return frame, None
        # Most frequent, ties broken alphabetically, so reruns pick the same value.
        top = counts.sort(["count", name], descending=[True, False]).row(0)
        value, how = str(top[0]), "most common value"
    else:
        value, how = CONSTANT_FILL, "placeholder"
    filled = as_text.fill_null(value)
    if dtype != pl.String:
        filled = filled.cast(dtype) if dtype != pl.Boolean else filled.eq("true")
    return frame.with_columns(filled.alias(name)), PreprocessingStep(
        step_id=f"analysis_fill_{strategy}_{name}",
        scope="analysis",
        action=f"fill_missing_{strategy}",
        column=name,
        detail=f"Filled {missing:,} missing value(s) with the {how} '{value}'.",
        rows_affected=missing,
    )


def _cap_iqr(frame: pl.DataFrame, name: str) -> tuple[pl.DataFrame, PreprocessingStep | None]:
    series = frame.get_column(name).cast(pl.Float64).drop_nulls()
    if series.len() < 4:
        return frame, None
    q1 = series.quantile(0.25, interpolation="linear")
    q3 = series.quantile(0.75, interpolation="linear")
    if q1 is None or q3 is None or q3 - q1 <= 0:
        return frame, None
    low, high = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    outside = int(((series < low) | (series > high)).sum())
    if not outside:
        return frame, None
    dtype = frame.schema[name]
    capped = pl.col(name).cast(pl.Float64).clip(low, high)
    capped = capped.round(0).cast(dtype) if dtype.is_integer() else capped.cast(dtype)
    return frame.with_columns(capped.alias(name)), PreprocessingStep(
        step_id=f"analysis_cap_iqr_{name}",
        scope="analysis",
        action="cap_outliers_iqr",
        column=name,
        detail=(
            f"Capped {outside:,} value(s) outside the 1.5 x IQR fences "
            f"[{_fmt(low)}, {_fmt(high)}] to the nearest fence."
        ),
        rows_affected=outside,
    )


def _fmt(value: float) -> str:
    return f"{value:,.4g}" if abs(value) < 1e6 else f"{value:,.0f}"
