"""Visualization: rendering fidelity, determinism and chart/table agreement."""

from __future__ import annotations

import polars as pl
import pytest
from insightflow_analytics import REGISTRY, execute_plan
from insightflow_contracts import (
    AnalysisPlan,
    AnalysisType,
    ChartKind,
    ChartRequest,
    ChartSeries,
    PlannedStep,
)
from insightflow_visualization import render_charts, verify_chart_matches


@pytest.fixture
def chart_requests(sales_frame, sales_snapshot, tmp_path):
    plan = AnalysisPlan(
        objective="charts",
        steps=[
            PlannedStep(step_id="a", analysis_type=AnalysisType.COMPARISON,
                        tool_name="compare_groups",
                        parameters={"group_by": "region", "column": "revenue", "unit": "USD"}),
            PlannedStep(step_id="b", analysis_type=AnalysisType.TREND,
                        tool_name="time_series_aggregate",
                        parameters={"date_column": "order_date", "grain": "monthly",
                                    "measures": ["revenue"], "unit": "USD"}),
            PlannedStep(step_id="c", analysis_type=AnalysisType.DISTRIBUTION,
                        tool_name="value_distribution",
                        parameters={"column": "revenue", "bins": 15}),
            PlannedStep(step_id="d", analysis_type=AnalysisType.RELATIONSHIP,
                        tool_name="correlation_matrix",
                        parameters={"columns": ["revenue", "units"]}),
            PlannedStep(step_id="e", analysis_type=AnalysisType.DISTRIBUTION,
                        tool_name="distribution_by_group",
                        parameters={"group_by": "region", "column": "revenue"}),
        ],
    )
    artifacts = execute_plan(plan, REGISTRY, sales_frame, sales_snapshot, tmp_path)
    assert not artifacts.failed_steps(), [
        (r.step_id, r.errors) for r in artifacts.results if r.status == "failed"
    ]
    return artifacts, plan


class TestRendering:
    def test_every_chart_renders_to_a_png(self, chart_requests, tmp_path):
        artifacts, _ = chart_requests
        rendered, warnings = render_charts(artifacts.charts, tmp_path / "charts")
        assert len(rendered) == len(artifacts.charts)
        for artifact in rendered:
            assert artifact.path.endswith(".png")
            assert artifact.byte_size > 1000
            assert artifact.sha256
        assert warnings == []

    def test_rendering_is_deterministic(self, chart_requests, tmp_path):
        artifacts, _ = chart_requests
        first, _ = render_charts(artifacts.charts, tmp_path / "one")
        second, _ = render_charts(artifacts.charts, tmp_path / "two")
        assert [a.sha256 for a in first] == [b.sha256 for b in second]

    def test_every_chart_carries_a_caption_and_caveat(self, chart_requests):
        artifacts, _ = chart_requests
        for chart in artifacts.charts:
            assert chart.title
            assert chart.caveat, f"{chart.kind} chart has no caveat"
            if chart.y_unit:
                assert chart.y_label

    def test_all_chart_kinds_are_exercised(self, chart_requests):
        artifacts, _ = chart_requests
        kinds = {chart.kind for chart in artifacts.charts}
        assert ChartKind.BAR in kinds
        assert ChartKind.LINE in kinds
        assert ChartKind.HISTOGRAM in kinds
        assert ChartKind.HEATMAP in kinds
        assert ChartKind.BOX in kinds


class TestChartDataIntegrity:
    def test_chart_values_match_the_evidence_table(self, chart_requests):
        artifacts, _ = chart_requests
        results = {result.step_id: result for result in artifacts.results}
        # compare_groups charts the group means it also wrote to a table.
        table = pl.read_csv(results["a"].tables[0].path)
        chart = next(
            c for c in artifacts.charts
            if c.kind in {ChartKind.BAR, ChartKind.HORIZONTAL_BAR}
        )
        plotted = sorted(round(v, 6) for v in chart.series[0].y)
        expected = sorted(round(v, 6) for v in table["mean"].to_list())
        assert plotted == expected

    def test_trend_chart_matches_its_table(self, chart_requests):
        artifacts, _ = chart_requests
        results = {result.step_id: result for result in artifacts.results}
        table = pl.read_csv(results["b"].tables[0].path)
        chart = next(c for c in artifacts.charts if c.kind is ChartKind.LINE)
        plotted = [v for v in chart.series[0].y if v is not None]
        expected = table["revenue_total"].to_list()
        assert len(plotted) == len(expected)
        for left, right in zip(plotted, expected, strict=True):
            assert left == pytest.approx(right, rel=1e-9)

    def test_verifier_reports_no_problems_for_real_charts(self, chart_requests):
        artifacts, _ = chart_requests
        for chart in artifacts.charts:
            assert verify_chart_matches(chart) == []


class TestChartVerification:
    def test_mismatch_is_detected(self, tmp_path):
        chart = ChartRequest(
            chart_id="chart_bad",
            kind=ChartKind.BAR,
            title="Mismatch",
            series=[ChartSeries(name="s", x=["a", "b"], y=[1.0, 2.0])],
            x_label="x",
            y_label="y",
        )
        source = pl.DataFrame({"value": [1.0, 2.0, 3.0]})
        problems = verify_chart_matches(chart, source=source, value_column="value")
        assert problems
        assert "chart plots 2 value(s) but the evidence table holds 3" in problems[0]

    def test_altered_value_is_detected(self, tmp_path):
        chart = ChartRequest(
            chart_id="chart_altered",
            kind=ChartKind.BAR,
            title="Altered",
            series=[ChartSeries(name="s", x=["a", "b"], y=[1.0, 99.0])],
        )
        source = pl.DataFrame({"value": [1.0, 2.0]})
        problems = verify_chart_matches(chart, source=source, value_column="value")
        assert any("differ" in problem for problem in problems)

    def test_heatmap_without_a_matrix_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="matrix"):
            ChartRequest(
                chart_id="c", kind=ChartKind.HEATMAP, title="t",
                series=[ChartSeries(name="s", x=["a"], y=[1.0])],
            )

    def test_chart_with_no_series_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="at least one series"):
            ChartRequest(chart_id="c", kind=ChartKind.BAR, title="t", series=[])

    def test_unit_without_a_label_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="y_label"):
            ChartRequest(
                chart_id="c", kind=ChartKind.BAR, title="t",
                series=[ChartSeries(name="s", x=["a"], y=[1.0])],
                y_unit="%",
            )

    def test_misaligned_series_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="x values"):
            ChartSeries(name="s", x=["a", "b"], y=[1.0])

    def test_rendering_an_empty_chart_fails_cleanly(self, tmp_path):
        from insightflow_contracts import ToolExecutionError
        from insightflow_visualization import ChartRenderer

        # A box plot has nothing to draw without raw observations.
        chart = ChartRequest(
            chart_id="chart_empty",
            kind=ChartKind.BOX,
            title="Empty box",
            series=[ChartSeries(name="s", x=["a"], y=[1.0])],
        )
        with pytest.raises(ToolExecutionError, match="raw observations"):
            ChartRenderer(tmp_path).render(chart)