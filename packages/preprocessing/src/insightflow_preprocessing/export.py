"""The model-ready export: a copy of the data shaped for machine learning.

Typical preprocessing a data scientist would do before fitting a model - drop
columns a model cannot use, derive date features, impute what is left, one-hot
encode categories and scale numbers - done with pandas, NumPy and scikit-learn,
and logged step by step. It is a download, not an input to the report: scaled
and encoded values are not business figures.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import polars as pl
from insightflow_contracts import (
    ColumnProfile,
    DatasetProfile,
    DroppedColumn,
    ExportSummary,
    PreprocessingOptions,
    PreprocessingStep,
    SemanticRole,
)
from sklearn.preprocessing import MinMaxScaler, RobustScaler, StandardScaler

EXPORT_ARTIFACT_ID = "model_ready_dataset"

_SCALERS = {
    "standard": (StandardScaler, "standardised to mean 0 and standard deviation 1"),
    "minmax": (MinMaxScaler, "rescaled to the range 0-1"),
    "robust": (RobustScaler, "centred on the median and scaled by the IQR"),
}


def build_model_ready(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    options: PreprocessingOptions,
) -> tuple[pd.DataFrame, ExportSummary, list[PreprocessingStep]]:
    """Return the model-ready table, its summary and the log of what was done."""
    columns = {column.name: column for column in (profile.columns if profile else [])}
    data = frame.to_pandas()
    steps: list[PreprocessingStep] = []
    dropped: list[DroppedColumn] = []

    # 1. Columns a model should not see.
    for name in list(data.columns):
        reason = _drop_reason(data[name], columns.get(name), options)
        if reason:
            dropped.append(DroppedColumn(column=name, reason=reason))
    if dropped:
        data = data.drop(columns=[item.column for item in dropped])
        steps.append(
            PreprocessingStep(
                step_id="export_drop_columns",
                scope="export",
                action="drop_columns",
                detail=f"Dropped {len(dropped)} column(s) a model cannot use: "
                + "; ".join(f"{item.column} ({item.reason})" for item in dropped[:12]),
                columns_removed=[item.column for item in dropped],
            )
        )

    # 2. Date features: models cannot take a raw date.
    date_features: list[str] = []
    if options.date_features:
        for name in [c for c in data.columns if pd.api.types.is_datetime64_any_dtype(data[c])]:
            stamp = data[name]
            derived = {
                f"{name}_year": stamp.dt.year,
                f"{name}_month": stamp.dt.month,
                f"{name}_quarter": stamp.dt.quarter,
                f"{name}_dayofweek": stamp.dt.dayofweek,
                f"{name}_is_weekend": (stamp.dt.dayofweek >= 5).astype("Int64"),
            }
            for key, values in derived.items():
                data[key] = values.astype("Float64")
            data = data.drop(columns=[name])
            date_features.extend(derived)
            steps.append(
                PreprocessingStep(
                    step_id=f"export_date_features_{name}",
                    scope="export",
                    action="extract_date_features",
                    column=name,
                    detail=f"Replaced '{name}' with year, month, quarter, day-of-week and weekend flag.",
                    columns_added=list(derived),
                    columns_removed=[name],
                )
            )
    else:
        stray = [c for c in data.columns if pd.api.types.is_datetime64_any_dtype(data[c])]
        if stray:
            data = data.drop(columns=stray)
            dropped.extend(DroppedColumn(column=c, reason="date (date features disabled)") for c in stray)

    numeric = [c for c in data.columns if pd.api.types.is_numeric_dtype(data[c])
               and not pd.api.types.is_bool_dtype(data[c])]
    categorical = [c for c in data.columns if c not in numeric]

    # 3. Imputation: most estimators reject missing values.
    for name in numeric:
        missing = int(data[name].isna().sum())
        if missing:
            median = float(data[name].median()) if data[name].notna().any() else 0.0
            data[name] = data[name].astype("Float64").fillna(median)
            steps.append(_step("export_impute_median", name, f"Filled {missing:,} missing value(s) with the median ({median:,.4g}).", missing))
    for name in categorical:
        missing = int(data[name].isna().sum())
        if missing:
            data[name] = data[name].astype("object").where(data[name].notna(), "Missing")
            steps.append(_step("export_impute_missing_label", name, f"Labelled {missing:,} missing value(s) as 'Missing'.", missing))

    # 4. Encoding.
    encoded: list[str] = []
    if options.encode_categoricals and categorical:
        too_many = [c for c in categorical if data[c].nunique(dropna=False) > options.max_encoding_levels]
        for name in too_many:
            dropped.append(DroppedColumn(
                column=name,
                reason=f"{data[name].nunique(dropna=False)} categories, above the one-hot limit of {options.max_encoding_levels}",
            ))
        data = data.drop(columns=too_many)
        to_encode = [c for c in categorical if c not in too_many]
        if to_encode:
            before = set(data.columns)
            data = pd.get_dummies(
                data, columns=to_encode, prefix=to_encode, prefix_sep="=", dtype="int8"
            )
            added = [c for c in data.columns if c not in before]
            encoded = to_encode
            steps.append(
                PreprocessingStep(
                    step_id="export_one_hot",
                    scope="export",
                    action="one_hot_encode",
                    detail=f"One-hot encoded {len(to_encode)} categorical column(s) into {len(added)} indicator column(s).",
                    columns_added=added,
                    columns_removed=to_encode,
                )
            )
    elif categorical:
        data = data.drop(columns=categorical)
        dropped.extend(DroppedColumn(column=c, reason="categorical (encoding disabled)") for c in categorical)

    # 5. Scaling of the original continuous measures (not indicators or date parts).
    scaled = [c for c in numeric if c in data.columns and c not in date_features
              and data[c].nunique() > 2]
    if options.scaling != "none" and scaled:
        scaler_cls, description = _SCALERS[options.scaling]
        values = data[scaled].astype("float64").to_numpy()
        data[scaled] = scaler_cls().fit_transform(values)
        steps.append(
            PreprocessingStep(
                step_id=f"export_scale_{options.scaling}",
                scope="export",
                action=f"scale_{options.scaling}",
                detail=f"{len(scaled)} numeric column(s) {description} (scikit-learn {scaler_cls.__name__}).",
            )
        )

    data = data.replace([np.inf, -np.inf], np.nan)
    summary = ExportSummary(
        artifact_id=EXPORT_ARTIFACT_ID,
        rows=int(data.shape[0]),
        columns=int(data.shape[1]),
        column_names=[str(c) for c in data.columns],
        scaling=options.scaling if scaled else "none",
        encoded_columns=encoded,
        date_feature_columns=date_features,
        dropped_columns=dropped,
    )
    return data, summary, steps


def _drop_reason(series: pd.Series, column: ColumnProfile | None, options: PreprocessingOptions) -> str | None:
    if column is not None:
        # Personal data is named first: it is the reason that matters most to a reader.
        if column.pii_risk.value in {"medium", "high"}:
            return f"personal data ({column.pii_risk.value} risk)"
        if column.semantic_role is SemanticRole.IDENTIFIER:
            return "identifier"
        if column.semantic_role is SemanticRole.FREE_TEXT:
            return "free text"
    if series.isna().all():
        return "empty"
    if series.nunique(dropna=True) <= 1:
        return "constant"
    threshold = options.drop_columns_missing_above
    share = float(series.isna().mean())
    if threshold is not None and share > threshold:
        return f"{share:.0%} missing, above the {threshold:.0%} limit"
    return None


def _step(action: str, column: str, detail: str, rows: int) -> PreprocessingStep:
    return PreprocessingStep(
        step_id=f"{action}_{column}", scope="export", action=action.removeprefix("export_"),
        column=column, detail=detail, rows_affected=rows,
    )
