"""Turn the engineered frame into a numeric model matrix and a target vector."""

from __future__ import annotations

import pandas as pd
import polars as pl
from insightflow_contracts import DatasetProfile, PreprocessingOptions
from insightflow_preprocessing import build_model_ready


def build_matrix(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    target: str,
    engineered: set[str],
    options: PreprocessingOptions,
) -> tuple[pd.DataFrame, pd.Series, dict[str, str], list[str]]:
    """Encode, impute and scale everything except the target, with the export's rules.

    Returns the matrix, the target, each column's origin and the export steps' notes.
    Selection reuses the model-ready export pipeline so the feature set and the
    model-ready dataset are prepared identically.
    """
    target_values = frame.get_column(target).to_pandas()
    inputs = frame.drop(target)
    matrix, summary, steps = build_model_ready(inputs, profile, options)
    originals = set(frame.columns)
    origins = {
        column: "engineered" if column in engineered else "original" if column in originals else "encoded"
        for column in matrix.columns
    }
    notes = [step.detail for step in steps]
    notes.extend(f"Left out '{item.column}': {item.reason}." for item in summary.dropped_columns)
    return matrix.reset_index(drop=True), target_values.reset_index(drop=True), origins, notes
