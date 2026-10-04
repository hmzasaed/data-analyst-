"""Analysis tools: correctness against known answers, and plan validation."""

from __future__ import annotations

import polars as pl
import pytest
from insightflow_analytics import REGISTRY, AnalysisContext, PlanValidator, execute_plan
from insightflow_contracts import (
    AnalysisPlan,
    AnalysisType,
    PlannedStep,
    PlanRejectedError,
    ToolExecutionError,
)


@pytest.fixture
def ctx(sales_frame, sales_snapshot, tmp_path):
    return AnalysisContext(
        frame=sales_frame, snapshot=sales_snapshot, artifact_dir=tmp_path, step_id="t"
    )


def _run(ctx, tool, parameters, step_id="s"):
    plan = AnalysisPlan(
        objective="t",
        steps=[PlannedStep(step_id=step_id, analysis_type=AnalysisType.DESCRIPTIVE,
                           tool_name=tool, parameters=parameters)],
    )
    artifacts = execute_plan(plan, REGISTRY, ctx.frame, ctx.snapshot, ctx.artifact_dir)
    return artifacts


class TestRegistry:
    def test_expected_tools_are_registered(self):
        expected = {
            "describe_measures", "rank_categories", "missingness_profile", "value_distribution",
            "distribution_by_group", "compare_groups", "share_of_total", "missingness_by_group",
            "time_series_aggregate", "trend_line", "growth_rates", "moving_average",
            "seasonality_strength", "correlation_matrix", "cross_tabulation",
            "simple_regression", "detect_outliers", "detect_group_outliers",
            "detect_multivariate_outliers",
        }
        assert expected <= set(REGISTRY.names())

    def test_every_tool_declares_a_strict_schema(self):
        for spec in REGISTRY.catalogue():
            assert spec.version
            assert spec.summary
            assert spec.max_rows > 0
            assert spec.timeout_seconds > 0

    def test_tool_names_are_snake_case(self):
        for name in REGISTRY.names():
            assert name.islower()
            assert " " not in name

    def test_unknown_tool_is_rejected(self, ctx):
        with pytest.raises(PlanRejectedError, match="unknown tool"):
            REGISTRY.validate_parameters("does_not_exist", {}, context=ctx)

    def test_unknown_parameter_is_rejected(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters(
                "compare_groups",
                {"group_by": "region", "column": "revenue", "bogus": 1},
                context=ctx,
            )
        assert any("unknown parameters" in v for v in excinfo.value.violations)

    def test_missing_required_parameter_is_rejected(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters("compare_groups", {"group_by": "region"}, context=ctx)
        assert any("is required" in v for v in excinfo.value.violations)

    def test_wrong_column_type_is_rejected(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters(
                "compare_groups", {"group_by": "region", "column": "region"}, context=ctx
            )
        assert any("not numeric" in v for v in excinfo.value.violations)

    def test_numeric_column_cannot_be_a_group_key(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters(
                "compare_groups", {"group_by": "revenue", "column": "units"}, context=ctx
            )
        assert any("grouping key" in v for v in excinfo.value.violations)

    def test_out_of_range_number_is_rejected(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters(
                "value_distribution", {"column": "revenue", "bins": 100000}, context=ctx
            )
        assert any("must be <=" in v for v in excinfo.value.violations)

    def test_enum_is_restricted(self, ctx):
        with pytest.raises(PlanRejectedError) as excinfo:
            REGISTRY.validate_parameters(
                "time_series_aggregate",
                {"date_column": "order_date", "grain": "fortnightly"},
                context=ctx,
            )
        assert any("expected one of" in v for v in excinfo.value.violations)


class TestDescriptiveTools:
    def test_describe_measures_matches_numpy(self, ctx, sales_frame):
        artifacts = _run(ctx, "describe_measures", {"columns": ["revenue", "units"]})
        assert not artifacts.failed_steps()
        metrics = {m.name: m.value for m in artifacts.all_metrics()}

        import numpy as np

        expected_mean = float(sales_frame["revenue"].mean())
        assert metrics["revenue__mean"] == pytest.approx(expected_mean, rel=1e-9)
        assert metrics["revenue__total"] == pytest.approx(
            float(sales_frame["revenue"].sum()), rel=1e-9
        )
        assert metrics["revenue__median"] == pytest.approx(
            float(np.median(sales_frame["revenue"].to_numpy())), rel=1e-9
        )

    def test_describe_measures_writes_an_evidence_table(self, ctx):
        artifacts = _run(ctx, "describe_measures", {"columns": ["revenue"]})
        result = artifacts.results[0]
        assert result.tables
        assert result.tables[0].row_count == 1

    def test_metrics_carry_a_definition_and_sample_size(self, ctx):
        artifacts = _run(ctx, "describe_measures", {"columns": ["revenue"]})
        for metric in artifacts.all_metrics():
            assert metric.definition
            assert metric.n is not None

    def test_value_distribution_bins_sum_to_row_count(self, ctx, sales_frame):
        artifacts = _run(ctx, "value_distribution", {"column": "revenue", "bins": 10})
        table = artifacts.results[0].tables[0]
        frame = pl.read_csv(table.path)
        assert frame["count"].sum() == sales_frame.height

    def test_missingness_profile_on_complete_data(self, ctx):
        artifacts = _run(ctx, "missingness_profile", {})
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        # A complete dataset must not nominate an arbitrary "worst" column.
        assert metrics["worst_missing_column"] is None
        assert metrics["worst_missing_share"] == 0.0


class TestComparisonTools:
    def test_compare_groups_mean_matches_groupby(self, ctx, sales_frame):
        artifacts = _run(ctx, "compare_groups", {"group_by": "region", "column": "revenue"})
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        expected = sales_frame.group_by("region").agg(pl.col("revenue").mean())
        highest = float(expected["revenue"].max())
        lowest = float(expected["revenue"].min())
        assert metrics["largest_group_mean"] == pytest.approx(highest, rel=1e-9)
        assert metrics["smallest_group_mean"] == pytest.approx(lowest, rel=1e-9)
        assert metrics["group_gap_mean"] == pytest.approx(highest - lowest, rel=1e-9)

    def test_compare_groups_reports_its_test_and_assumptions(self, ctx):
        artifacts = _run(ctx, "compare_groups", {"group_by": "region", "column": "revenue"})
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        assert metrics["group_difference_test"] in {"welch_t_test", "mann_whitney_u"}
        p_value = metrics["group_difference_p_value"]
        assert p_value is None or 0.0 <= p_value <= 1.0

    def test_share_of_total_sums_to_one_hundred(self, ctx, sales_frame):
        artifacts = _run(ctx, "share_of_total", {"group_by": "region", "column": "revenue"})
        table = artifacts.results[0].tables[0]
        frame = pl.read_csv(table.path)
        assert frame["share"].sum() == pytest.approx(1.0, abs=1e-9)
        assert frame["cumulative_share"][-1] == pytest.approx(1.0, abs=1e-9)

    def test_share_of_total_refuses_a_zero_total(self, ctx):
        frame = pl.DataFrame({"region": ["a", "b", "a"], "value": [0.0, 0.0, 0.0]})
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="z", analysis_type=AnalysisType.COMPARISON,
                               tool_name="share_of_total",
                               parameters={"group_by": "region", "column": "value"})],
        )
        artifacts = execute_plan(plan, REGISTRY, frame, ctx.snapshot, ctx.artifact_dir)
        assert artifacts.failed_steps() == ["z"]
        assert "zero" in artifacts.results[0].errors[0].lower()


class TestTrendTools:
    def test_time_series_aggregates_to_the_requested_grain(self, ctx, sales_frame):
        artifacts = _run(
            ctx, "time_series_aggregate",
            {"date_column": "order_date", "grain": "monthly", "measures": ["revenue"]},
        )
        assert not artifacts.failed_steps()
        table = artifacts.results[0].tables[0]
        frame = pl.read_csv(table.path)
        assert frame.height == 12
        assert frame["revenue_total"].sum() == pytest.approx(
            float(sales_frame["revenue"].sum()), rel=1e-9
        )

    def test_short_series_refuses_a_trend_fit(self, ctx, sales_snapshot):
        frame = pl.DataFrame(
            {
                "order_date": [f"2024-01-{day:02d}" for day in range(1, 4)],
                "revenue": [1.0, 2.0, 3.0],
            }
        ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="t1", analysis_type=AnalysisType.TREND,
                               tool_name="trend_line",
                               parameters={"date_column": "order_date", "column": "revenue"})],
        )
        artifacts = execute_plan(plan, REGISTRY, frame, sales_snapshot, ctx.artifact_dir)
        assert artifacts.failed_steps() == ["t1"]
        assert "few periods" in artifacts.results[0].errors[0].lower()

    def test_growth_rates_count_undefined_periods(self, ctx):
        frame = pl.DataFrame(
            {
                "order_date": [f"2024-01-{day:02d}" for day in range(1, 7)],
                "revenue": [100.0, 0.0, 50.0, 60.0, 0.0, 80.0],
            }
        ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="g", analysis_type=AnalysisType.TREND,
                               tool_name="growth_rates",
                               parameters={"date_column": "order_date", "column": "revenue"})],
        )
        artifacts = execute_plan(plan, REGISTRY, frame, ctx.snapshot, ctx.artifact_dir)
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        # Growth after a zero period is undefined and must be counted, not filled.
        assert metrics["growth_periods_undefined"] >= 2


