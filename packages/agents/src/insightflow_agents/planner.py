"""`AnalysisPlanner`: turn a business question into a validated, runnable plan.

Two paths, same contract:

* **deterministic** (default, and the fallback whenever a model is unavailable) -
  intent is read from keywords and columns are selected by semantic role. This is
  what runs in development, in CI and whenever the model call fails.
* **model-assisted** - the question, a redacted profile and the tool catalogue go
  to a model, which must return a schema-valid `AnalysisPlan`. Every step is then
  validated against the registry and the actual dataset before anything runs.

Whichever path produced it, the plan is passed through `PlanValidator` before
execution. A model cannot introduce a tool, column or parameter that does not
exist, because those checks are code, not instructions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from insightflow_analytics import PlanValidator, ToolRegistry
from insightflow_analytics.context import AnalysisContext
from insightflow_contracts import (
    AnalysisPlan,
    AnalysisType,
    ColumnProfile,
    DatasetProfile,
    InferredType,
    JobObjective,
    PlannedStep,
    PlanRejectedError,
    SemanticRole,
)

from insightflow_agents.context import build_model_context
from insightflow_agents.llm import LLMClient
from insightflow_agents.prompts import PromptBuilder

PLANNER_VERSION = "1"

#: Question keywords mapped to the intent they imply.
_INTENT_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("anomaly", ("anomal", "unusual", "outlier", "odd", "weird", "flag", "unusual", "exception")),
    ("trend", ("trend", "over time", "growth", "decline", "month", "quarter", "week", "year",
               "history", "season", "forecast")),
    ("comparison", ("compare", "versus", " vs ", "by region", "breakdown", "which", "ranking",
                    "top ", "leader", "difference", "segment")),
    ("relationship", ("why", "cause", "driver", "correlat", "related", "impact of",
                      "relationship", "predict")),
    ("overview", ("overview", "summary", "profile", "what", "describe", "look at", "review")),
)

#: Questions that need data this platform does not have. Reported rather than faked.
UNSUPPORTABLE_TOPICS: dict[str, str] = {
    "customer-level prediction": "requires training a predictive model, which is out of scope.",
    "forecasting the future": "requires forecast methods beyond descriptive time-series analysis.",
    "causal conclusion": (
        "requires experimental or quasi-experimental design; this analysis can only show association."
    ),
    "sentiment or intent detection": "requires reading free-text content, which is not modelled.",
    "external benchmarks": "requires third-party market data, which is not available here.",
    "real-time monitoring": "requires a live data connection, which this upload does not provide.",
}


@dataclass
class PlanningOutcome:
    plan: AnalysisPlan
    source: str  # "deterministic" | "model" | "deterministic_fallback"
    model_used: str | None = None
    llm_error: str | None = None
    clarifying_questions: list[str] = field(default_factory=list)
    rejected_steps: list[str] = field(default_factory=list)


class AnalysisPlanner:
    def __init__(self, registry: ToolRegistry, client: LLMClient | None = None) -> None:
        self.registry = registry
        self.client = client or LLMClient.from_settings()

    # ------------------------------------------------------------------- plan
    def plan(
        self,
        profile: DatasetProfile,
        objective: JobObjective,
        ctx: AnalysisContext,
    ) -> PlanningOutcome:
        if not objective.question or not objective.question.strip():
            outcome = PlanningOutcome(
                plan=self.exploratory_plan(profile, ctx), source="deterministic"
            )
            outcome.plan.is_exploratory = True
            return outcome

        if self.client.enabled:
            outcome = self._model_plan(profile, objective, ctx)
            if outcome is not None:
                return outcome

        return PlanningOutcome(plan=self.rule_based_plan(profile, objective), source="deterministic_fallback")

    # ------------------------------------------------------- model-assisted
    def _model_plan(
        self, profile: DatasetProfile, objective: JobObjective, ctx: AnalysisContext
    ) -> PlanningOutcome | None:
        model_context = build_model_context(profile)
        layers = PromptBuilder().with_role("planner").complete()
        layers.context = model_context.to_json(indent=2)
        layers.objective = json.dumps(
            {
                "question": objective.question,
                "business_context": objective.business_context,
                "time_grain": objective.time_grain,
                "dimension_preferences": objective.dimension_preferences,
                "measure_preferences": objective.measure_preferences,
                "exclusions": objective.exclusions,
            },
            indent=2,
        )
        layers.evidence = self._catalogue_text()

        plan, response = self.client.complete_json(
            system=layers.system_message(),
            user=layers.user_message(),
            schema=AnalysisPlan,
            complex_reasoning=len(objective.question or "") > 120,
        )
        if plan is None:
            return None

        # Anything the model proposed that this dataset cannot support is dropped
        # rather than executed, and reported so the user knows what was dropped.
        accepted, rejected = self._filter_steps(plan.steps, ctx)
        plan = plan.model_copy(
            update={
                "steps": accepted,
                "planner_model": response.model,
                "planner_version": PLANNER_VERSION,
                "is_exploratory": False,
                "not_supported_questions": _merge_unique(
                    plan.not_supported_questions, _unsupported_for(profile, objective)
                ),
            }
        )
        if not plan.steps:
            return None

        return PlanningOutcome(
            plan=plan,
            source="model",
            model_used=response.model,
            clarifying_questions=plan.clarifying_questions,
            rejected_steps=rejected,
        )

    def _filter_steps(self, steps: list[PlannedStep], ctx: AnalysisContext) -> tuple[list[PlannedStep], list[str]]:
        """Keep only steps whose tool exists and whose parameters validate here."""
        accepted: list[PlannedStep] = []
        rejected: list[str] = []
        for step in steps:
            tool = self.registry.get(step.tool_name)
            if tool is None:
                rejected.append(f"{step.step_id}: tool '{step.tool_name}' is not registered")
                continue
            try:
                self.registry.validate_parameters(step.tool_name, step.parameters, context=ctx)
            except PlanRejectedError as exc:
                rejected.extend(f"{step.step_id}: {violation}" for violation in exc.violations)
                continue
            accepted.append(step)
        return accepted, rejected

    def _catalogue_text(self) -> str:
        return json.dumps(
            [
                {
                    "tool_name": spec.name,
                    "analysis_type": spec.analysis_type,
                    "summary": spec.summary,
                    "parameters": spec.input_schema,
                    "max_rows": spec.max_rows,
                }
                for spec in self.registry.catalogue()
            ],
            indent=2,
        )

    # ----------------------------------------------------------- deterministic
    def rule_based_plan(
        self, profile: DatasetProfile, objective: JobObjective
    ) -> AnalysisPlan:
        """Intent-driven plan built from semantic roles, with no model involved."""
        intent = detect_intent(objective.question or "")
        question = (objective.question or "").lower()
        steps: list[PlannedStep] = []
        assumptions: list[str] = []

        measures = _measures(profile)
        dimensions = _dimensions(profile)
        time_columns = _time_columns(profile)
        identifiers = [c for c in profile.columns if c.semantic_role is SemanticRole.IDENTIFIER]

        steps.append(
            _step("overview_measures", AnalysisType.DESCRIPTIVE, "describe_measures",
                  {"columns": [c.name for c in measures[:6]]})
        )
        steps.append(
            _step("data_quality", AnalysisType.DESCRIPTIVE, "missingness_profile", {})
        )

        referenced = _referenced_columns(question, profile)

        if intent in {"trend", "overview"} and time_columns:
            date_column = _pick(referenced, time_columns)
            grain = (
                "yearly"
                if _is_year_column(_column(profile, date_column))
                else objective.time_grain or _suggest_grain(profile)
            )
            steps.append(
                _step(
                    "time_series",
                    AnalysisType.TREND,
                    "time_series_aggregate",
                    {
                        "date_column": date_column,
                        "grain": grain,
                        "measures": [c.name for c in measures[:3]],
                    },
                )
            )
            if measures:
                steps.append(
                    _step("growth", AnalysisType.TREND, "growth_rates",
                          {"date_column": date_column, "column": measures[0].name})
                )
            assumptions.append(
                f"Time grain of '{grain}' was chosen from the date column's own coverage."
            )

        if intent in {"comparison", "overview", "anomaly"} and dimensions and measures:
            dimension = _pick(referenced, dimensions)
            measure = _pick_measure(referenced, measures)
            steps.append(
                _step("group_comparison", AnalysisType.COMPARISON, "compare_groups",
                      {"group_by": dimension, "column": measure})
            )
            steps.append(
                _step("group_ranking", AnalysisType.DESCRIPTIVE, "rank_categories",
                      {"group_by": dimension, "measure": measure, "limit": 20})
            )
            steps.append(
                _step("group_contribution", AnalysisType.COMPARISON, "share_of_total",
                      {"group_by": dimension, "column": measure})
            )
            if identifiers:
                steps.append(
                    _step("group_distribution", AnalysisType.DISTRIBUTION, "distribution_by_group",
                          {"group_by": dimension, "column": measure})
                )

        if intent == "relationship" and len(measures) >= 2:
            numeric = [c.name for c in measures[:6]]
            steps.append(
                _step("correlations", AnalysisType.RELATIONSHIP, "correlation_matrix",
                      {"columns": numeric})
            )
            assumptions.append(
                "Correlation is reported as association only; no causal claim is made."
            )

        if len(measures) >= 2 and intent != "relationship":
            steps.append(
                _step("correlations", AnalysisType.RELATIONSHIP, "correlation_matrix",
                      {"columns": [c.name for c in measures[:6]]})
            )

        if measures:
            steps.append(
                _step("primary_distribution", AnalysisType.DISTRIBUTION, "value_distribution",
                      {"column": _pick_measure(referenced, measures)})
            )

        if intent in {"anomaly", "overview"} and measures:
            measure = _pick_measure(referenced, measures)
            steps.append(
                _step("anomalies", AnalysisType.ANOMALY, "detect_outliers",
                      {"column": measure, "method": "auto"})
            )
            if time_columns:
                steps.append(
                    _step("anomalies_over_time", AnalysisType.ANOMALY, "detect_outliers",
                          {"column": measure, "method": "rolling_mad",
                           "method_options": {"window": 7}})
                )
            if dimensions:
                steps.append(
                    _step("anomalies_by_group", AnalysisType.ANOMALY, "detect_group_outliers",
                          {"column": measure, "group_by": [_pick(referenced, dimensions)]})
                )

        if not measures:
            assumptions.append(
                "No numeric column is available, so only data-quality checks were planned."
            )

        return AnalysisPlan(
            objective=objective.question or "Exploratory analysis of the uploaded dataset",
            assumptions=_merge_unique(assumptions, _unsupported_for(profile, objective)),
            steps=steps,
            not_supported_questions=_unsupported_for(profile, objective),
            is_exploratory=intent == "overview" and not objective.question,
            planner_version=PLANNER_VERSION,
        )

    def exploratory_plan(self, profile: DatasetProfile, ctx: AnalysisContext) -> AnalysisPlan:
        """A general first look when the user did not ask a question."""
        del ctx  # the exploratory plan depends on the profile only
        return self.rule_based_plan(
            profile,
            JobObjective(question="", is_exploratory=True),
        )

    # -------------------------------------------------------------- validation
    def validate(self, plan: AnalysisPlan, ctx: AnalysisContext):
        return PlanValidator(self.registry).validate(plan, ctx)


# --------------------------------------------------------------------- helpers
def detect_intent(question: str) -> str:
    """Best-effort intent from keywords. Defaults to `overview`."""
    lowered = f" {question.lower()} "
    scores = {
        intent: sum(1 for keyword in keywords if keyword in lowered)
        for intent, keywords in _INTENT_PATTERNS
    }
    best = max(scores, key=lambda key: scores[key])
    return best if scores[best] > 0 else "overview"


def unsupported_topics(question: str) -> list[str]:
    lowered = question.lower()
    hits = [topic for topic in UNSUPPORTABLE_TOPICS if topic.split()[0] in lowered]
    return hits


def _unsupported_for(profile: DatasetProfile, objective: JobObjective) -> list[str]:
    """Questions this dataset cannot answer, stated plainly."""
    notes: list[str] = []
    if not profile.columns:
        return notes
    has_time = bool(_time_columns(profile))
    has_measure = any(c.semantic_role is SemanticRole.MEASURE for c in profile.columns)
    has_dimension = any(
        c.semantic_role
        in {SemanticRole.DIMENSION, SemanticRole.GEOGRAPHY, SemanticRole.CATEGORY}
        for c in profile.columns
    )

    if not has_time:
        notes.append(
            "Questions about change over time: no date column was identified in this dataset."
        )
    if not has_measure:
        notes.append(
            "Questions about magnitude or totals: no numeric measure column was identified."
        )
    if not has_dimension:
        notes.append(
            "Questions comparing segments: no categorical dimension column was identified."
        )
    notes.extend(
        f"Not supported: {topic} - {UNSUPPORTABLE_TOPICS[topic]}"
        for topic in unsupported_topics(objective.question or "")
    )
    return notes


def _measures(profile: DatasetProfile) -> list[ColumnProfile]:
    candidates = [
        column
        for column in profile.columns
        if column.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}
        and column.semantic_role not in {SemanticRole.IDENTIFIER, SemanticRole.TIME}
        and column.pii_risk.value not in {"high"}
    ]
    if not candidates:
        candidates = [
            column
            for column in profile.columns
            if column.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}
        ]
    return sorted(candidates, key=lambda c: (-c.null_count / max(profile.row_count, 1)))


#: Plausible calendar years; mirrors `insightflow_analytics.context.YEAR_RANGE`.
_YEAR_RANGE = (1000, 2999)


def _is_year_column(column: ColumnProfile | None) -> bool:
    """A whole-number column whose values all fall in the calendar-year range."""
    if column is None or column.inferred_type not in {InferredType.INTEGER, InferredType.NUMBER}:
        return False
    numeric = column.numeric
    if numeric is None or numeric.min is None or numeric.max is None:
        return False
    if not (float(numeric.min).is_integer() and float(numeric.max).is_integer()):
        return False
    low, high = _YEAR_RANGE
    return low <= numeric.min and numeric.max <= high


def _time_columns(profile: DatasetProfile) -> list[ColumnProfile]:
    """Columns the date-based trend tools can use as a time axis.

    The time role comes from the column *name*, so a numeric `month` (1-12) or
    `week_number` gets it too. Those cannot be placed on a calendar, so only real
    dates, date-like text and whole-number years qualify. Dates come first.
    """
    candidates = [c for c in profile.columns if c.semantic_role is SemanticRole.TIME]
    dated = [
        c for c in candidates
        if c.inferred_type in {InferredType.DATE, InferredType.DATETIME, InferredType.TEXT}
    ]
    years = [c for c in candidates if _is_year_column(c)]
    return dated + years


def _column(profile: DatasetProfile, name: str) -> ColumnProfile | None:
    return next((c for c in profile.columns if c.name == name), None)


def _dimensions(profile: DatasetProfile) -> list[ColumnProfile]:
    return [
        column
        for column in profile.columns
        if column.semantic_role
        in {SemanticRole.DIMENSION, SemanticRole.GEOGRAPHY, SemanticRole.CATEGORY}
        and column.unique_count > 1
        and column.unique_count <= 200
        and column.inferred_type not in {InferredType.DATE, InferredType.DATETIME}
    ]


def _referenced_columns(question: str, profile: DatasetProfile) -> set[str]:
    """Column names the question mentions explicitly."""
    lowered = question.lower()
    return {
        column.name
        for column in profile.columns
        if column.name.lower() in lowered
        or (column.original_name and column.original_name.lower() in lowered)
    }


def _pick(referenced: set[str], candidates: list[ColumnProfile]) -> str:
    """Prefer a column the question names; otherwise take the first candidate."""
    for column in candidates:
        if column.name in referenced:
            return column.name
    return candidates[0].name if candidates else ""


def _pick_measure(referenced: set[str], measures: list[ColumnProfile]) -> str:
    return _pick(referenced, measures)


def _suggest_grain(profile: DatasetProfile) -> str:
    coverage = profile.time_coverage
    if coverage is None or coverage.span_days is None:
        return "monthly"
    if coverage.span_days <= 31:
        return "daily"
    if coverage.span_days <= 180:
        return "weekly"
    if coverage.span_days <= 1200:
        return "monthly"
    return "quarterly"


def _step(step_id: str, analysis_type: AnalysisType, tool_name: str, parameters: dict) -> PlannedStep:
    return PlannedStep(
        step_id=step_id,
        analysis_type=analysis_type,
        tool_name=tool_name,
        parameters=parameters,
        expected_outputs=["table", "metric"],
    )


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    for group in groups:
        for item in group:
            if item and item not in merged:
                merged.append(item)
    return merged


