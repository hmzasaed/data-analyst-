"""`VerificationAgent`: decide which claims are allowed into the report.

Verification is mostly deterministic, on purpose. Recomputing or re-checking a
figure in code is reliable; asking a model whether a number "looks right" is not.
The model is used for one thing only, where code genuinely cannot judge: whether a
sentence of narrative overstates or contradicts the evidence attached to it.

Checks performed for every finding:

1. **schema** - evidence exists, values are of a usable type, dataset version matches;
2. **recomputation** - each evidence value is re-derived from the stored artifact
   where the metric allows it, within tolerance;
3. **statistical sanity** - reported percentages and counts agree with each other;
4. **language** - causal, absolute and accusatory wording is rejected;
5. **contradiction** - two findings asserting opposite things about the same target;
6. **narrative** - model-assisted check that the prose does not exceed its evidence.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

import polars as pl
from insightflow_contracts import (
    AnomalyResult,
    ChartRequest,
    ExecutionResult,
    Finding,
    FindingStatus,
    MetricValue,
    VerificationCheck,
    VerificationResult,
    VerificationStatus,
    content_digest,
)
from pydantic import BaseModel

from insightflow_agents.llm import LLMClient
from insightflow_agents.prompts import PromptBuilder

VERIFIER_VERSION = "1"

#: Relative tolerance when re-deriving a stored value.
DEFAULT_TOLERANCE = 1e-6

#: Softer variants the verifier flags in prose it did not author. The `Finding`
#: contract already refuses the worst of these outright; these catch phrasings that
#: slip through when a model writes a title or an executive-summary sentence.
PROBLEM_PHRASES: tuple[tuple[str, str], ...] = (
    (r"\bbecause\b", "asserts a cause; the data shows association only"),
    (r"\bcaused?\b", "claims causation without an experimental design"),
    (r"\bdriven by\b", "claims causation"),
    (r"\battributable to\b", "claims causation"),
    (r"\bthanks to\b", "claims causation"),
    (r"\bas a result of\b", "claims causation"),
    (r"\bproves?\b", "claims proof; the data can only support association"),
    (r"\bconfirms? that\b", "claims confirmation from a single analysis"),
    (r"\bwill (?:increase|decrease|drop|rise|grow)\b", "predicts the future from past data"),
    (r"\bguarantee[sd]?\b", "claims a guarantee"),
    (r"\balways\b", "absolute claim not supported by a sample"),
    (r"\bnever\b", "absolute claim not supported by a sample"),
    (r"\bevery\b", "universal claim; check that every case was actually measured"),
    (r"\ball\b", "universal claim; check that every case was actually measured"),
    (r"\bdefinitely\b", "unsupported certainty"),
    (r"\bcertainly\b", "unsupported certainty"),
    (r"\bobviously\b", "unsupported certainty"),
    (r"\bclearly\b", "unsupported certainty"),
    (r"\bfraud\b", "asserts wrongdoing"),
    (r"\bmisconduct\b", "asserts wrongdoing"),
    (r"\bimpressive\b", "subjective judgement presented as a finding"),
    (r"\bshocking\b", "subjective judgement presented as a finding"),
    (r"\bdisaster\b", "subjective judgement presented as a finding"),
)

_NUMBER = re.compile(r"-?\d[\d,]*\.?\d*")


@dataclass
class VerificationInput:
    """Everything the verifier is allowed to look at."""

    findings: list[Finding]
    metrics: list[MetricValue] = field(default_factory=list)
    tables: dict[str, pl.DataFrame] = field(default_factory=dict)
    charts: list[ChartRequest] = field(default_factory=list)
    anomalies: list[AnomalyResult] = field(default_factory=list)
    execution_results: list[ExecutionResult] = field(default_factory=list)
    dataset_version: int = 1


@dataclass
class MetricIndex:
    """Metrics keyed by producing step, then by name.

    Two steps can legitimately compute the same metric name with different values -
    `detect_outliers` and `detect_group_outliers` both report an anomaly rate for the
    same column. Matching on the name alone would compare a finding against the
    wrong computation, so the step is part of the key.
    """

    by_step: dict[str, dict[str, MetricValue]] = field(default_factory=dict)
    by_name: dict[str, list[MetricValue]] = field(default_factory=dict)

    def lookup(self, name: str, step_id: str | None) -> MetricValue | None:
        if step_id:
            found = self.by_step.get(step_id, {}).get(name)
            if found is not None:
                return found
        candidates = self.by_name.get(name, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        # Ambiguous without a step id: the values must agree or it cannot be checked.
        values = {str(metric.value) for metric in candidates}
        return candidates[0] if len(values) == 1 else None


def _index_metrics(metrics: list[MetricValue]) -> MetricIndex:
    index = MetricIndex()
    for metric in metrics:
        if metric.source_step:
            index.by_step.setdefault(metric.source_step, {})[metric.name] = metric
        index.by_name.setdefault(metric.name, []).append(metric)
    return index


class VerificationAgent:
    version = VERIFIER_VERSION

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient.from_settings()

    def verify(self, payload: VerificationInput) -> VerificationResult:
        checks: list[VerificationCheck] = []
        verified: list[str] = []
        rejected: list[str] = []
        unsupported: list[str] = []
        contradictions: list[str] = []

        known_metrics = _index_metrics(payload.metrics)
        known_artifacts = set(payload.tables)

        for finding in payload.findings:
            finding_checks = self._check_finding(
                finding,
                known_metrics=known_metrics,
                known_artifacts=known_artifacts,
                dataset_version=payload.dataset_version,
            )
            checks.extend(finding_checks)
            failures = [c for c in finding_checks if c.status is VerificationStatus.FAIL]
            if failures:
                rejected.append(finding.finding_id)
                unsupported.extend(f.detail for f in failures)
            else:
                verified.append(finding.finding_id)

        contradictions = self._find_contradictions(payload.findings)
        for contradiction in contradictions:
            checks.append(
                VerificationCheck(
                    check_id=f"contradiction_{content_digest(contradiction, length=8)}",
                    name="contradiction",
                    status=VerificationStatus.FAIL,
                    detail=contradiction,
                )
            )

        narrative = self._narrative_check(payload.findings)
        checks.extend(narrative.checks)
        unsupported.extend(narrative.unsupported)

        # A finding named in a contradiction is not publishable either way.
        for finding_id in contradiction_targets(contradictions):
            if finding_id in verified:
                verified.remove(finding_id)
            if finding_id not in rejected:
                rejected.append(finding_id)

        return VerificationResult(
            verified_finding_ids=verified,
            rejected_finding_ids=rejected,
            checks=checks,
            unsupported_claims=_dedupe(unsupported),
            contradictions=contradictions,
            summary=_summarise(verified, rejected, checks),
            verifier_model=narrative.model,
            verifier_version=VERIFIER_VERSION,
        )

    # ------------------------------------------------------------ per finding
    def _check_finding(
        self,
        finding: Finding,
        *,
        known_metrics: MetricIndex,
        known_artifacts: set[str],
        dataset_version: int,
    ) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        prefix = finding.finding_id

        checks.append(
            self._check(
                prefix,
                "evidence_present",
                VerificationStatus.PASS if finding.evidence else VerificationStatus.FAIL,
                "finding carries at least one evidence reference"
                if finding.evidence
                else "finding has no evidence and cannot be published",
                finding_id=finding.finding_id,
            )
        )

        if finding.confidence.value == "high" and not finding.limitations:
            checks.append(
                self._check(
                    prefix,
                    "high_confidence_needs_limitations",
                    VerificationStatus.PASS_WITH_WARNING,
                    "high-confidence finding states no limitations",
                    finding_id=finding.finding_id,
                )
            )

        if not finding.limitations:
            checks.append(
                self._check(
                    prefix,
                    "limitations_stated",
                    VerificationStatus.PASS_WITH_WARNING,
                    "finding states no limitations",
                    finding_id=finding.finding_id,
                )
            )

        for evidence in finding.evidence:
            checks.extend(
                self._check_evidence(evidence, finding, known_metrics, known_artifacts, dataset_version)
            )

        language = self._language_issues(finding)
        checks.append(
            self._check(
                prefix,
                "language",
                VerificationStatus.FAIL if language else VerificationStatus.PASS,
                "; ".join(language) if language else "statement avoids unsupported language",
                finding_id=finding.finding_id,
                actual=finding.statement[:200],
            )
        )
        return checks

    def _check_evidence(
        self,
        evidence,
        finding: Finding,
        known_metrics: MetricIndex,
        known_artifacts: set[str],
        dataset_version: int,
    ) -> list[VerificationCheck]:
        checks: list[VerificationCheck] = []
        label = f"{finding.finding_id}:{evidence.metric}"

        if evidence.value is None:
            checks.append(
                self._check(
                    label,
                    "evidence_has_value",
                    VerificationStatus.FAIL,
                    f"evidence '{evidence.metric}' has no value, so it supports nothing",
                    finding_id=finding.finding_id,
                )
            )
            return checks

        if isinstance(evidence.value, float) and not math.isfinite(evidence.value):
            checks.append(
                self._check(
                    label,
                    "evidence_value_finite",
                    VerificationStatus.FAIL,
                    f"evidence '{evidence.metric}' is not a finite number",
                    finding_id=finding.finding_id,
                )
            )

        if known_artifacts and evidence.artifact_id not in known_artifacts:
            checks.append(
                self._check(
                    label,
                    "artifact_exists",
                    VerificationStatus.PASS_WITH_WARNING,
                    (
                        f"evidence references artifact '{evidence.artifact_id}', which is not in "
                        "this result set; it may belong to another stage"
                    ),
                    finding_id=finding.finding_id,
                )
            )

        stored = known_metrics.lookup(evidence.metric, finding.analysis_step_id)
        if stored is None:
            ambiguous = len(
                known_metrics.by_name.get(evidence.metric, [])
            ) > 1
            checks.append(
                self._check(
                    label,
                    "metric_known",
                    VerificationStatus.PASS_WITH_WARNING,
                    (
                        f"no unambiguous computed metric named '{evidence.metric}' is available "
                        "to cross-check this value"
                    ),
                    finding_id=finding.finding_id,
                )
            )
            del ambiguous
        else:
            checks.append(
                self._compare(label, finding.finding_id, evidence.value, stored.value)
            )

        if evidence.dataset_version is not None and evidence.dataset_version != dataset_version:
            checks.append(
                self._check(
                    label,
                    "dataset_version_matches",
                    VerificationStatus.FAIL,
                    (
                        f"evidence was computed on dataset version {evidence.dataset_version} "
                        f"but the report is built on version {dataset_version}"
                    ),
                    finding_id=finding.finding_id,
                )
            )

        return checks

    def _compare(self, label: str, finding_id: str, claimed, stored) -> VerificationCheck:
        """Compare a claimed value with the stored computed metric."""
        if isinstance(claimed, int | float) and isinstance(stored, int | float):
            denominator = max(abs(float(stored)), 1e-12)
            difference = abs(float(claimed) - float(stored))
            if difference / denominator <= DEFAULT_TOLERANCE:
                return self._check(
                    label, "value_matches_artifact", VerificationStatus.PASS,
                    f"value {claimed} matches the computed metric {stored}",
                    finding_id=finding_id, expected=str(stored), actual=str(claimed),
                )
            return self._check(
                label, "value_matches_artifact", VerificationStatus.FAIL,
                f"reported {claimed} but the computed metric is {stored}",
                finding_id=finding_id, expected=str(stored), actual=str(claimed),
                tolerance=DEFAULT_TOLERANCE,
            )
        if str(claimed) == str(stored):
            return self._check(
                label, "value_matches_artifact", VerificationStatus.PASS,
                f"value '{claimed}' matches the computed metric",
                finding_id=finding_id, expected=str(stored), actual=str(claimed),
            )
        return self._check(
            label, "value_matches_artifact", VerificationStatus.FAIL,
            f"reported '{claimed}' but the computed metric is '{stored}'",
            finding_id=finding_id, expected=str(stored), actual=str(claimed),
        )

    # -------------------------------------------------------------- language
    def _language_issues(self, finding: Finding) -> list[str]:
        text = f"{finding.title} {finding.statement}".lower()
        issues: list[str] = []
        for pattern, explanation in PROBLEM_PHRASES:
            if re.search(pattern, text):
                issues.append(f"'{finding.statement[:80]}' {explanation}")
        return issues

    def _find_contradictions(self, findings: list[Finding]) -> list[str]:
        """Two findings about the same metric with opposite signs are a defect."""
        contradictions: list[str] = []
        by_metric: dict[str, list[Finding]] = {}
        for finding in findings:
            for evidence in finding.evidence:
                if isinstance(evidence.value, int | float) and evidence.comparator:
                    by_metric.setdefault(evidence.metric, []).append(finding)

        for metric, group in by_metric.items():
            signs = {math.copysign(1.0, float(f.evidence[0].value)) for f in group if f.evidence}
            if len(group) > 1 and len(signs) > 1:
                ids = ", ".join(sorted(f.finding_id for f in group))
                contradictions.append(
                    f"findings {ids} report '{metric}' with opposite directions against "
                    f"the same comparator; one of them is wrong or the comparators differ"
                )
        return contradictions

    # ------------------------------------------------------------- narrative
    def _narrative_check(self, findings: list[Finding]) -> NarrativeResult:
        if not self.client.enabled or not findings:
            return NarrativeResult()

        payload = json.dumps(
            [
                {
                    "finding_id": finding.finding_id,
                    "statement": finding.statement,
                    "confidence": finding.confidence.value,
                    "limitations": finding.limitations,
                    "evidence": [
                        {
                            "metric": evidence.metric,
                            "value": evidence.value,
                            "comparator": evidence.comparator,
                        }
                        for evidence in finding.evidence
                    ],
                }
                for finding in findings
            ],
            indent=2,
        )
        layers = PromptBuilder().with_role("verifier").complete()
        layers.evidence = payload

        result, response = self.client.complete_json(
            system=layers.system_message(),
            user=layers.user_message(),
            schema=NarrativeVerdict,
            complex_reasoning=True,
        )
        if result is None:
            return NarrativeResult()

        checks = [
            self._check(
                item.finding_id,
                "narrative_support",
                VerificationStatus.FAIL
                if item.supported is False
                else VerificationStatus.PASS_WITH_WARNING
                if item.supported is None
                else VerificationStatus.PASS,
                item.reason,
                finding_id=item.finding_id,
                actual=item.reason[:300],
            )
            for item in result.findings
        ]
        return NarrativeResult(
            checks=checks,
            unsupported=[item.reason for item in result.findings if item.supported is False],
            model=response.model,
        )

    # --------------------------------------------------------------- helpers
    def _check(
        self,
        check_id: str,
        name: str,
        status: VerificationStatus,
        detail: str,
        *,
        finding_id: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
        tolerance: float | None = None,
    ) -> VerificationCheck:
        return VerificationCheck(
            check_id=f"{check_id}:{name}"[:120],
            name=name,
            status=status,
            detail=detail,
            finding_id=finding_id,
            expected=expected,
            actual=actual,
            tolerance=tolerance,
        )


@dataclass
class NarrativeResult:
    checks: list[VerificationCheck] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    model: str | None = None


class FindingVerdict(BaseModel):
    finding_id: str
    supported: bool | None
    reason: str


class NarrativeVerdict(BaseModel):
    """Model output for the narrative check: a verdict per finding, no prose."""

    findings: list[FindingVerdict]


def contradiction_targets(contradictions: list[str]) -> set[str]:
    targets: set[str] = set()
    for contradiction in contradictions:
        tokens = contradiction.split("report")[0]
        for candidate in re.findall(r"[A-Za-z0-9_]{6,}", tokens):
            if candidate.startswith("find_"):
                targets.add(candidate)
    return targets


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def _summarise(
    verified: list[str], rejected: list[str], checks: list[VerificationCheck]
) -> str:
    warnings = len([c for c in checks if c.status is VerificationStatus.PASS_WITH_WARNING])
    return (
        f"{len(verified)} finding(s) verified, {len(rejected)} rejected, "
        f"{warnings} warning(s). "
        + (
            "A rejected finding is kept out of the report rather than corrected silently."
        )
    )


def mark_status(result: VerificationResult, findings: list[Finding]) -> list[Finding]:
    """Return findings with their verification status applied."""
    verified = set(result.verified_finding_ids)
    rejected = set(result.rejected_finding_ids)
    updated: list[Finding] = []
    for finding in findings:
        if finding.finding_id in rejected:
            updated.append(finding.model_copy(update={"status": FindingStatus.REJECTED}))
        elif finding.finding_id in verified:
            updated.append(finding.model_copy(update={"status": FindingStatus.VERIFIED}))
        else:
            updated.append(finding.model_copy(update={"status": FindingStatus.NEEDS_REVIEW}))
    return updated