class TestRelationshipTools:
    def test_correlation_matches_pearson(self, ctx, sales_frame):
        artifacts = _run(ctx, "correlation_matrix", {"columns": ["revenue", "units"]})
        assert not artifacts.failed_steps()
        import numpy as np

        expected = float(
            np.corrcoef(
                sales_frame["revenue"].to_numpy(), sales_frame["units"].to_numpy()
            )[0, 1]
        )
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        assert metrics["strongest_correlation"] == pytest.approx(expected, abs=1e-6)

    def test_correlation_carries_a_causality_warning(self, ctx):
        artifacts = _run(ctx, "correlation_matrix", {"columns": ["revenue", "units"]})
        warnings = " ".join(artifacts.warnings).lower()
        assert "does not establish" in warnings

    def test_regression_reports_fit_and_caveat(self, ctx):
        artifacts = _run(
            ctx, "simple_regression", {"response": "revenue", "predictor": "units"}
        )
        metrics = {m.name: m.value for m in artifacts.all_metrics()}
        assert 0.0 <= metrics["regression_r_squared"] <= 1.0
        assert "must not be read as an effect" in " ".join(artifacts.warnings).lower()

    def test_regression_refuses_too_few_rows(self, ctx):
        frame = ctx.frame.head(6)
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="r", analysis_type=AnalysisType.RELATIONSHIP,
                               tool_name="simple_regression",
                               parameters={"response": "revenue", "predictor": "units"})],
        )
        artifacts = execute_plan(plan, REGISTRY, frame, ctx.snapshot, ctx.artifact_dir)
        assert artifacts.failed_steps() == ["r"]
        assert "too few complete rows" in artifacts.results[0].errors[0].lower()


