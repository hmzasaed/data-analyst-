"""Feature engineering and feature selection agents.

Same shape as the analysis planner:

* **FeatureEngineeringAgent** decides the target and proposes new features. With a
  model enabled and consented, it sends the redacted profile, the target and the
  registered transform kinds to the model, which returns a `FeaturePlan`. Every
  proposal - the model's and the rules' alike - then passes through
  `validate_spec`, so a model cannot invent a column, use personal data, or leak
  the target into its own inputs. Without a model the rules run alone.
* **FeatureSelectionAgent** chooses the selection settings from the task, runs the
  ranking and cross-validation, and explains the result. The numbers come only
  from `insightflow_features.select_features`; a model may phrase the explanation
  and nothing else.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import polars as pl
from insightflow_contracts import (
    FEATURE_KINDS,
    DatasetProfile,
    FeatureEngineeringResult,
    FeatureOptions,
    FeaturePlan,
    FeatureSelectionResult,
    FeatureSpec,
    PreprocessingOptions,
    RejectedFeature,
)
from insightflow_features import (
    METHODS,
    apply_features,
    build_matrix,
    choose_target,
    output_columns,
    propose_features,
    select_features,
)
from pydantic import BaseModel, Field

from insightflow_agents.context import build_model_context
from insightflow_agents.llm import LLMClient
from insightflow_agents.prompts import PromptBuilder

FEATURE_AGENT_VERSION = "1"


@dataclass
class EngineeringOutcome:
    result: FeatureEngineeringResult
    frame: pl.DataFrame
    engineered_columns: set[str] = field(default_factory=set)


class FeatureEngineeringAgent:
    def __init__(self, client: LLMClient | None = None, *, allow_model: bool = False) -> None:
        self.client = client or LLMClient.from_settings()
        self.allow_model = allow_model and self.client.enabled

    def run(
        self,
        frame: pl.DataFrame,
        profile: DatasetProfile | None,
        options: FeatureOptions,
        *,
        question: str | None,
    ) -> EngineeringOutcome:
        if not options.enabled:
            return EngineeringOutcome(
                FeatureEngineeringResult(skipped_reason="Feature engineering is turned off for this job.",
                                         source="skipped"),
                frame,
            )
        choice = choose_target(frame, profile, question=question, requested=options.target_column)
        if choice.column is None:
            return EngineeringOutcome(
                FeatureEngineeringResult(
                    skipped_reason="No column can serve as a prediction target (it needs at least 20 "
                    "rows and two or more values), so features were not engineered.",
                    source="skipped", target_reason=choice.reason, warnings=choice.warnings,
                ),
                frame,
            )

        rules = propose_features(frame, profile, choice.column, limit=max(options.max_engineered * 2, 1))
        proposals: list[FeatureSpec] = []
        sources: dict[str, str] = {}
        source, model_used = "deterministic", None
        warnings = list(choice.warnings)
        if self.allow_model:
            plan, model_used, error = self._model_plan(frame, profile, choice.column, question, rules)
            if plan is not None:
                source = "model"
                proposals.extend(plan.features)
                sources.update({spec.name: "model" for spec in plan.features})
            else:
                source = "deterministic_fallback"
                warnings.append(f"The model's feature plan was not used ({error}); rule-based proposals ran instead.")

        seen = {spec.name for spec in proposals}
        proposals.extend(spec for spec in rules if spec.name not in seen)

        widened, created, rejected = apply_features(frame, proposals, profile, choice.column, source=sources)
        # Keep the strongest-priority features within the budget; extras are recorded, not silently dropped.
        if len(created) > options.max_engineered:
            overflow = created[options.max_engineered:]
            created = created[: options.max_engineered]
            dropped = {name for item in overflow for name in output_columns(item.spec)}
            widened = widened.drop([name for name in dropped if name in widened.columns])
            rejected.extend(
                RejectedFeature(name=item.spec.name, kind=item.spec.kind,
                                reason=f"over the budget of {options.max_engineered} engineered features")
                for item in overflow
            )
        engineered = {name for item in created for name in output_columns(item.spec)}
        return EngineeringOutcome(
            FeatureEngineeringResult(
                target_column=choice.column, target_reason=choice.reason, task=choice.task,
                source=source, model_used=model_used, created=created, rejected=rejected,
                warnings=warnings,
            ),
            widened,
            engineered,
        )

    def _model_plan(self, frame, profile, target, question, rules):
        layers = PromptBuilder().with_role("feature-engineer").complete()
        context = build_model_context(profile) if profile is not None else None
        layers.context = context.to_json(indent=2) if context is not None else json.dumps(
            {"columns": frame.columns}
        )
        layers.objective = json.dumps({"question": question, "target_column": target}, indent=2)
        layers.evidence = json.dumps(
            {
                "allowed_kinds": list(FEATURE_KINDS),
                "rule_based_proposals": [spec.model_dump() for spec in rules[:20]],
            },
            indent=2,
        )
        plan, response = self.client.complete_json(
            system=layers.system_message(), user=layers.user_message(), schema=FeaturePlan,
        )
        if plan is None:
            return None, response.model, response.error or "no plan"
        return plan, response.model, None


class _Explanation(BaseModel):
    explanation: str = Field(max_length=1200)


class FeatureSelectionAgent:
    def __init__(self, client: LLMClient | None = None, *, allow_model: bool = False) -> None:
        self.client = client or LLMClient.from_settings()
        self.allow_model = allow_model and self.client.enabled

    def run(
        self,
        engineering: EngineeringOutcome,
        profile: DatasetProfile | None,
        options: FeatureOptions,
        preprocessing: PreprocessingOptions,
    ):
        """Returns the result and the feature-set table (selected columns + target)."""
        result = engineering.result
        if result.skipped_reason or not result.target_column or not result.task:
            return FeatureSelectionResult(
                skipped_reason=result.skipped_reason or "No target was chosen, so nothing was selected.",
            ), None
        target, task = result.target_column, result.task
        # Scaling does not change mutual information or tree importance, but keeps
        # correlations and the exported feature set comparable across columns.
        matrix_options = preprocessing.model_copy(
            update={"scaling": preprocessing.scaling if preprocessing.scaling != "none" else "standard"}
        )
        matrix, y, origins, _notes = build_matrix(
            engineering.frame, profile, target, engineering.engineered_columns, matrix_options
        )
        if matrix.shape[1] == 0:
            return FeatureSelectionResult(
                skipped_reason="No usable feature columns remained after encoding.",
                target_column=target, task=task,
            ), None

        outcome = select_features(matrix, y, task, max_features=options.max_features, origins=origins)
        explanation, source = self._explain(target, task, outcome)
        feature_set = matrix[outcome.selected].copy()
        feature_set[target] = y.to_numpy()
        selection = FeatureSelectionResult(
            target_column=target, task=task, rows_used=outcome.rows_used,
            candidate_count=int(matrix.shape[1]), selected=outcome.selected, scores=outcome.scores,
            cross_validation=outcome.cross_validation, methods=list(METHODS),
            explanation=explanation, explanation_source=source,
            warnings=list(outcome.warnings),
        )
        return selection, feature_set

    def _explain(self, target, task, outcome) -> tuple[str, str]:
        top = [s for s in outcome.scores if s.selected][:5]
        cv = outcome.cross_validation
        parts = [
            f"The agent treated this as a {task} problem: predicting `{target}`.",
            f"Of {len(outcome.scores)} candidate features, {len(outcome.selected)} were kept.",
        ]
        if top:
            parts.append("The strongest were " + ", ".join(f"`{s.feature}`" for s in top) + ".")
        dropped = {}
        for score in outcome.scores:
            if not score.selected:
                key = score.status.split(" with ")[0]
                dropped[key] = dropped.get(key, 0) + 1
        if dropped:
            parts.append("Not kept: " + ", ".join(f"{count} {reason}" for reason, count in sorted(dropped.items())) + ".")
        if cv and cv.selected_mean is not None and cv.all_features_mean is not None:
            parts.append(
                f"Cross-validated {cv.metric} was {cv.selected_mean:.3f} with the selected set and "
                f"{cv.all_features_mean:.3f} with every candidate."
            )
        parts.append("Rankings describe association with the target in this data, not cause and effect.")
        fallback = " ".join(parts)
        if not self.allow_model:
            return fallback, "deterministic"
        layers = PromptBuilder().with_role("feature-selector").complete()
        layers.evidence = json.dumps(
            {
                "target": target, "task": task,
                "selected": [s.model_dump() for s in outcome.scores if s.selected][:15],
                "not_selected": [s.model_dump() for s in outcome.scores if not s.selected][:15],
                "cross_validation": cv.model_dump() if cv else None,
                "deterministic_summary": fallback,
            },
            indent=2,
        )
        reply, _ = self.client.complete_json(
            system=layers.system_message(), user=layers.user_message(), schema=_Explanation,
        )
        if reply is None or not reply.explanation.strip():
            return fallback, "deterministic"
        from insightflow_contracts.finding import banned_claim_phrases

        if banned_claim_phrases(reply.explanation):
            return fallback, "deterministic"  # causal or absolute wording is refused, not edited
        return reply.explanation.strip(), "model"
