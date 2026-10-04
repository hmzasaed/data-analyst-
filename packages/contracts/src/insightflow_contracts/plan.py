"""`AnalysisPlan` - the only thing the planner agent is allowed to produce.

Plans reference tools from the registry by name. The orchestrator validates
every step against the registry before execution, so an unconstrained model
cannot invent an analysis method or pass an unvalidated column.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator, model_validator

from insightflow_contracts.profile import StrictModel


class AnalysisType(StrEnum):
    DESCRIPTIVE = "descriptive"
    COMPARISON = "comparison"
    TREND = "trend"
    RELATIONSHIP = "relationship"
    ANOMALY = "anomaly"
    DISTRIBUTION = "distribution"


class PlannedStep(StrictModel):
    step_id: str
    analysis_type: AnalysisType
    tool_name: str
    parameters: dict[str, object] = Field(default_factory=dict)
    expected_outputs: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    rationale: str | None = None
    requires_user_approval: bool = False
    optional: bool = False
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("expected_outputs")
    @classmethod
    def known_outputs_only(cls, value: list[str]) -> list[str]:
        allowed = {"table", "chart", "metric", "records", "none"}
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown expected_outputs {sorted(unknown)}; allowed {sorted(allowed)}")
        return value

    @model_validator(mode="after")
    def _require_rationale_for_optional(self) -> PlannedStep:
        if self.optional and not self.rationale:
            raise ValueError("optional steps must explain why they may be skipped")
        return self


class AnalysisPlan(StrictModel):
    objective: str
    assumptions: list[str] = Field(default_factory=list)
    steps: list[PlannedStep] = Field(min_length=1)
    not_supported_questions: list[str] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    is_exploratory: bool = False
    planner_model: str | None = None
    planner_version: str = "1"

    @model_validator(mode="after")
    def _validate_graph(self) -> AnalysisPlan:
        ids = [step.step_id for step in self.steps]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate step_id(s): {sorted(duplicates)}")
        known = set(ids)
        for step in self.steps:
            unknown_dependencies = set(step.depends_on) - known
            if unknown_dependencies:
                raise ValueError(
                    f"step {step.step_id} depends on unknown steps "
                    f"{sorted(unknown_dependencies)}"
                )
        return self

    def step(self, step_id: str) -> PlannedStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def required_steps(self) -> list[PlannedStep]:
        return [step for step in self.steps if not step.optional]

    def digests(self) -> str:
        """Stable fingerprint of the plan, used to detect no-op resumes."""
        from insightflow_contracts.ids import content_digest

        return content_digest(
            {
                "objective": self.objective,
                "steps": [
                    {
                        "tool": s.tool_name,
                        "params": s.parameters,
                        "type": s.analysis_type.value,
                    }
                    for s in self.steps
                ],
            }
        )
