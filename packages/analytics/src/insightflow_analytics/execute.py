"""Step execution: run a validated plan through registered tools.

Guarantees enforced here:

* every step runs against exactly one dataset version, recorded in its result;
* every evidence table is persisted before the step is reported complete;
* a failing step yields a `failed` result carrying the error text, so the job
  continues and the report can state what failed;
* a sandbox violation or unsupported input is re-raised rather than swallowed;
* no number is produced here - only tools compute values.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl
from insightflow_contracts import (
    AnalysisPlan,
    ArtifactRef,
    ChartRequest,
    DatasetSnapshot,
    ExecutionResult,
    InsightFlowError,
    MetricValue,
    PlannedStep,
    ResourceUsage,
    content_digest,
    derive_id,
)

from insightflow_analytics.context import AnalysisContext, ToolOutcome
from insightflow_analytics.registry import ToolRegistry

#: Error codes that must stop the job rather than be recorded and skipped.
FATAL_ERROR_CODES = frozenset({"sandbox_violation", "unsupported_input"})


@dataclass
class StepArtifacts:
    """Everything one plan execution produced, keyed for the report."""

    results: list[ExecutionResult] = field(default_factory=list)
    tables: list[ArtifactRef] = field(default_factory=list)
    charts: list[ChartRequest] = field(default_factory=list)
    metrics_by_step: dict[str, list[MetricValue]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def metric(self, step_id: str, name: str) -> MetricValue | None:
        for metric in self.metrics_by_step.get(step_id, []):
            if metric.name == name:
                return metric
        return None

    def all_metrics(self) -> list[MetricValue]:
        merged: list[MetricValue] = []
        for metrics in self.metrics_by_step.values():
            merged.extend(metrics)
        return merged

    def succeeded_steps(self) -> list[str]:
        return [r.step_id for r in self.results if r.status == "succeeded"]

    def failed_steps(self) -> list[str]:
        return [r.step_id for r in self.results if r.status == "failed"]

    def errors(self) -> list[str]:
        return [error for result in self.results for error in result.errors]

    def result_for(self, step_id: str) -> ExecutionResult | None:
        for result in self.results:
            if result.step_id == step_id:
                return result
        return None


class PlanExecutor:
    """Runs `PlannedStep`s against one dataset version."""

    version = "1"

    def __init__(
        self,
        registry: ToolRegistry,
        frame: pl.DataFrame,
        snapshot: DatasetSnapshot,
        artifact_dir: Path,
        *,
        owner_id: str = "anonymous",
    ) -> None:
        self.registry = registry
        self.frame = frame
        self.snapshot = snapshot
        self.artifact_dir = artifact_dir
        self.owner_id = owner_id

    def base_context(self, step_id: str = "plan") -> AnalysisContext:
        return AnalysisContext(
            frame=self.frame,
            snapshot=self.snapshot,
            artifact_dir=self.artifact_dir,
            step_id=step_id,
            owner_id=self.owner_id,
        )

    def execute(
        self,
        plan: AnalysisPlan,
        *,
        steps: list[PlannedStep] | None = None,
        continue_on_error: bool = True,
    ) -> StepArtifacts:
        artifacts = StepArtifacts()
        for step in steps if steps is not None else plan.steps:
            result, outcome = self._execute_step(step)
            artifacts.results.append(result)
            if result.status != "succeeded":
                if not continue_on_error:
                    break
                continue
            artifacts.metrics_by_step[step.step_id] = result.metrics
            artifacts.tables.extend(result.tables)
            artifacts.warnings.extend(result.warnings)
            if outcome is not None:
                # Tag each chart with its producing step, as metrics are, so a report
                # can place it with the analysis it illustrates.
                artifacts.charts.extend(
                    chart if chart.analysis_step_id else chart.model_copy(
                        update={"analysis_step_id": step.step_id}
                    )
                    for chart in outcome.charts
                )
        return artifacts

    # ------------------------------------------------------------------ steps
    def _execute_step(
        self, step: PlannedStep
    ) -> tuple[ExecutionResult, ToolOutcome | None]:
        tool = self.registry.get(step.tool_name)
        if tool is None:
            return _failed(step, self.snapshot.version, "plan_rejected", "tool is not registered"), None

        ctx = AnalysisContext(
            frame=self.frame,
            snapshot=self.snapshot,
            artifact_dir=self.artifact_dir,
            max_rows=min(5_000_000, tool.spec.max_rows),
            step_id=step.step_id,
            owner_id=self.owner_id,
        )

        started = time.perf_counter()
        try:
            parameters = self.registry.validate_parameters(step.tool_name, step.parameters, context=ctx)
            outcome = tool.handler(ctx, parameters)
        except InsightFlowError as exc:
            if exc.code in FATAL_ERROR_CODES:
                raise
            elapsed = time.perf_counter() - started
            return (
                _failed(step, self.snapshot.version, exc.code, exc.message, elapsed=elapsed),
                None,
            )
        except Exception as exc:  # pragma: no cover - defensive catch-all
            elapsed = time.perf_counter() - started
            return (
                _failed(
                    step, self.snapshot.version, "tool_execution_error",
                    f"{type(exc).__name__}: {exc}", elapsed=elapsed,
                ),
                None,
            )

        return self._finalise(step, tool.spec.name, tool.spec.version, outcome, started), outcome

    def _finalise(
        self,
        step: PlannedStep,
        tool_name: str,
        tool_version: str,
        outcome: ToolOutcome,
        started: float,
    ) -> ExecutionResult:
        ctx = self.base_context(step.step_id)
        tables: list[ArtifactRef] = []
        for frame, name in zip(outcome.tables, outcome.table_names, strict=True):
            try:
                tables.append(ctx.write_table(frame, name))
            except InsightFlowError as exc:
                return _failed(
                    step, self.snapshot.version, exc.code,
                    f"could not persist evidence table '{name}': {exc.message}",
                    elapsed=time.perf_counter() - started,
                )

        # Tag every metric with its producing step and evidence table so downstream
        # verification can match a claim to the exact computation and artifact,
        # even when two steps share a metric name or a step writes several tables.
        default_table = outcome.table_names[0] if outcome.table_names else None
        metrics = [
            metric.model_copy(
                update={
                    "source_step": step.step_id,
                    "table": metric.table or default_table,
                }
            )
            for metric in outcome.metrics
        ]

        return ExecutionResult(
            step_id=step.step_id,
            tool_name=tool_name,
            tool_version=tool_version,
            dataset_version=self.snapshot.version,
            status="succeeded",
            metrics=metrics,
            tables=tables,
            warnings=outcome.warnings,
            usage=ResourceUsage(wall_seconds=round(time.perf_counter() - started, 4)),
            tool_digest=content_digest(
                {"tool": tool_name, "step": step.step_id, "parameters": step.parameters}, length=24
            ),
        )


def _failed(
    step: PlannedStep,
    dataset_version: int,
    code: str,
    message: str,
    *,
    elapsed: float = 0.0,
) -> ExecutionResult:
    return ExecutionResult(
        step_id=step.step_id,
        tool_name=step.tool_name,
        tool_version="1",
        dataset_version=dataset_version,
        status="failed",
        errors=[f"[{code}] {message}"],
        usage=ResourceUsage(wall_seconds=round(elapsed, 4)),
    )


def execute_plan(
    plan: AnalysisPlan,
    registry: ToolRegistry,
    frame: pl.DataFrame,
    snapshot: DatasetSnapshot,
    artifact_dir: Path,
    *,
    owner_id: str = "anonymous",
) -> StepArtifacts:
    """Validate parameters and run every step, collecting evidence tables."""
    return PlanExecutor(registry, frame, snapshot, artifact_dir, owner_id=owner_id).execute(plan)


def step_digest(step: PlannedStep) -> str:
    """Stable identity of a step, used to detect no-op resumes."""
    return derive_id("step", {"tool": step.tool_name, "parameters": step.parameters})
