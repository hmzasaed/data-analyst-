"""Explaining a cleaning plan in business language.

The transformations themselves are deterministic. This agent only writes the
"why should I care" sentence that accompanies each one, and it is strictly
optional: with the model disabled, the deterministic `reason` and `impact_note`
fields already say what happened.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightflow_contracts import (
    CleaningOperation,
    CleaningPlan,
    DatasetProfile,
    StrictModel,
)

from insightflow_agents.llm import LLMClient
from insightflow_agents.prompts import PromptBuilder

EXPLAINER_VERSION = "1"


class OperationExplanation(StrictModel):
    """Plain-language explanation of one proposed change."""

    operation_id: str
    business_impact: str
    risk_if_ignored: str
    recommendation: str


class CleaningExplanation(StrictModel):
    operations: list[OperationExplanation]
    overall: str


@dataclass
class CleaningExplainer:
    client: LLMClient | None = None
    version: str = EXPLAINER_VERSION

    def explain(self, plan: CleaningPlan, profile: DatasetProfile) -> CleaningExplanation:
        deterministic = self._deterministic(plan)
        if not self.client or not self.client.enabled:
            return deterministic
        if not plan.operations:
            return deterministic

        layers = PromptBuilder().with_role("cleaning").complete()
        layers.context = (
            f"Dataset: {profile.row_count:,} rows x {profile.column_count} columns. "
            f"Quality issues detected: {len(profile.quality_issues)}."
        )
        layers.objective = "Explain each proposed cleaning change to a business user."
        layers.evidence = "\n".join(
            f"- `{op.kind.value}` on `{op.column or 'table'}` ({op.review_level.value}): "
            f"{op.reason} Affects {op.affected_rows:,} rows."
            for op in plan.operations
        )

        result, _ = self.client.complete_json(
            system=layers.system_message(),
            user=layers.user_message(),
            schema=CleaningExplanation,
            complex_reasoning=False,
        )
        if result is None:
            return deterministic

        # Only keep explanations for operations that exist, so a hallucinated id
        # cannot invent a change the plan never proposed.
        known = {op.operation_id: op for op in plan.operations}
        kept = [item for item in result.operations if item.operation_id in known]
        if not kept:
            return deterministic
        return CleaningExplanation(
            operations=kept,
            overall=result.overall or deterministic.overall,
        )

    def _deterministic(self, plan: CleaningPlan) -> CleaningExplanation:
        operations = [
            OperationExplanation(
                operation_id=op.operation_id,
                business_impact=op.impact_note or op.reason,
                risk_if_ignored=_risk(op),
                recommendation=_recommendation(op),
            )
            for op in plan.operations
        ]
        return CleaningExplanation(
            operations=operations,
            overall=_overall(plan),
        )


def _risk(operation: CleaningOperation) -> str:
    if operation.kind.value.startswith("drop_exact"):
        return "Totals may double-count the duplicated records."
    if operation.kind.value.startswith("parse"):
        return (
            "Aggregations over this column cannot be computed at all while it is stored as text."
        )
    if operation.kind.value.startswith("mask"):
        return "The report may reproduce personal data that should not be shared."
    if operation.kind.value.startswith("fill_null"):
        return "Aggregates will silently exclude these rows, which can look like a real drop."
    if operation.kind.value.startswith("normalise_categor"):
        return "The same category will be counted as several separate groups."
    if operation.kind.value.startswith(("trim", "collapse", "normalise_case")):
        return "Groups that should be identical will be counted separately."
    return "Results may be inconsistent or hard to reproduce."


def _recommendation(operation: CleaningOperation) -> str:
    level = operation.review_level.value
    if level == "automatic":
        return "Safe to apply; it has been applied to the analysis copy only."
    if level == "recommended":
        return "Apply if you want these values counted as one group."
    return "Review before applying: this changes the data's meaning, not just its shape."


def _overall(plan: CleaningPlan) -> str:
    summary = plan.summary()
    if not plan.operations:
        return "No cleaning changes were proposed for this dataset."
    return (
        f"{summary.total_operations} change(s) proposed: {summary.automatic} safe to apply "
        f"automatically, {summary.needs_review} need review. Applying all of them would touch "
        f"{summary.rows_touched:,} rows. The original file is never modified."
    )