"""Plan validation: refuse to execute a plan the data cannot support.

Two distinct failure modes are handled here:

* *capability* violations - the plan asks for a tool that does not exist, or a
  column that is absent or of the wrong type. These are `PlanRejectedError` and
  surface to the user as "this question is not answerable from this dataset".
* *statistical* preconditions - too few rows, too few periods. These are warnings
  attached to the step so the report can state the limitation instead of hiding it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl
from insightflow_contracts import AnalysisPlan, PlannedStep, PlanRejectedError

from insightflow_analytics.context import AnalysisContext, is_temporal_dtype
from insightflow_analytics.registry import ToolRegistry


@dataclass
class ValidatedPlan:
    plan: AnalysisPlan
    warnings: list[str] = field(default_factory=list)
    runnable_steps: list[PlannedStep] = field(default_factory=list)


class PlanValidator:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def validate(self, plan: AnalysisPlan, ctx: AnalysisContext) -> ValidatedPlan:
        violations: list[str] = []
        warnings: list[str] = []
        runnable: list[PlannedStep] = []

        for step in plan.steps:
            tool = self.registry.get(step.tool_name)
            if tool is None:
                violations.append(
                    f"step '{step.step_id}': tool '{step.tool_name}' is not registered "
                    f"(available: {', '.join(self.registry.names())})"
                )
                continue
            try:
                self.registry.validate_parameters(step.tool_name, step.parameters, context=ctx)
            except PlanRejectedError as exc:
                violations.extend(f"step '{step.step_id}': {item}" for item in exc.violations)
                continue

            precondition_warning = self._statistical_precondition(step.tool_name, ctx)
            if precondition_warning:
                warnings.append(f"step '{step.step_id}': {precondition_warning}")
            runnable.append(step)

        if violations:
            raise PlanRejectedError(
                "the analysis plan cannot be executed against this dataset",
                violations=violations,
                objective=plan.objective,
            )
        if not runnable:
            raise PlanRejectedError(
                "the plan has no runnable steps",
                violations=["every step was rejected during validation"],
            )
        return ValidatedPlan(plan=plan, warnings=warnings, runnable_steps=runnable)

    def _statistical_precondition(self, tool_name: str, ctx: AnalysisContext) -> str | None:
        rows = ctx.frame.height
        if tool_name in {"compare_groups", "cross_tabulation"} and rows < 20:
            return f"only {rows} rows are available, which is thin for a group comparison"
        if tool_name == "correlation_matrix" and rows < 20:
            return f"only {rows} complete rows are available for correlation"
        if tool_name == "simple_regression" and rows < 12:
            return f"only {rows} rows are available for regression"
        if tool_name in {"time_series_aggregate", "trend_line", "growth_rates"}:
            dates = _date_columns(ctx.frame)
            if not dates:
                return "no parsed date column is available for time-series analysis"
        return None


def _date_columns(frame: pl.DataFrame) -> list[str]:
    return [name for name, dtype in frame.schema.items() if is_temporal_dtype(dtype)]
