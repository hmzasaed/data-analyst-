"""Preprocessing: opt-in analysis changes, logged exactly, and the model-ready export."""

from __future__ import annotations

import math

import polars as pl
import pytest
from insightflow_contracts import (
    ColumnProfile,
    DatasetProfile,
    InferredType,
    PiiRisk,
    PreprocessingOptions,
    SemanticRole,
)
from insightflow_preprocessing import build_model_ready, preprocess_for_analysis


def _column(name, inferred, role, *, pii=PiiRisk.NONE) -> ColumnProfile:
    return ColumnProfile(name=name, inferred_type=inferred, semantic_role=role, pii_risk=pii)


@pytest.fixture
def frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "order_id": [1, 2, 3, 4, 5, 6, 7, 8],
            "year": [2020, 2020, 2021, 2021, 2022, 2022, 2023, None],
            "region": ["N", "N", "S", None, "S", "S", "E", None],
            "revenue": [10.0, 12.0, None, 11.0, 13.0, 500.0, 12.5, None],
            "email": ["a@x.io", None, "c@x.io", "d@x.io", "e@x.io", "f@x.io", "g@x.io", "h@x.io"],
        }
    )


@pytest.fixture
def profile(frame) -> DatasetProfile:
    return DatasetProfile(
        dataset_id="ds", source_file_hash="h", row_count=frame.height, column_count=frame.width,
        columns=[
            _column("order_id", InferredType.INTEGER, SemanticRole.IDENTIFIER),
            _column("year", InferredType.INTEGER, SemanticRole.TIME),
            _column("region", InferredType.CATEGORY, SemanticRole.DIMENSION),
            _column("revenue", InferredType.NUMBER, SemanticRole.MEASURE),
            _column("email", InferredType.TEXT, SemanticRole.FREE_TEXT, pii=PiiRisk.HIGH),
        ],
    )


class TestAnalysisScope:
    def test_defaults_change_nothing(self, frame, profile):
        result, steps, _ = preprocess_for_analysis(frame, profile, PreprocessingOptions())
        assert steps == []
        assert result.equals(frame)

    def test_median_fill_is_logged_with_the_exact_count(self, frame, profile):
        result, steps, _ = preprocess_for_analysis(
            frame, profile, PreprocessingOptions(missing_numeric="median")
        )
        assert result["revenue"].null_count() == 0
        expected = frame["revenue"].median()
        assert result["revenue"].to_list()[2] == expected
        (step,) = [s for s in steps if s.column == "revenue"]
        assert step.rows_affected == 2
        assert step.scope == "analysis"

    def test_identifiers_time_axes_and_personal_data_are_not_imputed(self, frame, profile):
        result, steps, _ = preprocess_for_analysis(
            frame, profile, PreprocessingOptions(missing_numeric="mean", missing_categorical="constant")
        )
        touched = {step.column for step in steps}
        assert "year" not in touched and "email" not in touched and "order_id" not in touched
        assert result["year"].null_count() == 1
        assert result["email"].null_count() == 1

    def test_mode_fill_is_deterministic(self, frame, profile):
        options = PreprocessingOptions(missing_categorical="mode")
        first, _, _ = preprocess_for_analysis(frame, profile, options)
        second, _, _ = preprocess_for_analysis(frame, profile, options)
        assert first["region"].to_list() == second["region"].to_list()
        assert first["region"].null_count() == 0
        assert first["region"].to_list()[3] == "S"  # the most common value

    def test_drop_rows_removes_only_incomplete_measure_rows(self, frame, profile):
        result, steps, _ = preprocess_for_analysis(
            frame, profile, PreprocessingOptions(missing_numeric="drop_rows")
        )
        assert result.height == 6
        assert steps[0].rows_affected == 2

    def test_outlier_capping_moves_values_to_the_fence(self, frame, profile):
        result, steps, _ = preprocess_for_analysis(
            frame, profile, PreprocessingOptions(outliers="cap_iqr")
        )
        (step,) = steps
        assert step.column == "revenue" and step.rows_affected == 1
        assert result["revenue"].max() < 500.0
        assert result.height == frame.height


class TestModelReadyExport:
    def test_unusable_columns_are_dropped_with_reasons(self, frame, profile):
        _, summary, _ = build_model_ready(frame, profile, PreprocessingOptions())
        reasons = {item.column: item.reason for item in summary.dropped_columns}
        assert reasons["order_id"] == "identifier"
        assert "personal data" in reasons["email"]

    def test_output_is_complete_encoded_and_scaled(self, frame, profile):
        data, summary, steps = build_model_ready(frame, profile, PreprocessingOptions())
        assert int(data.isna().sum().sum()) == 0
        assert "region" not in data.columns
        assert any(column.startswith("region=") for column in data.columns)
        assert summary.encoded_columns == ["region"]
        mean = float(data["revenue"].mean())
        assert math.isclose(mean, 0.0, abs_tol=1e-9)
        assert {step.scope for step in steps} == {"export"}
        assert summary.columns == data.shape[1]

    def test_date_columns_become_features(self):
        frame = pl.DataFrame(
            {"day": pl.date_range(pl.date(2024, 1, 1), pl.date(2024, 1, 10), eager=True),
             "sales": [float(i) for i in range(10)]}
        )
        data, summary, _ = build_model_ready(frame, None, PreprocessingOptions(scaling="none"))
        assert "day" not in data.columns
        assert {"day_year", "day_month", "day_dayofweek", "day_is_weekend"} <= set(data.columns)
        assert data["day_is_weekend"].sum() == 2  # 6-7 January 2024
        assert summary.date_feature_columns

    def test_high_missing_columns_respect_the_threshold(self):
        frame = pl.DataFrame({"a": [1.0, 2.0, None, None, None], "b": [1.0, 2.0, 3.0, 4.0, 5.0]})
        _, dropped, _ = build_model_ready(frame, None, PreprocessingOptions(drop_columns_missing_above=0.5))
        assert [item.column for item in dropped.dropped_columns] == ["a"]
        _, kept, _ = build_model_ready(frame, None, PreprocessingOptions(drop_columns_missing_above=None))
        assert kept.dropped_columns == []
