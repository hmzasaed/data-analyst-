"""The resumable job state machine.

Stages run in a fixed order. Each stage:

- is idempotent, so a retry after a crash re-runs cleanly;
- records a `StageRecord` with its outcome, timing and error;
- writes an audit row naming what was done and by which actor;
- returns a typed payload the next stage consumes.

A failure never destroys prior work: the job stops, the completed stages stay
recorded, and `resume()` picks up at the stage that failed.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import polars as pl
from insightflow_agents import (
    AnalysisPlanner,
    ComposerInput,
    EngineeringOutcome,
    FeatureEngineeringAgent,
    FeatureSelectionAgent,
    FindingFactory,
    ReportComposer,
    VerificationAgent,
    VerificationInput,
    mark_status,
)
from insightflow_analytics import REGISTRY, PlanValidator, execute_plan
from insightflow_analytics.context import AnalysisContext
from insightflow_anomaly import AnomalyDetector, DetectionRequest
from insightflow_cleaning import apply_cleaning, next_snapshot, plan_cleaning
from insightflow_contracts import (
    AnalysisPlan,
    AnomalyResult,
    ArtifactKind,
    ArtifactRef,
    ChartRequest,
    CleaningExecution,
    CleaningPlan,
    DatasetProfile,
    DatasetSnapshot,
    ExecutionResult,
    FeatureEngineeringResult,
    FeatureSelectionResult,
    Finding,
    InsightFlowError,
    Job,
    JobStage,
    JobStatus,
    PreprocessingResult,
    RawFileRecord,
    StageRecord,
    VerificationResult,
    content_digest,
    get_settings,
    utc_now,
)
from insightflow_features import apply_features, output_columns, render_feature_charts
from insightflow_ingestion import IngestionService
from insightflow_preprocessing import build_model_ready, preprocess_for_analysis
from insightflow_profiling import profile_dataset
from insightflow_reporting import ReportBundle, build_bundle, embed_charts
from insightflow_storage import ArtifactStore, AuditEvent, StateStore
from insightflow_visualization import render_charts
from insightflow_visualization.eda import run_eda

from insightflow_orchestration.persistence import save_stage_outputs

#: Failure codes worth one more attempt. Everything else fails fast.
RETRYABLE_CODES = frozenset({"tool_execution_error", "sandbox_timeout"})

MAX_STAGE_ATTEMPTS = 3


#: Stages whose outputs `_rehydrate` can rebuild from the stored original and the
#: persisted stage outputs. Later stages hold in-memory results (charts, tables,
#: execution results) that are cheaper and safer to recompute than to reload.
REHYDRATABLE_STAGES = frozenset(
    {
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
    }
)


class JobFailedError(InsightFlowError):
    """A stage failed in a way the job cannot recover from."""

    code = "job_failed"


class StagePausedError(InsightFlowError):
    """A stage needs a human decision before the job can continue. Not a failure."""

    code = "awaiting_review"


@dataclass
class JobContext:
    """Everything the stages share for one job."""

    job: Job
    raw_record: RawFileRecord
    artifact_store: ArtifactStore
    state: StateStore
    artifact_dir: Path
    ingestion: IngestionService
    registry: Any = REGISTRY

    profile: DatasetProfile | None = None
    snapshot: DatasetSnapshot | None = None
    frame: pl.DataFrame | None = None
    cleaning_plan: CleaningPlan | None = None
    cleaning: CleaningExecution | None = None
    plan: AnalysisPlan | None = None
    plan_source: str = ""
    results: list[ExecutionResult] = field(default_factory=list)
    chart_requests: list[ChartRequest] = field(default_factory=list)
    chart_refs: list[ArtifactRef] = field(default_factory=list)
    table_refs: list[ArtifactRef] = field(default_factory=list)
    anomaly_results: list[AnomalyResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    verification: VerificationResult | None = None
    bundle: ReportBundle | None = None
    preprocessing: PreprocessingResult | None = None
    feature_engineering: EngineeringOutcome | None = None
    feature_selection: FeatureSelectionResult | None = None
    #: (artifact, title, caption, wide) for the feature section of the report.
    feature_charts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def analysis_context(self, step_id: str = "plan") -> AnalysisContext:
        if self.frame is None or self.snapshot is None:  # pragma: no cover - stage ordering
            raise JobFailedError("analysis attempted before ingestion", retryable=False)
        return AnalysisContext(
            frame=self.frame,
            snapshot=self.snapshot,
            profile=self.profile,
            artifact_dir=self.artifact_dir,
            step_id=step_id,
            owner_id=self.job.owner_id,
        )

    def metrics(self):
        return [metric for result in self.results for metric in result.metrics]

    def load_tables(self) -> dict[str, pl.DataFrame]:
        tables: dict[str, pl.DataFrame] = {}
        for reference in self.table_refs:
            try:
                tables[reference.artifact_id] = pl.read_csv(reference.path)
            except Exception:  # pragma: no cover - a missing table is not fatal
                continue
        return tables


class JobOrchestrator:
    """Runs a job through its stages, with retries and an audit trail."""

    version = "1"

    def __init__(self, context: JobContext) -> None:
        self.ctx = context

    # ------------------------------------------------------------------ drive
    def run(self, *, until: JobStage | None = None, resume: bool = True) -> Job:
        """Run stages in order, stopping at `until` or when the job completes."""
        stages: dict[JobStage, Callable[[], Any]] = {
            JobStage.INGESTED: self.ingest,
            JobStage.PROFILED: self.profile,
            JobStage.INTENT_CAPTURED: self.capture_intent,
            JobStage.CLEANING_PROPOSED: self.propose_cleaning,
            JobStage.CLEANING_REVIEWED: self.review_cleaning,
            JobStage.CLEANING_APPLIED: self.apply_cleaning,
            JobStage.PREPROCESSED: self.preprocess,
            JobStage.FEATURES_ENGINEERED: self.engineer_features,
            JobStage.FEATURES_SELECTED: self.select_features,
            JobStage.PLANNED: self.plan_analysis,
            JobStage.EXECUTED: self.execute_plan,
            JobStage.VERIFIED: self.verify,
            JobStage.REPORTED: self.report,
        }

        # A fresh process resuming a job has none of the in-memory stage outputs, so
        # rebuild them; anything that cannot be rebuilt is recomputed instead.
        stale = self._rehydrate() if resume else set()

        already_done = {done.value for done in self.ctx.job.completed_stages()}
        for stage, handler in stages.items():
            if self._cancelled():
                self._audit("job.cancelled", "ok", stage=stage.value)
                break
            if stage in stale:
                self._run_stage(stage, handler)
                if self.ctx.job.status in {JobStatus.FAILED, JobStatus.AWAITING_REVIEW}:
                    break
                continue
            if until is not None and stage != until and stage.value in already_done:
                continue
            if resume and stage in self.ctx.job.completed_stages():
                self._audit(f"stage.{stage.value}.skipped", "ok", stage=stage.value)
                continue
            self._run_stage(stage, handler)
            if self.ctx.job.status in {JobStatus.FAILED, JobStatus.AWAITING_REVIEW}:
                break
            if until is not None and stage == until:
                break
        return self.ctx.job

    def resume(self, until: JobStage | None = None) -> Job:
        return self.run(until=until, resume=True)

    def _run_stage(self, stage: JobStage, handler: Callable[[], Any]) -> None:
        attempts = self._attempts_for(stage)
        for attempt in range(1, attempts + 1):
            started = utc_now()
            self.ctx.job.current_stage = stage
            self.ctx.job.status = JobStatus.RUNNING
            self._persist()
            self._audit(f"stage.{stage.value}.started", "ok", stage=stage.value,
                        detail={"attempt": attempt})
            try:
                detail = handler() or {}
            except StagePausedError as exc:
                self.ctx.job.status = JobStatus.AWAITING_REVIEW
                self._persist()
                self._audit(f"stage.{stage.value}.paused", "pending_review", stage=stage.value,
                            detail={"reason": exc.message})
                return
            except InsightFlowError as exc:
                self._fail_stage(stage, exc, attempt, started)
                if exc.code in RETRYABLE_CODES and attempt < attempts:
                    self._audit(f"stage.{stage.value}.retrying", "warn", stage=stage.value,
                                detail={"attempt": attempt, "code": exc.code})
                    continue
                return
            except Exception as exc:  # pragma: no cover - defensive
                self._fail_stage(
                    stage,
                    JobFailedError(f"{type(exc).__name__}: {exc}", retryable=False),
                    attempt,
                    started,
                )
                return

            elapsed = (utc_now() - started).total_seconds()
            record = StageRecord(
                stage=stage,
                status="succeeded",
                started_at=started,
                finished_at=utc_now(),
                attempt=attempt,
                duration_seconds=round(elapsed, 4),
                detail=detail if isinstance(detail, dict) else {},
            )
            self.ctx.job.stages.append(record)
            self.ctx.state.append_stage(self.ctx.job.job_id, record)
            self._persist()
            self._audit(f"stage.{stage.value}.succeeded", "ok", stage=stage.value,
                        detail=record.detail)
            return

    def _fail_stage(self, stage: JobStage, error: InsightFlowError, attempt: int, started) -> None:
        record = StageRecord(
            stage=stage,
            status="failed",
            started_at=started,
            finished_at=utc_now(),
            attempt=attempt,
            duration_seconds=round((utc_now() - started).total_seconds(), 4),
            error=error.message,
            error_code=error.code,
            detail=error.detail,
        )
        self.ctx.job.stages.append(record)
        self.ctx.state.append_stage(self.ctx.job.job_id, record)
        self.ctx.job.status = JobStatus.FAILED
        self.ctx.job.error = f"[{error.code}] {error.message}"
        self._persist()
        self._audit(f"stage.{stage.value}.failed", "error", stage=stage.value,
                    detail={"code": error.code, "attempt": attempt, "error": error.message})

    def _attempts_for(self, stage: JobStage) -> int:
        previous = len([s for s in self.ctx.job.stages if s.stage is stage and s.status == "failed"])
        return min(MAX_STAGE_ATTEMPTS, previous + 1)

    # ----------------------------------------------------------------- stages
    def ingest(self) -> dict:
        service = self.ctx.ingestion
        outcome = service.ingest(
            self.ctx.raw_record,
            sheet_name=self.ctx.job.source_sheet,
        )
        self.ctx.frame = outcome.table.frame
        self.ctx.snapshot = outcome.snapshot
        self.ctx.job.dataset_id = outcome.snapshot.dataset_id
        self.ctx.job.dataset_version = outcome.snapshot.version
        self.ctx.job.source_sheet = outcome.read.sheet_name
        self.ctx.job.available_sheets = outcome.read.available_sheets
        self._adopt(outcome.raw_artifact)
        return {
            "rows": outcome.snapshot.row_count,
            "columns": outcome.snapshot.column_count,
            "sheet": outcome.read.sheet_name,
            "seconds": round(outcome.elapsed_seconds, 3),
            "notes": outcome.read.notes[:5],
        }

    def profile(self) -> dict:
        profile = profile_dataset(self.ctx.frame, self.ctx.snapshot)
        self.ctx.profile = profile
        self.ctx.job.profile_digest = content_digest(
            {"columns": [c.name for c in profile.columns], "rows": profile.row_count}
        )
        highest = profile.highest_pii_risk().value
        self.ctx.warnings.extend(warning.message for warning in profile.warnings)
        self._persist_output(profile=profile)
        return {
            "columns": profile.column_count,
            "quality_issues": len(profile.quality_issues),
            "duplicate_rows": profile.duplicate_row_count,
            "highest_pii_risk": highest,
            "types": sorted({c.inferred_type.value for c in profile.columns}),
        }

    def capture_intent(self) -> dict:
        objective = self.ctx.job.objective
        self.ctx.job.objective = objective.model_copy(
            update={"is_exploratory": not bool(objective.question)}
        )
        return {
            "question": objective.question,
            "is_exploratory": self.ctx.job.objective.is_exploratory,
            "context_sent_to_model": self._consented(),
        }

    def propose_cleaning(self) -> dict:
        plan = plan_cleaning(self.ctx.profile)
        self.ctx.cleaning_plan = plan
        self._persist_output(cleaning_plan=plan)
        return {
            "operations": len(plan.operations),
            "automatic": len([o for o in plan.operations if o.review_level.value == "automatic"]),
            "needs_review": len(
                [o for o in plan.operations if o.review_level.value != "automatic"]
            ),
        }

    def review_cleaning(self) -> dict:
        plan = self.ctx.cleaning_plan
        if plan is None:
            return {"skipped": "no cleaning plan"}
        self._apply_decisions(plan)
        summary = plan.summary()
        requires_review = [
            op.operation_id for op in plan.for_review() if op.review_level.value != "automatic"
        ]
        if self.ctx.job.require_review and requires_review and not self.ctx.job.review_submitted:
            # A human gate, not a failure: the stage is left incomplete so that
            # resuming after the decision re-enters it and records the outcome.
            raise StagePausedError(
                f"{len(requires_review)} cleaning operation(s) are waiting for a decision",
                retryable=False,
            )
        self._audit(
            "cleaning.review",
            "pending_review" if requires_review else "ok",
            stage=JobStage.CLEANING_REVIEWED.value,
            detail={
                "requires_review": len(requires_review),
                "user_reviewed": self.ctx.job.review_submitted,
            },
        )
        return {
            "awaiting_user_review": requires_review,
            "user_reviewed": self.ctx.job.review_submitted,
            "accepted": sorted(k for k, v in self.ctx.job.cleaning_decisions.items() if v),
            "rejected": sorted(k for k, v in self.ctx.job.cleaning_decisions.items() if not v),
            "summary": summary.model_dump(mode="json"),
        }

    def _apply_decisions(self, plan: CleaningPlan) -> None:
        """Copy the user's recorded decisions onto the plan's operations."""
        decisions = self.ctx.job.cleaning_decisions
        for operation in plan.operations:
            if operation.operation_id in decisions:
                operation.accepted = decisions[operation.operation_id]

    def apply_cleaning(self) -> dict:
        plan = self.ctx.cleaning_plan
        if plan is None:
            return {"skipped": "no cleaning plan"}
        self._apply_decisions(plan)
        # Automatic operations the user did not reject, plus anything they accepted.
        approved_ids = {op.operation_id for op in plan.approved()}
        frame, execution = apply_cleaning(
            self.ctx.frame, plan, self.ctx.snapshot, approved_operation_ids=approved_ids
        )
        self.ctx.frame = frame
        self.ctx.cleaning = execution
        self.ctx.snapshot = next_snapshot(self.ctx.snapshot, execution)
        self.ctx.job.dataset_version = self.ctx.snapshot.version
        self.ctx.job.versions["cleaning_digest"] = execution.transform_digest or ""
        self._persist_output(cleaning_execution=execution)
        return {
            "applied": len(execution.applied),
            "rejected": len(execution.rejected),
            "rows_touched": execution.total_rows_affected,
            "dataset_version": self.ctx.snapshot.version,
        }

    def preprocess(self) -> dict:
        """Prepare the analysed data (opt-in changes) and the model-ready export."""
        options = self.ctx.job.preprocessing
        frame = self.ctx.frame
        rows_before, columns_before = frame.height, frame.width
        missing_before = _missing_counts(frame)

        frame, steps, warnings = self._preprocess_frame(frame)
        missing_after = _missing_counts(frame)

        export_frame, export, export_steps = build_model_ready(frame, self.ctx.profile, options)
        self.ctx.artifact_store.write_text(
            self.ctx.job.owner_id, self.ctx.job.job_id, ArtifactKind.DATASET_CSV,
            export.artifact_id, export_frame.to_csv(index=False, lineterminator="\n"),
        )

        result = PreprocessingResult(
            options=options,
            analysis_changed=bool(steps),
            rows_before=rows_before,
            rows_after=frame.height,
            columns_before=columns_before,
            columns_after=frame.width,
            steps=[*steps, *export_steps],
            missing_before=missing_before,
            missing_after=missing_after,
            export=export,
            warnings=warnings,
        )
        self.ctx.preprocessing = result
        self.ctx.warnings.extend(warnings)
        self._persist_output(preprocessing=result)
        if frame.height == 0:
            raise JobFailedError(
                "preprocessing left no rows to analyse", retryable=False,
                suggestion="Choose a different missing-value strategy than dropping rows.",
            )
        return {
            "analysis_changed": result.analysis_changed,
            "rows_before": rows_before,
            "rows_after": frame.height,
            "steps": len(result.steps),
            "export_columns": export.columns,
            "dataset_version": self.ctx.snapshot.version,
        }

    def _preprocess_frame(self, frame: pl.DataFrame):
        """Apply analysis-scope preprocessing and advance the dataset version if it changed."""
        frame, steps, warnings = preprocess_for_analysis(
            frame, self.ctx.profile, self.ctx.job.preprocessing
        )
        self.ctx.frame = frame
        if steps:
            snapshot = self.ctx.snapshot
            self.ctx.snapshot = snapshot.model_copy(
                update={
                    "version": snapshot.version + 1,
                    "artifact_id": f"{snapshot.artifact_id}_pre",
                    "row_count": frame.height,
                    "created_from": "preprocessing",
                    "transform_digest": content_digest([step.model_dump(mode="json") for step in steps]),
                }
            )
            self.ctx.job.dataset_version = self.ctx.snapshot.version
        return frame, steps, warnings

    def engineer_features(self) -> dict:
        """Agent: choose the target and propose, validate and compute new features.

        Works on a copy: engineered columns go to the feature set, never into the
        data the analysis tools read.
        """
        agent = FeatureEngineeringAgent(allow_model=self._consented())
        outcome = agent.run(
            self.ctx.frame, self.ctx.profile, self.ctx.job.features,
            question=self.ctx.job.objective.question,
        )
        self.ctx.feature_engineering = outcome
        result = outcome.result
        if result.model_used:
            self.ctx.job.versions["feature_model"] = result.model_used
        self.ctx.warnings.extend(result.warnings)
        self._persist_output(feature_engineering=result)
        return {
            "skipped": bool(result.skipped_reason),
            "target": result.target_column,
            "task": result.task,
            "source": result.source,
            "created": len(result.created),
            "rejected": len(result.rejected),
        }

    def select_features(self) -> dict:
        """Agent: rank candidates, drop weak and redundant ones, cross-validate."""
        engineering = self.ctx.feature_engineering
        if engineering is None:  # pragma: no cover - stage ordering
            raise JobFailedError("feature selection ran before feature engineering", retryable=False)
        agent = FeatureSelectionAgent(allow_model=self._consented())
        selection, feature_set = agent.run(
            engineering, self.ctx.profile, self.ctx.job.features, self.ctx.job.preprocessing
        )
        owner, job_id = self.ctx.job.owner_id, self.ctx.job.job_id
        if feature_set is not None:
            selection.artifact_id = "feature_set"
            self.ctx.artifact_store.write_text(
                owner, job_id, ArtifactKind.DATASET_CSV, "feature_set",
                feature_set.to_csv(index=False, lineterminator="\n"),
            )
            scores = pd.DataFrame([score.model_dump() for score in selection.scores])
            reference = self.ctx.artifact_store.write_text(
                owner, job_id, ArtifactKind.TABLE_CSV, "feature_scores",
                scores.to_csv(index=False, lineterminator="\n"),
            )
            self.ctx.table_refs.append(reference.model_copy(update={"row_count": len(scores)}))
            charts = render_feature_charts(
                selection.scores, selection.cross_validation, selection.target_column or "",
                self.ctx.artifact_dir / "features",
            )
            self.ctx.feature_charts = []
            for chart in charts:
                adopted = self._adopt(chart.artifact)
                self.ctx.chart_refs.append(adopted)
                self.ctx.feature_charts.append(
                    {"artifact_id": adopted.artifact_id, "title": chart.title,
                     "caption": chart.caption, "wide": chart.wide}
                )
        self.ctx.feature_selection = selection
        self.ctx.warnings.extend(selection.warnings)
        self._persist_output(feature_selection=selection, feature_charts=self.ctx.feature_charts or None)
        cv = selection.cross_validation
        return {
            "skipped": bool(selection.skipped_reason),
            "candidates": selection.candidate_count,
            "selected": len(selection.selected),
            "metric": cv.metric if cv else None,
            "cv_selected": cv.selected_mean if cv else None,
            "cv_all": cv.all_features_mean if cv else None,
        }

    def plan_analysis(self) -> dict:
        ctx = self.ctx.analysis_context()
        planner = AnalysisPlanner(self.ctx.registry)
        outcome = planner.plan(self.ctx.profile, self.ctx.job.objective, ctx)
        self.ctx.plan = outcome.plan
        self.ctx.plan_source = outcome.source
        self.ctx.job.plan_digest = outcome.plan.digests()
        self.ctx.job.versions["planner"] = outcome.plan.planner_version
        if outcome.model_used:
            self.ctx.job.versions["planner_model"] = outcome.model_used
        self.ctx.job.versions["plan_source"] = outcome.source

        if outcome.rejected_steps:
            self.ctx.warnings.append(
                f"{len(outcome.rejected_steps)} proposed step(s) were rejected before execution."
            )
        if outcome.plan.not_supported_questions:
            self.ctx.warnings.extend(outcome.plan.not_supported_questions[:3])
        self._persist_output(analysis_plan=self.ctx.plan)
        return {
            "source": outcome.source,
            "steps": len(outcome.plan.steps),
            "rejected_steps": outcome.rejected_steps[:5],
            "digest": self.ctx.job.plan_digest,
        }

    def execute_plan(self) -> dict:
        plan = self.ctx.plan
        ctx = self.ctx.analysis_context()
        # Parameters are validated here as well as at plan time, because the frame
        # may have changed type since the plan was written.
        PlanValidator(self.ctx.registry).validate(plan, ctx)

        artifacts = execute_plan(
            plan, self.ctx.registry, self.ctx.frame, self.ctx.snapshot, self.ctx.artifact_dir,
            owner_id=self.ctx.job.owner_id,
        )
        self.ctx.results = artifacts.results
        self.ctx.chart_requests = artifacts.charts
        for reference in artifacts.tables:
            self.ctx.table_refs.append(self._adopt(reference))
        self.ctx.warnings.extend(artifacts.warnings)
        if artifacts.failed_steps():
            self.ctx.warnings.append(
                f"{len(artifacts.failed_steps())} analysis step(s) did not complete."
            )

        chart_refs, render_warnings = render_charts(artifacts.charts, self.ctx.artifact_dir / "charts")
        self.ctx.chart_refs = [self._adopt(reference) for reference in chart_refs]
        self.ctx.warnings.extend(render_warnings)

        self.ctx.anomaly_results = self._run_anomaly_scans()
        self.ctx.job.versions["tools"] = ",".join(sorted({r.tool_name for r in artifacts.results}))
        self._persist_output(
            execution_results=self.ctx.results,
            anomaly_results=self.ctx.anomaly_results,
        )
        return {
            "succeeded": len(artifacts.succeeded_steps()),
            "failed": artifacts.failed_steps(),
            "charts": len(self.ctx.chart_refs),
            "tables": len(self.ctx.table_refs),
            "anomaly_scans": len(self.ctx.anomaly_results),
        }

    def _run_anomaly_scans(self) -> list[AnomalyResult]:
        """Re-derive `AnomalyResult` objects from the steps that reported them."""
        results: list[AnomalyResult] = []
        detector = AnomalyDetector()
        for step in self.ctx.plan.steps if self.ctx.plan else []:
            if step.analysis_type.value != "anomaly":
                continue
            execution = next(
                (r for r in self.ctx.results if r.step_id == step.step_id), None
            )
            if execution is None or execution.status != "succeeded":
                continue
            request = self._request_from(step)
            if request is None:
                continue
            try:
                results.append(detector.detect(self.ctx.frame, self.ctx.snapshot, request))
            except InsightFlowError as exc:
                self.ctx.warnings.append(f"anomaly step '{step.step_id}': {exc.message}")
        return results

    def _request_from(self, step) -> DetectionRequest | None:
        parameters = step.parameters
        try:
            if step.tool_name == "detect_group_outliers":
                return DetectionRequest(
                    column=parameters["column"],
                    method=None,
                    group_columns=tuple(parameters["group_by"]),
                    max_anomalies=int(parameters.get("max_anomalies", 500)),
                    analysis_step_id=step.step_id,
                )
            if step.tool_name == "detect_multivariate_outliers":
                return DetectionRequest(
                    column=parameters["features"][0],
                    features=tuple(parameters["features"]),
                    max_anomalies=int(parameters.get("max_anomalies", 500)),
                    analysis_step_id=step.step_id,
                )
            return DetectionRequest(
                column=parameters["column"],
                method=None,
                threshold=parameters.get("threshold"),
                max_anomalies=int(parameters.get("max_anomalies", 500)),
                analysis_step_id=step.step_id,
                extra=dict(parameters.get("method_options") or {}),
            )
        except (KeyError, IndexError, TypeError):
            return None

    def verify(self) -> dict:
        factory = FindingFactory(dataset_version=self.ctx.snapshot.version)
        findings = factory.from_execution(self.ctx.results)
        findings.extend(factory.from_anomalies(self.ctx.anomaly_results))
        findings.extend(factory.from_failures(self.ctx.results))

        agent = VerificationAgent()
        result = agent.verify(
            VerificationInput(
                findings=findings,
                metrics=self.ctx.metrics(),
                tables=self.ctx.load_tables(),
                charts=self.ctx.chart_requests,
                anomalies=self.ctx.anomaly_results,
                execution_results=self.ctx.results,
                dataset_version=self.ctx.snapshot.version,
            )
        )
        self.ctx.findings = mark_status(result, findings)
        self.ctx.verification = result
        self.ctx.job.versions["verifier"] = result.verifier_version
        if result.failed_checks:
            self.ctx.warnings.append(
                f"{len(result.failed_checks)} verification check(s) failed; the affected "
                "findings were excluded from the report."
            )
        self._persist_output(
            findings=self.ctx.findings,
            verification=result,
            warnings=self.ctx.warnings,
        )
        return {
            "verified": len(result.verified_finding_ids),
            "rejected": len(result.rejected_finding_ids),
            "checks": len(result.checks),
            "failures": len(result.failed_checks),
        }

    def report(self) -> dict:
        eda = run_eda(self.ctx.frame, self.ctx.profile, self.ctx.artifact_dir / "eda")
        known = {c.artifact_id for c in self.ctx.chart_refs}
        for chart in self.ctx.feature_charts:
            if chart["artifact_id"] not in known:
                path = self.ctx.artifact_store.path_for(
                    self.ctx.job.owner_id, self.ctx.job.job_id, ArtifactKind.CHART_PNG, chart["artifact_id"]
                )
                if path.exists():
                    self.ctx.chart_refs.append(ArtifactRef(
                        artifact_id=chart["artifact_id"], kind=ArtifactKind.CHART_PNG, path=str(path),
                        byte_size=path.stat().st_size, mime_type="image/png",
                    ))
        # Adopt the exploratory artifacts so they are served, embedded and purged
        # exactly like the analysis tools' own charts and tables.
        for chart in eda.charts:
            chart.artifact = self._adopt(chart.artifact)
            if chart.artifact.artifact_id not in {c.artifact_id for c in self.ctx.chart_refs}:
                self.ctx.chart_refs.append(chart.artifact)
        for table in eda.tables:
            table.artifact = self._adopt(table.artifact)
            if table.artifact.artifact_id not in {t.artifact_id for t in self.ctx.table_refs}:
                self.ctx.table_refs.append(table.artifact)
        self.ctx.warnings.extend(eda.warnings)

        document = ReportComposer().compose(
            ComposerInput(
                job_id=self.ctx.job.job_id,
                objective=self.ctx.job.objective.question or "",
                profile=self.ctx.profile,
                findings=self.ctx.findings,
                verification=self.ctx.verification,
                metrics=self.ctx.metrics(),
                charts=self.ctx.chart_refs,
                tables=self.ctx.table_refs,
                anomalies=self.ctx.anomaly_results,
                cleaning=self.ctx.cleaning,
                execution_results=self.ctx.results,
                not_supported=self.ctx.plan.not_supported_questions if self.ctx.plan else [],
                source_filename=self.ctx.job.source_filename,
                is_exploratory=self.ctx.job.objective.is_exploratory,
                eda=eda,
                preprocessing=self.ctx.preprocessing,
                chart_requests=self.ctx.chart_requests,
                feature_engineering=(
                    self.ctx.feature_engineering.result if self.ctx.feature_engineering else None
                ),
                feature_selection=self.ctx.feature_selection,
                feature_charts=self.ctx.feature_charts,
            )
        )
        bundle = build_bundle(
            document,
            findings=self.ctx.findings,
            verification=self.ctx.verification,
            charts=self.ctx.chart_refs,
            tables=self.ctx.table_refs,
        )
        self.ctx.bundle = bundle
        written = bundle.write(self.ctx.artifact_dir / "report")

        embedded = embed_charts(self.ctx.chart_refs)
        self._store_text(ArtifactKind.REPORT_HTML, "report_html", bundle.to_html(embedded=embedded))

        self.ctx.job.status = JobStatus.COMPLETED
        # An error from an earlier, since-resumed attempt no longer describes the job.
        self.ctx.job.error = None
        self.ctx.job.versions["report"] = document.report_version
        self._persist_output(report_document=document)
        return {
            "sections": len(document.sections),
            "findings": len(document.finding_ids),
            "exports": {name: path.name for name, path in written.items()},
        }

    # -------------------------------------------------------------- utilities
    def _consented(self) -> bool:
        """Whether dataset context may be sent to an external model."""
        settings = get_settings()
        return bool(settings.llm.enabled and self.ctx.job.consent_third_party_llm)

    def _adopt(self, reference: ArtifactRef) -> ArtifactRef:
        """Copy a stage-produced artifact into the job's own folder."""
        return self.ctx.artifact_store.adopt(
            self.ctx.job.owner_id, self.ctx.job.job_id, reference
        )

    def _store_text(self, kind: ArtifactKind, artifact_id: str, text: str) -> ArtifactRef:
        return self.ctx.artifact_store.write_text(
            self.ctx.job.owner_id, self.ctx.job.job_id, kind, artifact_id, text
        )

    def _rehydrate(self) -> set[JobStage]:
        """Rebuild in-memory stage outputs for a job resumed in a fresh process.

        Returns the completed stages that must run again because their outputs
        live only in memory. Does nothing when the context is already populated,
        which is the in-process resume case.
        """
        done = self.ctx.job.completed_stages()
        if self.ctx.frame is not None or JobStage.INGESTED not in done:
            return set()

        from insightflow_orchestration.persistence import load_json

        def stored(name: str):
            return load_json(
                self.ctx.artifact_store, self.ctx.job.owner_id, self.ctx.job.job_id, name
            )

        # Re-reading the original is deterministic and re-verifies its hash.
        outcome = self.ctx.ingestion.ingest(self.ctx.raw_record, sheet_name=self.ctx.job.source_sheet)
        self.ctx.frame = outcome.table.frame
        self.ctx.snapshot = outcome.snapshot

        if JobStage.PROFILED in done:
            payload = stored("profile")
            self.ctx.profile = (
                DatasetProfile.model_validate(payload)
                if payload
                else profile_dataset(self.ctx.frame, self.ctx.snapshot)
            )
        if JobStage.CLEANING_PROPOSED in done:
            payload = stored("cleaning_plan")
            self.ctx.cleaning_plan = (
                CleaningPlan.model_validate(payload) if payload else plan_cleaning(self.ctx.profile)
            )
        if JobStage.CLEANING_APPLIED in done and self.ctx.cleaning_plan is not None:
            self._apply_decisions(self.ctx.cleaning_plan)
            approved = {op.operation_id for op in self.ctx.cleaning_plan.approved()}
            frame, execution = apply_cleaning(
                self.ctx.frame, self.ctx.cleaning_plan, self.ctx.snapshot,
                approved_operation_ids=approved,
            )
            self.ctx.frame = frame
            self.ctx.cleaning = execution
            self.ctx.snapshot = next_snapshot(self.ctx.snapshot, execution)
        if JobStage.PREPROCESSED in done:
            # Deterministic, so re-applying reproduces the frame the plan was made for.
            self._preprocess_frame(self.ctx.frame)
            payload = stored("preprocessing")
            if payload:
                self.ctx.preprocessing = PreprocessingResult.model_validate(payload)
        if JobStage.FEATURES_ENGINEERED in done:
            payload = stored("feature_engineering")
            if payload:
                result = FeatureEngineeringResult.model_validate(payload)
                # Re-apply the recorded specs (not re-ask a model), so the frame matches.
                frame, created, _ = apply_features(
                    self.ctx.frame, [item.spec for item in result.created], self.ctx.profile,
                    result.target_column,
                )
                self.ctx.feature_engineering = EngineeringOutcome(
                    result, frame, {name for item in created for name in output_columns(item.spec)}
                )
        if JobStage.FEATURES_SELECTED in done:
            payload = stored("feature_selection")
            if payload:
                self.ctx.feature_selection = FeatureSelectionResult.model_validate(payload)
            self.ctx.feature_charts = stored("feature_charts") or []
        if JobStage.PLANNED in done:
            payload = stored("analysis_plan")
            if payload:
                self.ctx.plan = AnalysisPlan.model_validate(payload)
                self.ctx.plan_source = self.ctx.job.versions.get("plan_source", "")
            else:
                done = done - {JobStage.PLANNED}

        self._audit("job.rehydrated", "ok", detail={"dataset_version": self.ctx.snapshot.version})

        # Everything after the last rehydratable stage is recomputed, unless the
        # job already finished (then nothing runs at all).
        if JobStage.REPORTED in done:
            return set()
        stale = {stage for stage in done if stage not in REHYDRATABLE_STAGES}
        stale.discard(JobStage.REGISTERED)
        if JobStage.PLANNED not in done and JobStage.PLANNED in self.ctx.job.completed_stages():
            stale.add(JobStage.PLANNED)
        return stale

    def _cancelled(self) -> bool:
        """Whether someone cancelled the job since this run started."""
        stored = self.ctx.state.load_job(self.ctx.job.job_id)
        if stored is not None and stored.status is JobStatus.CANCELLED:
            self.ctx.job.status = JobStatus.CANCELLED
            return True
        return self.ctx.job.status is JobStatus.CANCELLED

    def _persist(self) -> None:
        # Never overwrite a cancellation that arrived while a stage was running.
        stored = self.ctx.state.load_job(self.ctx.job.job_id)
        if stored is not None and stored.status is JobStatus.CANCELLED:
            self.ctx.job.status = JobStatus.CANCELLED
        self.ctx.state.save_job(self.ctx.job)

    def _persist_output(self, **payload) -> None:
        """Store typed stage outputs so readers never re-run the analysis."""
        save_stage_outputs(
            self.ctx.artifact_store,
            self.ctx.job.owner_id,
            self.ctx.job.job_id,
            {key: value for key, value in payload.items() if value is not None},
        )

    def _audit(
        self,
        action: str,
        outcome: str,
        *,
        stage: str | None = None,
        subject: str | None = None,
        detail: dict | None = None,
        actor: str = "system",
    ) -> None:
        self.ctx.state.audit(
            self.ctx.job.job_id,
            self.ctx.job.owner_id,
            AuditEvent(
                actor=actor,
                action=action,
                outcome=outcome,
                stage=stage,
                subject=subject,
                detail=_scrub(detail or {}),
            ),
        )


