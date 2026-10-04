"""Agent roles: planning, explaining, verifying and composing.

Every agent here is optional-by-design. With `llm.enabled = false` (the default)
each one still produces a correct, complete result through a deterministic path,
and the numbers never depend on a model at all.
"""

from insightflow_agents.cleaning_explainer import (
    CleaningExplainer,
    CleaningExplanation,
    OperationExplanation,
)
from insightflow_agents.composer import ComposerInput, ReportComposer
from insightflow_agents.context import ModelContext, build_model_context
from insightflow_agents.features import (
    EngineeringOutcome,
    FeatureEngineeringAgent,
    FeatureSelectionAgent,
)
from insightflow_agents.findings import FindingFactory
from insightflow_agents.llm import BASE_POLICY, UNTRUSTED_DATA_CLAUSE, LLMClient, LLMResponse
from insightflow_agents.planner import AnalysisPlanner, PlanningOutcome, detect_intent
from insightflow_agents.prompts import PromptBuilder, PromptLayers, load_prompt, prompts_dir
from insightflow_agents.verifier import (
    VerificationAgent,
    VerificationInput,
    mark_status,
)

__all__ = [
    "BASE_POLICY",
    "UNTRUSTED_DATA_CLAUSE",
    "AnalysisPlanner",
    "CleaningExplainer",
    "CleaningExplanation",
    "ComposerInput",
    "EngineeringOutcome",
    "FeatureEngineeringAgent",
    "FeatureSelectionAgent",
    "FindingFactory",
    "LLMClient",
    "LLMResponse",
    "ModelContext",
    "OperationExplanation",
    "PlanningOutcome",
    "PromptBuilder",
    "PromptLayers",
    "ReportComposer",
    "VerificationAgent",
    "VerificationInput",
    "build_model_context",
    "detect_intent",
    "load_prompt",
    "mark_status",
    "prompts_dir",
]