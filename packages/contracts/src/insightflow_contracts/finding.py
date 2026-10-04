"""`Finding` and its evidence links.

The central safety property of the whole platform: a material claim is only
representable if it carries at least one `Evidence` pointing at a stored artifact
and a computed value. `Finding` refuses to validate without evidence, so a report
composer cannot accidentally emit an unsupported sentence.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import Field, model_validator

from insightflow_contracts.profile import Severity, StrictModel


class Confidence(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"


class Evidence(StrictModel):
    """One computed value plus the artifact that produced it."""

    artifact_id: str
    metric: str
    value: float | str | int | bool | None = None
    unit: str | None = None
    comparator: str | None = Field(
        default=None,
        description="Optional comparison baseline, e.g. previous period or group median.",
    )
    source_rows_or_groups: list[str] = Field(default_factory=list)
    dataset_version: int | None = None


#: Wording that asserts cause, certainty or wrongdoing. This is enforced by the
#: contract rather than by review, because a claim built on association cannot be
#: rescued by careful phrasing later in the sentence. An analysis that genuinely
#: supports a causal statement must restate it descriptively.
#:
#: Patterns are matched with word boundaries, so "caused" is caught without also
#: matching unrelated words that happen to contain the same letters.
BANNED_CLAIM_PATTERNS: tuple[str, ...] = (
    # Causal connectors.
    r"\bcaused?\b",
    r"\bcausing\b",
    r"\bbecause\b",
    r"\bdue to\b",
    r"\bled to\b",
    r"\bled\b",
    r"\bresulted in\b",
    r"\bresulting in\b",
    r"\bdriven by\b",
    r"\bdrives\b",
    r"\bdrives?\b",
    r"\battributable to\b",
    r"\bthanks to\b",
    r"\bowing to\b",
    r"\bas a result of\b",
    r"\bstemming from\b",
    r"\bexplains? the\b",
    # Absolute certainty. Deliberately narrow: "every" and "all" appear in
    # perfectly honest descriptive text ("every analysed row"), so they are judged
    # by the verifier's contextual check rather than banned here. These words are
    # always assertions about the world, never about the computation.
    r"\bproves?\b",
    r"\bproven\b",
    r"\bconfirms? that\b",
    r"\bobviously\b",
    r"\bdefinitely\b",
    r"\bcertainly\b",
    r"\bguarantees?\b",
    r"\balways\b",
    r"\bnever\b",
    # Accusatory framing.
    r"\bfraud(ulent)?\b",
    r"\bmoney laundering\b",
    r"\bcriminal\b",
    r"\bstolen\b",
    r"\btheft\b",
    r"\bcorrupt(ion|ed)?\b",
    r"\bmisconduct\b",
)

_COMPILED_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (pattern, re.compile(pattern, flags=re.IGNORECASE)) for pattern in BANNED_CLAIM_PATTERNS
)


def banned_claim_phrases(text: str) -> list[str]:
    """Return the banned patterns present in `text`."""
    lowered = text.lower()
    return [pattern for pattern, compiled in _COMPILED_CLAIM_PATTERNS if compiled.search(lowered)]


class Finding(StrictModel):
    finding_id: str
    title: str
    statement: str
    evidence: list[Evidence] = Field(min_length=1)
    severity: Severity = Severity.INFO
    confidence: Confidence = Confidence.MEDIUM
    limitations: list[str] = Field(default_factory=list)
    status: FindingStatus = FindingStatus.NEEDS_REVIEW
    analysis_step_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    flagged_for_review: bool = False
    anomaly_ids: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_causal_language(self) -> Finding:
        """Refuse causal, absolute or accusatory wording outright."""
        hits = banned_claim_phrases(f"{self.title} {self.statement}")
        if hits:
            raise ValueError(
                "unsupported causal, absolute or accusatory language in finding: "
                f"{hits}. Restate descriptively and attach evidence."
            )
        return self

    @property
    def is_publishable(self) -> bool:
        return self.status is FindingStatus.VERIFIED and bool(self.evidence)

    def headline_value(self) -> float | str | int | bool | None:
        return self.evidence[0].value if self.evidence else None
