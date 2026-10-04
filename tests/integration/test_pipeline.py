"""End-to-end: upload to report, plus resume, failure handling and the worker."""

from __future__ import annotations

import pytest
from insightflow_contracts import JobObjective, JobStage, JobStatus, ReportSectionKind

pytestmark = pytest.mark.integration


@pytest.fixture
def pipeline(tmp_path, monkeypatch, sales_csv):
    """A complete pipeline wired to temp directories, with the LLM disabled."""
    from insightflow_contracts import reset_settings

    monkeypatch.setenv("INSIGHTFLOW_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv(
        "INSIGHTFLOW_STATE_DATABASE_URL", f"sqlite:///{(tmp_path / 'state.db').as_posix()}"
    )
    monkeypatch.setenv("INSIGHTFLOW_LLM_ENABLED", "false")
    reset_settings()

    from insightflow_orchestration import JobFactory, JobOrchestrator, build_context

    factory = JobFactory.from_settings()
    return factory, JobOrchestrator, build_context


def _run(factory, orchestrator_cls, build_context, source, *, question="", owner="alice"):
    job, record = factory.create(
        source,
        owner_id=owner,
        objective=JobObjective(question=question or None),
    )
    context = build_context(
        job=job, raw_record=record, artifact_store=factory.artifact_store, state=factory.state
    )
    result = orchestrator_cls(context).run()
    return result, context


