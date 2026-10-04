"""Registered, deterministic analysis tools.

The agent may only call what `REGISTRY` exposes. Charts are emitted as validated
`ChartRequest` objects and rendered separately, so a chart can always be checked
against the table it came from.
"""

from insightflow_analytics.context import AnalysisContext, ToolOutcome
from insightflow_analytics.execute import (
    PlanExecutor,
    StepArtifacts,
    execute_plan,
    step_digest,
)
from insightflow_analytics.registry import (
    ParameterSpec,
    RegisteredTool,
    ToolRegistry,
)
from insightflow_analytics.tools import REGISTRY, TOOL_VERSION, build_registry
from insightflow_analytics.validate import PlanValidator, ValidatedPlan

__all__ = [
    "REGISTRY",
    "TOOL_VERSION",
    "AnalysisContext",
    "ParameterSpec",
    "PlanExecutor",
    "PlanValidator",
    "RegisteredTool",
    "StepArtifacts",
    "ToolOutcome",
    "ToolRegistry",
    "ValidatedPlan",
    "build_registry",
    "execute_plan",
    "step_digest",
]
