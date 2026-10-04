"""Anomaly contracts.

Every anomaly must be attributable to a named method, carry a baseline it was
compared against, and state its own caveat. `AnomalyFinding` rejects records
that call something fraud, an error, or misconduct - "flagged for review" is the
only claim the platform is allowed to make.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from insightflow_contracts.profile import Severity, StrictModel


class AnomalyMethod(StrEnum):
    IQR_FENCE = "iqr_fence"
    ROBUST_ZSCORE = "robust_zscore"
    ROLLING_MAD = "rolling_mad"
    GROUP_PERCENTILE = "group_percentile"
    ISOLATION_FOREST = "isolation_forest"
    RULE_BASED = "rule_based"
    ZEROVARIANCE_CHECK = "zerovariance_check"


#: Human-readable limitations, always surfaced next to the result.
METHOD_CAVEATS: dict[AnomalyMethod, str] = {
    AnomalyMethod.IQR_FENCE: (
        "Univariate fences miss anomalies that only look unusual in context, and "
        "assume a roughly symmetric distribution."
    ),
    AnomalyMethod.ROBUST_ZSCORE: (
        "Robust z-scores assume the bulk of the data is representative; a broad "
        "shift in the data can hide a genuine change."
    ),
    AnomalyMethod.ROLLING_MAD: (
        "Requires reliable time ordering and enough history; results change with "
        "the window size and are unstable for short series."
    ),
    AnomalyMethod.GROUP_PERCENTILE: (
        "Needs adequate group size; small groups are unreliable and are usually "
        "excluded rather than silently scored."
    ),
    AnomalyMethod.ISOLATION_FOREST: (
        "Multivariate method with limited business interpretability; scores depend "
        "on feature scaling and random seed."
    ),
    AnomalyMethod.RULE_BASED: (
        "Rules encode assumptions that may not hold in this business; false "
        "positives are expected."
    ),
    AnomalyMethod.ZEROVARIANCE_CHECK: (
        "Structural check only; it detects impossible variation, not unusual values."
    ),
}

ACCUSATORY_TERMS = (
    "fraud",
    "fraudulent",
    "scam",
    "theft",
    "stolen",
    "criminal",
    "misconduct",
    "corrupt",
    "money laundering",
    "laundering",
)


class BaselineSpec(StrictModel):
    """What the observed value was compared against."""

    kind: str = Field(description="e.g. overall, trailing_window, group_median")
    description: str
    centre: float | None = None
    lower_bound: float | None = None
    upper_bound: float | None = None
    scale: float | None = Field(default=None, description="MAD / IQR / std used")
    sample_size: int | None = Field(default=None, ge=0)


class AnomalyFinding(StrictModel):
    """One flagged record or group."""

    anomaly_id: str
    method: AnomalyMethod
    entity_type: str = Field(description="row, group, or timeseries_point")
    entity_label: str
    entity_columns: dict[str, str] = Field(default_factory=dict)
    observed_value: float | str | None = None
    baseline: BaselineSpec
    severity: Severity = Severity.MEDIUM
    score: float | None = Field(
        default=None, description="Signed deviation in baseline units; None for rule hits"
    )
    threshold: float | None = None
    reason: str
    limitation: str
    row_reference: str | None = None
    record_values: dict[str, str] = Field(default_factory=dict)
    data_quality_related: bool = Field(
        default=False,
        description="True when the flag is more likely a data-quality artefact than a business event.",
    )
    needs_domain_review: bool = True

    @model_validator(mode="after")
    def _no_accusatory_language(self) -> AnomalyFinding:
        blob = " ".join([self.reason, self.entity_label, *self.entity_columns.values()]).lower()
        hits = [term for term in ACCUSATORY_TERMS if term in blob]
        if hits:
            raise ValueError(
                "anomaly records must not assert wrongdoing; "
                f"found {hits}. Say 'flagged for review' instead."
            )
        return self

    @property
    def caveat(self) -> str:
        return (
            f"Flagged for review by {self.method.value}, not evidence of an error or wrongdoing. "
            f"{self.limitation}"
        )


class AnomalyResult(StrictModel):
    analysis_step_id: str | None = None
    method: AnomalyMethod
    dataset_version: int = Field(ge=1)
    target_column: str | None = None
    group_columns: list[str] = Field(default_factory=list)
    anomalies: list[AnomalyFinding] = Field(default_factory=list)
    evaluated_count: int = Field(default=0, ge=0)
    flagged_count: int = Field(default=0, ge=0)
    skipped_count: int = Field(default=0, ge=0)
    parameter_digest: str | None = None
    limitations: list[str] = Field(default_factory=list)
    method_version: str = "1"

    @model_validator(mode="after")
    def _counts_are_consistent(self) -> AnomalyResult:
        if self.flagged_count != len(self.anomalies):
            raise ValueError(
                f"flagged_count ({self.flagged_count}) must equal number of anomalies "
                f"({len(self.anomalies)})"
            )
        if not self.limitations:
            self.limitations = [METHOD_CAVEATS.get(self.method, "")]
        return self