class TestHappyPath:
    def test_csv_reaches_a_verified_report(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, context = _run(
            factory,
            orchestrator_cls,
            build_context,
            sales_csv,
            question="How did monthly revenue trend, and which region is unusual?",
        )

        assert job.status is JobStatus.COMPLETED
        stages = [record.stage for record in job.stages if record.status == "succeeded"]
        assert stages == [
            JobStage.INGESTED,
            JobStage.PROFILED,
            JobStage.INTENT_CAPTURED,
            JobStage.CLEANING_PROPOSED,
            JobStage.CLEANING_REVIEWED,
            JobStage.CLEANING_APPLIED,
            JobStage.PREPROCESSED,
            JobStage.FEATURES_ENGINEERED,
            JobStage.FEATURES_SELECTED,
            JobStage.PLANNED,
            JobStage.EXECUTED,
            JobStage.VERIFIED,
            JobStage.REPORTED,
        ]

        document = context.bundle.document
        assert document.sections
        assert document.finding_ids
        assert context.chart_refs
        assert context.table_refs

    def test_every_report_finding_is_verified(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="overview")
        assert job.status is JobStatus.COMPLETED
        document = context.bundle.document
        published = {finding.finding_id for finding in context.findings if finding.is_publishable}
        assert set(document.finding_ids) <= published

    def test_report_has_no_unsupported_language(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="trend")
        text = context.bundle.to_markdown().lower()
        for phrase in ("caused by", "because of", "fraud", "proves that"):
            assert phrase not in text

    def test_anomaly_section_carries_its_caveat(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv,
            question="Find unusual values in revenue.",
        )
        anomalies = context.bundle.document.section(ReportSectionKind.ANOMALIES)
        assert anomalies is not None
        assert "not evidence of an error" in anomalies.body_markdown.lower()

    def test_charts_are_written_as_artifacts(self, pipeline, sales_csv):
        from pathlib import Path

        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")
        assert context.chart_refs
        for chart in context.chart_refs:
            assert Path(chart.path).exists()
            assert chart.byte_size > 1000

    def test_exports_are_written(self, pipeline, sales_csv):
        from pathlib import Path

        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv)
        written = context.bundle.write(context.artifact_dir / "again")
        assert set(written) == {"html", "markdown", "json"}
        for path in written.values():
            assert Path(path).exists()
            assert Path(path).stat().st_size > 500

    def test_the_planted_outlier_is_detected(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv,
            question="Find unusual transactions.",
        )
        assert context.anomaly_results
        assert any(result.flagged_count > 0 for result in context.anomaly_results)

    def test_audit_trail_records_every_stage(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, _ = _run(factory, orchestrator_cls, build_context, sales_csv)
        events = factory.state.audit_trail(job.job_id)
        actions = {event["action"] for event in events}
        assert "upload" in actions
        assert "stage.ingested.succeeded" in actions
        assert "stage.reported.succeeded" in actions
        # A stage that never ran must not appear as successful.
        assert "stage.verified.failed" not in actions

    def test_versions_are_recorded_for_reproducibility(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, _ = _run(factory, orchestrator_cls, build_context, sales_csv)
        assert job.versions["ingestion"]
        assert job.versions["planner"]
        assert job.versions["verifier"]
        assert job.versions["tools"]
        assert job.profile_digest
        assert job.plan_digest


class TestNoQuestion:
    def test_exploratory_run_is_labelled(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="")
        assert context.job.objective.is_exploratory
        assert context.bundle.document.is_exploratory
        assert "exploratory" in context.bundle.to_html().lower()


class TestMismatchedData:
    def test_messy_file_is_cleaned_and_reported(self, pipeline, messy_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, context = _run(factory, orchestrator_cls, build_context, messy_csv, question="summary")
        assert job.status is JobStatus.COMPLETED
        assert context.cleaning is not None
        assert context.cleaning.applied
        assert context.snapshot.version == 2
        quality = context.bundle.document.section(ReportSectionKind.DATA_QUALITY)
        assert quality is not None
        assert quality.body_markdown

    def test_missing_values_are_visible_in_the_report(self, pipeline, messy_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, messy_csv)
        text = context.bundle.to_markdown().lower()
        assert "missing" in text or "data-quality" in text


class TestFailureHandling:
    def test_unsupported_input_fails_at_upload(self, pipeline, tmp_path):
        factory, _, _ = pipeline
        path = tmp_path / "archive.zip"
        path.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        from insightflow_contracts import UnsupportedInputError

        with pytest.raises(UnsupportedInputError):
            factory.create(path, owner_id="alice")

    def test_a_failing_stage_stops_the_job_but_keeps_prior_work(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, record = factory.create(
            sales_csv, owner_id="alice", objective=JobObjective(question="overview")
        )
        context = build_context(
            job=job, raw_record=record, artifact_store=factory.artifact_store, state=factory.state
        )

        def explode() -> dict:
            raise RuntimeError("synthetic failure")

        orchestrator = orchestrator_cls(context)
        orchestrator.plan_analysis = explode
        result = orchestrator.run()

        assert result.status is JobStatus.FAILED
        assert "synthetic failure" in (result.error or "")
        # Everything before the failure is still recorded.
        assert JobStage.PROFILED in result.completed_stages()
        assert result.stage_record(JobStage.PROFILED) is not None

    def test_a_failed_job_can_resume(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, record = factory.create(
            sales_csv, owner_id="alice", objective=JobObjective(question="overview")
        )
        context = build_context(
            job=job, raw_record=record, artifact_store=factory.artifact_store, state=factory.state
        )
        orchestrator = orchestrator_cls(context)

        original = orchestrator.plan_analysis
        calls = {"n": 0}

        def flaky() -> dict:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return original()

        orchestrator.plan_analysis = flaky
        assert orchestrator.run().status is JobStatus.FAILED

        ingested_before = [
            r for r in context.job.stages
            if r.stage is JobStage.INGESTED and r.status == "succeeded"
        ]
        assert len(ingested_before) == 1

        resumed = orchestrator_cls(context).resume()
        assert resumed.status is JobStatus.COMPLETED

        # Stages that already succeeded are not repeated on resume.
        ingested_after = [
            r for r in resumed.stages
            if r.stage is JobStage.INGESTED and r.status == "succeeded"
        ]
        assert len(ingested_after) == 1
        # The failed stage is retried and now succeeds.
        planned = [r for r in resumed.stages if r.stage is JobStage.PLANNED]
        assert [r.status for r in planned] == ["failed", "succeeded"]

    def test_a_rejected_plan_is_reported_not_executed(self, pipeline, sales_csv):
        from insightflow_contracts import (
            AnalysisPlan,
            AnalysisType,
            PlannedStep,
            PlanRejectedError,
        )

        factory, orchestrator_cls, build_context = pipeline
        job, record = factory.create(
            sales_csv, owner_id="alice", objective=JobObjective(question="overview")
        )
        context = build_context(
            job=job, raw_record=record, artifact_store=factory.artifact_store, state=factory.state
        )
        # Run the real stages up to planning so the frame and profile are populated.
        orchestrator_cls(context).run(until=JobStage.PLANNED)

        context.plan = AnalysisPlan(
            objective="x",
            steps=[
                PlannedStep(
                    step_id="s", analysis_type=AnalysisType.DESCRIPTIVE,
                    tool_name="invent_a_method", parameters={},
                )
            ],
        )
        with pytest.raises(PlanRejectedError) as excinfo:
            orchestrator_cls(context).execute_plan()
        assert any("not registered" in v for v in excinfo.value.violations)


class TestWorker:
    def test_worker_claims_and_runs_a_pending_job(self, pipeline, sales_csv):
        from insightflow_worker import Worker

        factory, _, _ = pipeline
        job, _ = factory.create(
            sales_csv, owner_id="alice", objective=JobObjective(question="overview")
        )
        worker = Worker()
        assert worker.run_once() == 1
        finished = factory.state.load_job(job.job_id)
        assert finished.status is JobStatus.COMPLETED

    def test_claiming_is_exclusive(self, pipeline, sales_csv):
        from insightflow_worker import Worker

        factory, _, _ = pipeline
        factory.create(sales_csv, owner_id="alice", objective=JobObjective(question="overview"))
        first, second = Worker(), Worker()
        claimed = first.claim()
        assert len(claimed) == 1
        assert second.claim() == []


class TestTenantIsolation:
    def test_a_second_tenant_cannot_read_the_first_ones_artifacts(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job, _ = _run(factory, orchestrator_cls, build_context, sales_csv, owner="alice")
        assert factory.state.load_job(job.job_id).owner_id == "alice"
        assert factory.state.list_jobs("bob") == []