def _missing_counts(frame: pl.DataFrame) -> dict[str, int]:
    counts = frame.null_count().row(0, named=True)
    return {name: int(count) for name, count in counts.items() if count}


def _scrub(detail: dict) -> dict:
    """Audit details must never carry dataset cell values."""
    forbidden = {"values", "rows", "cells", "records", "sample", "samples"}
    return {
        key: value
        for key, value in detail.items()
        if key.lower() not in forbidden and not isinstance(value, (list, dict))
    }


def build_context(
    *,
    job: Job,
    raw_record: RawFileRecord,
    artifact_store: ArtifactStore,
    state: StateStore,
    ingestion: IngestionService | None = None,
    artifact_root: str | Path | None = None,
) -> JobContext:
    """Assemble a `JobContext`.

    `ingestion` should normally be the same service the job was created with, so
    that uploads and re-reads resolve to one directory. It is optional only for
    callers that have not yet configured an ingestion root.
    """
    settings = get_settings()
    artifact_dir = Path(artifact_root or artifact_store.job_dir(job.owner_id, job.job_id))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    return JobContext(
        job=job,
        raw_record=raw_record,
        artifact_store=artifact_store,
        state=state,
        artifact_dir=artifact_dir,
        ingestion=ingestion
        or IngestionService(settings.artifact_path("raw"), owner_id=job.owner_id),
    )