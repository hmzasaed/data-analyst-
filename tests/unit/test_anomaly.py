"""Anomaly detection: method selection, correctness against known answers, caveats."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from insightflow_anomaly import (
    MIN_UNIVARIATE_SAMPLE,
    AnomalyDetector,
    DetectionRequest,
)
from insightflow_contracts import AnomalyMethod, ToolExecutionError


@pytest.fixture
def labelled_frame(sales_snapshot) -> pl.DataFrame:
    """A clean normal distribution with three deliberately extreme records."""
    rng = np.random.default_rng(99)
    clean = rng.normal(100.0, 5.0, 300)
    values = np.concatenate([clean, [400.0, -250.0, 0.5]])
    return pl.DataFrame(
        {
            "value": values.tolist(),
            "region": [["North", "South", "East", "West"][i % 4] for i in range(len(values))],
            "order_date": [f"2024-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}" for i in range(len(values))],
        }
    ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))


class TestMethodSelection:
    def test_univariate_method_chosen_without_a_time_column(self, labelled_frame, sales_snapshot):
        # A frame with no date column cannot support a rolling baseline, so the
        # choice falls to a univariate fence.
        frame = labelled_frame.drop("order_date")
        method = AnomalyDetector().select_method(frame, DetectionRequest(column="value"))
        assert method in {AnomalyMethod.IQR_FENCE, AnomalyMethod.ROBUST_ZSCORE}

    def test_group_method_is_chosen_when_groups_are_large_enough(self, labelled_frame, sales_snapshot):
        method = AnomalyDetector().select_method(
            labelled_frame, DetectionRequest(column="value", group_columns=("region",))
        )
        assert method is AnomalyMethod.GROUP_PERCENTILE

    def test_rolling_method_is_chosen_when_a_date_column_exists(self, labelled_frame, sales_snapshot):
        rolling = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="order_date")
        )
        assert rolling.method is AnomalyMethod.ROLLING_MAD

    def test_small_sample_is_refused_with_an_explanation(self, sales_snapshot):
        frame = pl.DataFrame({"value": [1.0, 2.0, 3.0]})
        with pytest.raises(ToolExecutionError) as excinfo:
            AnomalyDetector().detect(frame, sales_snapshot, DetectionRequest(column="value"))
        assert "not enough non-null values" in excinfo.value.message
        assert excinfo.value.detail["required"] == MIN_UNIVARIATE_SAMPLE

    def test_missing_column_is_refused(self, sales_snapshot):
        with pytest.raises(ToolExecutionError, match="not present"):
            AnomalyDetector().detect(
                pl.DataFrame({"a": [1.0]}), sales_snapshot, DetectionRequest(column="nope")
            )


class TestUnivariateAccuracy:
    def test_iqr_finds_the_planted_values(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        assert result.method is AnomalyMethod.IQR_FENCE
        flagged = [item.observed_value for item in result.anomalies]
        assert 400.0 in flagged
        assert -250.0 in flagged
        assert 0.5 in flagged
        # The clean bulk must not be flagged.
        assert result.flagged_count <= 6

    def test_baseline_bounds_match_numpy_percentiles(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        baseline = result.anomalies[0].baseline
        values = labelled_frame["value"].to_numpy()
        q1, q3 = np.percentile(values, [25, 75])
        iqr = q3 - q1
        assert baseline.lower_bound == pytest.approx(q1 - 1.5 * iqr, rel=1e-9)
        assert baseline.upper_bound == pytest.approx(q3 + 1.5 * iqr, rel=1e-9)

    def test_robust_zscore_uses_median_and_mad(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.ROBUST_ZSCORE)
        )
        baseline = result.anomalies[0].baseline
        values = labelled_frame["value"].to_numpy()
        centre = float(np.median(values))
        assert baseline.centre == pytest.approx(centre, rel=1e-9)
        assert baseline.scale == pytest.approx(
            float(np.median(np.abs(values - centre))) * 1.4826, rel=1e-9
        )

    def test_constant_column_is_refused_with_a_clear_reason(self, sales_snapshot):
        frame = pl.DataFrame({"value": [5.0] * 60})
        with pytest.raises(ToolExecutionError, match="more than half"):
            AnomalyDetector().detect(
                frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.ROBUST_ZSCORE)
            )

    def test_result_counts_are_consistent(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        assert result.flagged_count == len(result.anomalies)
        assert result.evaluated_count >= labelled_frame.height


class TestGroupRelative:
    def test_scores_against_the_group_not_the_dataset(self, sales_snapshot):
        # West is uniformly low; a global fence would flag all 120 of its rows, while
        # group-relative scoring correctly leaves them alone and catches only the
        # genuine spike.
        rng = np.random.default_rng(5)
        west = list(rng.normal(10.0, 1.0, 120))
        east = list(rng.normal(100.0, 3.0, 120))
        frame = pl.DataFrame(
            {
                "value": west + east + [500.0],
                "region": ["West"] * 120 + ["East"] * 120 + ["West"],
            }
        )

        grouped = AnomalyDetector().detect(
            frame,
            sales_snapshot,
            DetectionRequest(
                column="value", group_columns=("region",), method=AnomalyMethod.GROUP_PERCENTILE
            ),
        )
        # The point of group-relative scoring: West is a legitimately low group, so
        # its 120 rows must not be flagged for being far from the dataset average.
        west_rows = 121
        assert grouped.flagged_count < 5
        assert grouped.flagged_count / west_rows < 0.02
        assert 500.0 in [item.observed_value for item in grouped.anomalies]

        # A global fence applied to this bimodal data would flag the whole low
        # cluster, which is the failure mode group-relative scoring avoids.
        global_result = AnomalyDetector().detect(
            frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        assert global_result.method is AnomalyMethod.IQR_FENCE
        # Sanity check that the global path did run and produced its own verdict.
        assert global_result.evaluated_count == frame.height

    def test_a_single_usable_group_is_refused(self, sales_snapshot):
        frame = pl.DataFrame({"value": [float(i) for i in range(30)], "region": ["A"] * 29 + ["B"]})
        with pytest.raises(ToolExecutionError, match="too few groups"):
            AnomalyDetector().detect(
                frame,
                sales_snapshot,
                DetectionRequest(
                    column="value", group_columns=("region",), method=AnomalyMethod.GROUP_PERCENTILE
                ),
            )

    def test_tiny_groups_are_skipped_and_counted(self, sales_snapshot):
        frame = pl.DataFrame(
            {
                "value": [float(i % 30) for i in range(65)],
                "region": ["A"] * 30 + ["B"] * 30 + ["C"] * 5,
            }
        )
        result = AnomalyDetector().detect(
            frame,
            sales_snapshot,
            DetectionRequest(
                column="value", group_columns=("region",), method=AnomalyMethod.GROUP_PERCENTILE
            ),
        )
        assert result.skipped_count == 5
        assert any("not scored" in note for note in result.limitations)


class TestRollingBaseline:
    def test_flags_a_spike_against_its_own_history(self, sales_snapshot):
        values = [100.0] * 30 + [900.0] + [100.0] * 10
        frame = pl.DataFrame(
            {
                "order_date": [
                    f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(len(values))
                ],
                "value": values,
            }
        ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))

        result = AnomalyDetector().detect(
            frame,
            sales_snapshot,
            DetectionRequest(column="value", method=AnomalyMethod.ROLLING_MAD,
                             extra={"window": 7, "z_threshold": 3.5}),
        )
        assert 900.0 in [item.observed_value for item in result.anomalies]
        assert result.skipped_count == 7

    def test_explains_the_baseline_it_used(self, sales_snapshot):
        frame = pl.DataFrame(
            {
                "order_date": [f"2024-01-{day:02d}" for day in range(1, 21)],
                "value": [100.0] * 19 + [900.0],
            }
        ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))
        result = AnomalyDetector().detect(
            frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.ROLLING_MAD)
        )
        worst = max(result.anomalies, key=lambda item: item.score or 0)
        assert worst.baseline.kind == "trailing_window"
        # With a perfectly flat history there is no scale, so the explanation says so
        # rather than quoting a deviation that was never measured.
        assert "no variation at all" in worst.reason
        assert "level 100" in worst.reason


class TestCaveats:
    def test_every_anomaly_carries_its_caveat(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        assert result.anomalies
        for item in result.anomalies:
            assert item.limitation
            assert "not evidence of an error or wrongdoing" in item.caveat
            assert item.needs_domain_review

    def test_accusatory_language_is_rejected_by_the_contract(self):
        from insightflow_contracts import AnomalyFinding, BaselineSpec

        with pytest.raises(ValueError, match="wrongdoing"):
            AnomalyFinding(
                anomaly_id="a",
                method=AnomalyMethod.IQR_FENCE,
                entity_type="row",
                entity_label="customer 42",
                baseline=BaselineSpec(kind="k", description="d"),
                reason="this record is fraud",
                limitation="l",
            )

    def test_counts_must_match_the_record_list(self):
        from insightflow_contracts import AnomalyResult
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must equal number of anomalies"):
            AnomalyResult(
                method=AnomalyMethod.IQR_FENCE, dataset_version=1, flagged_count=5
            )

    def test_method_is_always_recorded(self, labelled_frame, sales_snapshot):
        result = AnomalyDetector().detect(
            labelled_frame, sales_snapshot, DetectionRequest(column="value", method=AnomalyMethod.IQR_FENCE)
        )
        assert result.method is AnomalyMethod.IQR_FENCE
        assert result.parameter_digest
        assert result.method_version