class TestPlanValidation:
    def test_valid_plan_passes(self, ctx):
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="s", analysis_type=AnalysisType.COMPARISON,
                               tool_name="compare_groups",
                               parameters={"group_by": "region", "column": "revenue"})],
        )
        validated = PlanValidator(REGISTRY).validate(plan, ctx)
        assert len(validated.runnable_steps) == 1

    def test_unregistered_tool_is_rejected(self, ctx):
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                               tool_name="invent_a_method", parameters={})],
        )
        with pytest.raises(PlanRejectedError) as excinfo:
            PlanValidator(REGISTRY).validate(plan, ctx)
        assert any("not registered" in v for v in excinfo.value.violations)

    def test_plan_with_no_runnable_steps_is_rejected(self, ctx):
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                               tool_name="describe_measures",
                               parameters={"columns": ["nonexistent_column"]})],
        )
        with pytest.raises(PlanRejectedError):
            PlanValidator(REGISTRY).validate(plan, ctx)

    def test_duplicate_step_ids_are_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="duplicate step_id"):
            AnalysisPlan(
                objective="t",
                steps=[
                    PlannedStep(step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                                tool_name="describe_measures", parameters={}),
                    PlannedStep(step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                                tool_name="describe_measures", parameters={}),
                ],
            )

    def test_unknown_expected_output_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="unknown expected_outputs"):
            PlannedStep(
                step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                tool_name="describe_measures", parameters={}, expected_outputs=["vibes"],
            )


class TestExecutionBehaviour:
    def test_failed_step_does_not_stop_the_plan(self, ctx):
        plan = AnalysisPlan(
            objective="t",
            steps=[
                PlannedStep(step_id="good", analysis_type=AnalysisType.DESCRIPTIVE,
                            tool_name="describe_measures", parameters={"columns": ["revenue"]}),
                PlannedStep(step_id="bad", analysis_type=AnalysisType.COMPARISON,
                            tool_name="compare_groups",
                            parameters={"group_by": "region", "column": "missing"}),
                PlannedStep(step_id="also_good", analysis_type=AnalysisType.DESCRIPTIVE,
                            tool_name="missingness_profile", parameters={}),
            ],
        )
        artifacts = execute_plan(plan, REGISTRY, ctx.frame, ctx.snapshot, ctx.artifact_dir)
        assert artifacts.succeeded_steps() == ["good", "also_good"]
        assert artifacts.failed_steps() == ["bad"]
        assert artifacts.errors()

    def test_metrics_are_tagged_with_their_step(self, ctx):
        artifacts = _run(ctx, "describe_measures", {"columns": ["revenue"]}, step_id="mystep")
        for metric in artifacts.all_metrics():
            assert metric.source_step == "mystep"

    def test_execution_is_reproducible(self, ctx, sales_frame, sales_snapshot, tmp_path):
        plan = AnalysisPlan(
            objective="t",
            steps=[PlannedStep(step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                               tool_name="describe_measures",
                               parameters={"columns": ["revenue"]})],
        )
        first = execute_plan(plan, REGISTRY, sales_frame, sales_snapshot, tmp_path / "a")
        second = execute_plan(plan, REGISTRY, sales_frame, sales_snapshot, tmp_path / "b")
        assert [m.value for m in first.all_metrics()] == [m.value for m in second.all_metrics()]
        assert first.results[0].tool_digest == second.results[0].tool_digest

    def test_column_guard_blocks_a_missing_column(self, ctx):
        with pytest.raises(ToolExecutionError, match="not present"):
            ctx.require_columns("does_not_exist")