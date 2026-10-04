"""Agent lifecycle: the human review gate, resuming in a fresh process, cancellation."""

from __future__ import annotations

import pytest
from insightflow_contracts import JobObjective, JobStage, JobStatus

pytestmark = pytest.mark.integration


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


def _fresh_orchestrator(factory, orchestrator_cls, build_context, job_id):
    """What the API and the worker do: reload the job and start from nothing in memory."""
    job = factory.state.load_job(job_id)
    context = build_context(
        job=job, raw_record=factory.raw_record(job), artifact_store=factory.artifact_store,
        state=factory.state, ingestion=factory.ingestion,
    )
    return orchestrator_cls(context), context


def _create(factory, source, **job_fields):
    job, _record = factory.create(
        source, owner_id="alice", objective=JobObjective(question="overview")
    )
    for key, value in job_fields.items():
        setattr(job, key, value)
    factory.state.save_job(job)
    return job


class TestResumeInAFreshProcess:
    def test_a_job_stopped_mid_way_completes_from_a_new_orchestrator(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, sales_csv)
        first, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        first.run(until=JobStage.PLANNED)

        resumed, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        result = resumed.run(resume=True)

        assert result.status is JobStatus.COMPLETED, result.error
        # Rehydrated stages are not re-run; the plan is reused rather than re-planned.
        planned = [r for r in result.stages if r.stage is JobStage.PLANNED and r.status == "succeeded"]
        assert len(planned) == 1

    def test_a_failure_after_execution_recomputes_execution(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, sales_csv)
        first, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)

        def broken() -> dict:
            from insightflow_orchestration.job import JobFailedError

            raise JobFailedError("synthetic verify failure")

        first.verify = broken
        assert first.run().status is JobStatus.FAILED

        resumed, context = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        result = resumed.run(resume=True)
        assert result.status is JobStatus.COMPLETED, result.error
        # Execution outputs live in memory only, so they were recomputed, not trusted.
        executed = [r for r in result.stages if r.stage is JobStage.EXECUTED and r.status == "succeeded"]
        assert len(executed) == 2
        assert context.findings


class TestReviewGate:
    def _paused_job(self, pipeline, source):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, source, require_review=True)
        orchestrator, context = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        result = orchestrator.run()
        return result, context

    def test_the_job_pauses_for_review_without_failing(self, pipeline, messy_csv):
        result, context = self._paused_job(pipeline, messy_csv)
        if not [op for op in context.cleaning_plan.operations if op.review_level.value != "automatic"]:
            pytest.skip("fixture produced no operation that needs review")

        assert result.status is JobStatus.AWAITING_REVIEW
        assert JobStage.CLEANING_REVIEWED not in result.completed_stages()
        assert JobStage.CLEANING_PROPOSED in result.completed_stages()
        assert not [r for r in result.stages if r.status == "failed"]

    def test_accepted_operations_are_applied_after_resume(self, pipeline, messy_csv):
        factory, orchestrator_cls, build_context = pipeline
        result, context = self._paused_job(pipeline, messy_csv)
        reviewable = [
            op for op in context.cleaning_plan.operations if op.review_level.value != "automatic"
        ]
        if not reviewable:
            pytest.skip("fixture produced no operation that needs review")
        automatic = [
            op for op in context.cleaning_plan.operations if op.review_level.value == "automatic"
        ]

        job = factory.state.load_job(result.job_id)
        chosen = reviewable[0].operation_id
        job.cleaning_decisions = {chosen: True}
        if automatic:
            job.cleaning_decisions[automatic[0].operation_id] = False
        job.review_submitted = True
        factory.state.save_job(job)

        resumed, resumed_context = _fresh_orchestrator(
            factory, orchestrator_cls, build_context, job.job_id
        )
        finished = resumed.run(resume=True)
        assert finished.status is JobStatus.COMPLETED, finished.error

        rejected = {op.operation_id for op in resumed_context.cleaning.rejected}
        applied = {entry.operation_id for entry in resumed_context.cleaning.applied}
        # Before this fix, user approvals were dropped and only automatic ops ran.
        assert chosen in applied
        assert chosen not in rejected
        if automatic:
            # An explicitly rejected automatic operation must not run.
            assert automatic[0].operation_id not in applied

    def test_without_require_review_the_job_runs_through(self, pipeline, messy_csv):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, messy_csv)
        orchestrator, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        assert orchestrator.run().status is JobStatus.COMPLETED


class TestCancellation:
    def test_a_cancelled_job_stops_before_the_next_stage(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, sales_csv)
        orchestrator, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)

        original = orchestrator.profile

        def profile_then_cancel() -> dict:
            detail = original()
            factory.cancel(job.job_id)  # arrives from another request mid-stage
            return detail

        orchestrator.profile = profile_then_cancel
        result = orchestrator.run()

        assert result.status is JobStatus.CANCELLED
        assert factory.state.load_job(job.job_id).status is JobStatus.CANCELLED
        assert JobStage.INTENT_CAPTURED not in result.completed_stages()


class TestClaim:
    def test_only_one_claim_wins(self, pipeline, sales_csv):
        factory, _, _ = pipeline
        job = _create(factory, sales_csv)
        assert factory.state.claim_job(job.job_id) is not None
        assert factory.state.claim_job(job.job_id) is None
        assert factory.state.load_job(job.job_id).status is JobStatus.RUNNING


class TestFeatureStagesResume:
    def test_resuming_between_the_feature_stages_reuses_the_recorded_features(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        job = _create(factory, sales_csv)
        first, _ = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        first.run(until=JobStage.FEATURES_ENGINEERED)

        resumed, context = _fresh_orchestrator(factory, orchestrator_cls, build_context, job.job_id)
        result = resumed.run(resume=True)
        assert result.status is JobStatus.COMPLETED, result.error
        engineered = [r for r in result.stages if r.stage is JobStage.FEATURES_ENGINEERED and r.status == "succeeded"]
        assert len(engineered) == 1  # not re-run: specs were re-applied from the record
        assert context.feature_selection is not None and context.feature_selection.selected
