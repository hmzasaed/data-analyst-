"""Annual data: a whole-number `year` column is a valid time axis.

Regression: a `year` column was given the time role from its name, the planner
sent it to the date-only trend tools with a monthly grain, and the plan was
rejected with "column is not a date" - after cleaning had also turned it into
Float64 (2019 -> 2019.0). Resuming could never succeed.
"""

from __future__ import annotations

import polars as pl
import pytest
from insightflow_contracts import JobObjective, JobStage, JobStatus

pytestmark = pytest.mark.integration


@pytest.fixture
def panel_csv(tmp_path):
    """Country x year panel, shaped like World Bank style indicator exports."""
    lines = ["Country,Year,GDP,Population,Exports"]
    for country, base in (("Avalon", 100.0), ("Borduria", 250.0), ("Carpania", 60.0)):
        for offset, year in enumerate(range(1990, 2020)):
            lines.append(
                f"{country},{year},{base * (1.03 ** offset):.2f},"
                f"{1_000_000 + 9_000 * offset},{base * 0.2 + offset:.2f}"
            )
    path = tmp_path / "indicators.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    from insightflow_contracts import reset_settings

    monkeypatch.setenv("INSIGHTFLOW_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv(
        "INSIGHTFLOW_STATE_DATABASE_URL", f"sqlite:///{(tmp_path / 'state.db').as_posix()}"
    )
    monkeypatch.setenv("INSIGHTFLOW_LLM_ENABLED", "false")
    reset_settings()

    from insightflow_orchestration import JobFactory, JobOrchestrator, build_context

    return JobFactory.from_settings(), JobOrchestrator, build_context


def _run(pipeline, source, question):
    factory, orchestrator_cls, build_context = pipeline
    job, record = factory.create(
        source, owner_id="alice", objective=JobObjective(question=question)
    )
    context = build_context(
        job=job, raw_record=record, artifact_store=factory.artifact_store,
        state=factory.state, ingestion=factory.ingestion,
    )
    return orchestrator_cls(context).run(), context


@pytest.mark.parametrize("question", [None, "How did GDP trend over time?"])
def test_a_yearly_panel_completes_with_a_yearly_trend(pipeline, panel_csv, question):
    result, context = _run(pipeline, panel_csv, question)

    assert result.status is JobStatus.COMPLETED, result.error
    assert result.error is None
    trend = [s for s in context.plan.steps if s.tool_name == "time_series_aggregate"]
    assert trend, "the year column should still drive a trend analysis"
    assert trend[0].parameters["date_column"] == "year"
    assert trend[0].parameters["grain"] == "yearly"
    statuses = {r.step_id: r.status for r in context.results}
    assert statuses["time_series"] == "succeeded"
    # The time axis is not analysed as if it were a measure.
    described = next(s for s in context.plan.steps if s.tool_name == "describe_measures")
    assert "year" not in described.parameters["columns"]


def test_a_float_year_column_is_accepted_and_aggregated_yearly(tmp_path):
    """Cleaning's number parsing produces Float64 years; they must still work."""
    from insightflow_analytics import REGISTRY, AnalysisContext
    from insightflow_contracts import DatasetSnapshot

    frame = pl.DataFrame(
        {"year": [2018.0, 2018.0, 2019.0, 2020.0, 2021.0], "gdp": [1.0, 2.0, 3.0, 4.0, 5.0]}
    )
    snapshot = DatasetSnapshot(
        dataset_id="ds", artifact_id="art_ds_v1", version=1, row_count=frame.height, column_count=2,
        columns=frame.columns, source_file_hash="h",
    )
    ctx = AnalysisContext(frame=frame, snapshot=snapshot, artifact_dir=tmp_path)
    parameters = {"date_column": "year", "grain": "monthly", "measures": ["gdp"]}

    REGISTRY.validate_parameters("time_series_aggregate", parameters, context=ctx)
    outcome = REGISTRY.get("time_series_aggregate").handler(ctx, parameters)

    assert outcome.tables[0].height == 4  # one row per year, not per month
    assert outcome.tables[0]["gdp_total"].to_list() == [3.0, 3.0, 4.0, 5.0]
    assert any("aggregated yearly" in warning for warning in outcome.warnings)


def test_a_numeric_month_number_is_not_used_as_a_date(pipeline, tmp_path):
    """`month` holding 1-12 has the time role by name but no calendar position."""
    lines = ["month,region,sales"] + [
        f"{(i % 12) + 1},{'North' if i % 2 else 'South'},{100 + i}" for i in range(48)
    ]
    path = tmp_path / "months.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result, context = _run(pipeline, path, "How did sales trend over time?")

    assert result.status is JobStatus.COMPLETED, result.error
    assert not [s for s in context.plan.steps if "date_column" in s.parameters]
    assert JobStage.EXECUTED in result.completed_stages()
