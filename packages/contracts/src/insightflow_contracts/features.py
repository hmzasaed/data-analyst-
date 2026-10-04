"""Feature engineering and feature selection contracts.

Both stages work on a modelling copy of the data and never change a figure in the
report: engineered columns exist only in the downloadable feature set, and the
analysis tools keep reading the analysed data.

The agents may *propose* features (deterministically or with a model). A proposal
is only a `FeatureSpec`; it becomes a column only after code checks that its kind
is registered, its inputs exist with the right types, and it does not use the
target - so a model cannot leak the answer into its own inputs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from insightflow_contracts.profile import StrictModel

FeatureKind = Literal[
    "log1p",           # log(1 + x) of a skewed, non-negative measure
    "ratio",           # a / b
    "difference",      # a - b
    "product",         # a * b (interaction)
    "square",          # x ** 2
    "date_ordinal",    # days since the earliest date
    "cyclical_month",  # sin and cos of the month
    "frequency",       # how often each category occurs
    "group_mean",      # mean of a measure within each category
    "missing_flag",    # 1 where the value was missing
]

FEATURE_KINDS: tuple[str, ...] = (
    "log1p", "ratio", "difference", "product", "square", "date_ordinal",
    "cyclical_month", "frequency", "group_mean", "missing_flag",
)

TaskType = Literal["regression", "classification"]


class FeatureOptions(StrictModel):
    """Per-job settings for the two feature stages."""

    enabled: bool = True
    #: Column to predict. `None` lets the agent choose (question, then data).
    target_column: str | None = None
    #: Upper bound on selected features.
    max_features: int = Field(default=15, ge=1, le=100)
    #: Upper bound on engineered candidates.
    max_engineered: int = Field(default=20, ge=0, le=100)


class FeatureSpec(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    kind: FeatureKind
    inputs: list[str] = Field(min_length=1, max_length=2)
    rationale: str = Field(default="", max_length=400)


class FeaturePlan(StrictModel):
    """What a model may return when asked to propose features."""

    target_column: str | None = None
    features: list[FeatureSpec] = Field(default_factory=list, max_length=60)
    rationale: str = Field(default="", max_length=800)


class EngineeredFeature(StrictModel):
    spec: FeatureSpec
    source: Literal["rule", "model"] = "rule"
    non_missing: int = Field(default=0, ge=0)
    mean: float | None = None
    std: float | None = None


class RejectedFeature(StrictModel):
    name: str
    kind: str
    reason: str


class FeatureEngineeringResult(StrictModel):
    skipped_reason: str | None = None
    target_column: str | None = None
    target_reason: str = ""
    task: TaskType | None = None
    source: Literal["deterministic", "model", "deterministic_fallback", "skipped"] = "deterministic"
    model_used: str | None = None
    created: list[EngineeredFeature] = Field(default_factory=list)
    rejected: list[RejectedFeature] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FeatureScore(StrictModel):
    feature: str
    origin: Literal["original", "engineered", "encoded"]
    mutual_information: float | None = None
    correlation: float | None = None
    importance: float | None = None
    combined_rank: int | None = None
    selected: bool = False
    #: Why it was dropped, or `selected`.
    status: str = "selected"


class CrossValidation(StrictModel):
    metric: str
    folds: int = Field(ge=2)
    all_features_mean: float | None = None
    all_features_std: float | None = None
    selected_mean: float | None = None
    selected_std: float | None = None
    all_feature_count: int = 0
    selected_feature_count: int = 0
    model: str = "RandomForest"


class FeatureSelectionResult(StrictModel):
    skipped_reason: str | None = None
    target_column: str | None = None
    task: TaskType | None = None
    rows_used: int = Field(default=0, ge=0)
    candidate_count: int = Field(default=0, ge=0)
    selected: list[str] = Field(default_factory=list)
    scores: list[FeatureScore] = Field(default_factory=list)
    cross_validation: CrossValidation | None = None
    artifact_id: str | None = None
    methods: list[str] = Field(default_factory=list)
    #: Plain-language summary by the selection agent (deterministic unless a model is enabled).
    explanation: str = ""
    explanation_source: Literal["deterministic", "model"] = "deterministic"
    warnings: list[str] = Field(default_factory=list)
