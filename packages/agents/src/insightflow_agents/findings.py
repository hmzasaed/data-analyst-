"""Turning computed results into evidence-linked findings.

A finding is only built here, from a `MetricValue` that a tool produced. There is
deliberately no way to construct a finding from free text alone: the report's
material claims are structurally incapable of existing without a computed value
behind them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from insightflow_contracts import (
    AnomalyResult,
    Confidence,
    Evidence,
    ExecutionResult,
    Finding,
    FindingStatus,
    MetricValue,
    Severity,
    ToolExecutionError,
    derive_id,
)

FINDING_VERSION = "1"

#: metric suffix -> how to phrase a finding from it
_METRIC_TEMPLATES: dict[str, tuple[str, Severity, Confidence]] = {
    "group_gap_mean": (
        "The gap between the highest and lowest group mean is {value}.",
        Severity.MEDIUM,
        Confidence.MEDIUM,
    ),
    "group_difference_p_value": (
        "The difference between the two highest-mean groups has a p-value of {value}.",
        Severity.LOW,
        Confidence.MEDIUM,
    ),
    "group_difference_cohens_d": (
        "The standardised difference between the two highest-mean groups is {value}.",
        Severity.LOW,
        Confidence.MEDIUM,
    ),
    "largest_group_mean": (
        "The highest group mean is {value}.",
        Severity.INFO,
        Confidence.HIGH,
    ),
    "top3_share": (
        "The top groups account for {value} of the total.",
        Severity.MEDIUM,
        Confidence.HIGH,
    ),
    "contribution_concentration": (
        "Contribution is concentrated: the leading groups hold {value} of the total.",
        Severity.MEDIUM,
        Confidence.HIGH,
    ),
    "worst_missing_column": (
        "The least complete column in this dataset is {value}.",
        Severity.MEDIUM,
        Confidence.HIGH,
    ),
    "worst_missing_share": (
        "That column has no usable values in {value} of rows.",
        Severity.MEDIUM,
        Confidence.HIGH,
    ),
    "worst_group_missing_share": (
        "Within the most affected group, {value} of cells are missing.",
        Severity.MEDIUM,
        Confidence.HIGH,
    ),
    "trend_slope_per_period": (
        "The fitted trend moves by {value} per period.",
        Severity.MEDIUM,
        Confidence.MEDIUM,
    ),
    "trend_r_squared": (
        "A straight line explains {value} of the period-to-period variation.",
        Severity.LOW,
        Confidence.MEDIUM,
    ),
    "period_count": (
        "The series covers {value} periods.",
        Severity.INFO,
        Confidence.HIGH,
    ),
"anomaly_rate_pct": (
        "{value} of evaluated rows were flagged as unusual.",
        Severity.MEDIUM,
        Confidence.MEDIUM,
    ),
    "strongest_correlation": (
        "The strongest measured association in the data is {value}.",
        Severity.LOW,
        Confidence.MEDIUM,
    ),
    "regression_r_squared": (
        "The predictor accounts for {value} of the variation in the response.",
        Severity.LOW,
        Confidence.LOW,
    ),
    "skewness": (
        "The distribution is skewed ({value}), so averages understate the spread.",
        Severity.LOW,
        Confidence.HIGH,
    ),
}


@dataclass
class FindingFactory:
    """Builds findings from execution results, always citing an artifact."""

    step_by_id: dict[str, ExecutionResult] = field(default_factory=dict)
    dataset_version: int = 1

    def from_execution(
        self,
        results: list[ExecutionResult],
        *,
        tool_titles: dict[str, str] | None = None,
    ) -> list[Finding]:
        findings: list[Finding] = []
        # Several steps can produce the same metric name. Where that happens, the
        # step id is added to the title so two findings are never shown as the
        # same claim with the same title.
        repeated = _repeated_metrics(results)
        for result in results:
            if result.status != "succeeded":
                continue
            primary_table = _artifact_for(result, None)
            tool_name = (tool_titles or {}).get(result.tool_name, result.tool_name)
            for metric in result.metrics:
                finding = self._from_metric(
                    metric,
                    result,
                    _artifact_for(result, metric.table) or primary_table,
                    tool_name,
                    disambiguate=metric.name in repeated,
                )
                if finding is not None:
                    findings.append(finding)
        return _dedupe_findings(findings)

    def _from_metric(
        self,
        metric: MetricValue,
        result: ExecutionResult,
        artifact_id: str | None,
        tool_name: str,
        *,
        disambiguate: bool = False,
    ) -> Finding | None:
        if not _is_notable(metric):
            return None
        template = _template_for(metric.name)
        if template is None:
            return None

        title, severity, confidence = template
        # The metric's own definition is rendered separately as evidence metadata;
        # appending it to the sentence made the statement hard to read.
        statement = title.format(value=_format(metric))

        evidence = Evidence(
            artifact_id=artifact_id or f"metric:{metric.name}",
            metric=metric.name,
            value=metric.value,
            unit=metric.unit,
            dataset_version=self.dataset_version,
        )
        return Finding(
            finding_id=derive_id(
                "find",
                {"step": result.step_id, "metric": metric.name, "value": metric.value},
                length=12,
            ),
            title=_title_for(metric, tool_name, result.step_id if disambiguate else ""),
            statement=statement,
            evidence=[evidence],
            severity=severity,
            confidence=confidence,
            limitations=list(result.warnings),
            status=FindingStatus.NEEDS_REVIEW,
            analysis_step_id=result.step_id,
            tags=_tags_for(metric.name, tool_name),
        )

    # --------------------------------------------------------------- anomalies
    def from_anomalies(self, results: list[AnomalyResult]) -> list[Finding]:
        """One finding per anomaly scan, plus a flag for the worst record."""
        findings: list[Finding] = []
        for result in results:
            count = result.flagged_count
            evaluated = result.evaluated_count
            method = result.method.value
            limitation = result.limitations[0] if result.limitations else ""

            summary = Finding(
                finding_id=derive_id(
                    "find",
                    {"anomaly": result.parameter_digest, "method": method},
                    length=12,
                ),
                title=f"{count} of {evaluated:,} rows flagged by {method}",
                statement=(
                    f"{method} flagged {count:,} of {evaluated:,} evaluated rows in "
                    f"'{result.target_column}' as unusual relative to the stated baseline. "
                    f"{count / evaluated * 100:.1f}% of the evaluated rows, flagged for review."
                    if evaluated
                    else f"{method} flagged {count:,} rows."
                ),
                evidence=[
                    Evidence(
                        artifact_id=f"anomaly:{result.parameter_digest}",
                        metric=f"{result.target_column}__anomalies_flagged",
                        value=count,
                        unit="records",
                        dataset_version=self.dataset_version,
                    ),
                    Evidence(
                        artifact_id=f"anomaly:{result.parameter_digest}",
                        metric=f"{result.target_column}__anomaly_rate_pct",
                        value=(count / evaluated * 100.0) if evaluated else None,
                        unit="%",
                        dataset_version=self.dataset_version,
                    ),
                ],
                severity=Severity.MEDIUM,
                confidence=Confidence.MEDIUM,
                limitations=list(result.limitations) or [limitation],
                status=FindingStatus.NEEDS_REVIEW,
                tags=["anomaly", "review"],
                flagged_for_review=True,
                anomaly_ids=[item.anomaly_id for item in result.anomalies[:10]],
            )
            findings.append(summary)

            if result.anomalies:
                worst = max(
                    result.anomalies,
                    key=lambda item: (item.severity.value, item.score or 0.0),
                )
                findings.append(
                    Finding(
                        finding_id=derive_id(
                            "find", {"anomaly_worst": worst.anomaly_id, "scan": method}, length=12
                        ),
                        title=f"Most extreme {method} flag: {worst.entity_label}",
                        statement=worst.reason,
                        evidence=[
                            Evidence(
                                artifact_id=f"anomaly:{result.parameter_digest}",
                                metric=f"{result.target_column}__observed_value",
                                value=worst.observed_value,
                                unit=result.target_column,
                                source_rows_or_groups=[worst.row_reference or worst.entity_label],
                                dataset_version=self.dataset_version,
                            )
                        ],
                        severity=worst.severity,
                        confidence=Confidence.LOW,
                        limitations=[worst.limitation, worst.caveat],
                        status=FindingStatus.NEEDS_REVIEW,
                        tags=["anomaly", "review"],
                        flagged_for_review=True,
                        anomaly_ids=[worst.anomaly_id],
                    )
                )
        return findings

    # -------------------------------------------------------------- failures
    def from_failures(self, results: list[ExecutionResult]) -> list[Finding]:
        """A failed step becomes a visible finding, not a silent gap."""
        findings: list[Finding] = []
        for result in results:
            if result.status != "failed":
                continue
            findings.append(
                Finding(
                    finding_id=derive_id(
                        "find", {"failed_step": result.step_id, "error": result.errors}, length=12
                    ),
                    title=f"Analysis step could not be completed: {result.tool_name}",
                    statement=(
                        f"The planned step '{result.step_id}' using '{result.tool_name}' did not "
                        f"complete. {result.errors[0] if result.errors else ''} No result was "
                        "produced, so nothing about it is claimed in this report."
                    ),
                    evidence=[
                        Evidence(
                            artifact_id=f"execution:{result.step_id}",
                            metric=f"{result.tool_name}__status",
                            value="failed",
                            dataset_version=result.dataset_version,
                        )
                    ],
                    severity=Severity.LOW,
                    confidence=Confidence.HIGH,
                    limitations=["This section of the analysis is missing from the report."],
                    status=FindingStatus.NEEDS_REVIEW,
                    tags=["failure", "transparency"],
                )
            )
        return findings


# --------------------------------------------------------------------- helpers
def _artifact_for(result: ExecutionResult, table_name: str | None) -> str | None:
    """Resolve the artifact a metric was derived from.

    Evidence tables are written as `<step_id>_<table name>.csv`, so a metric that
    names its table is matched to exactly that file. A metric that names no table
    falls back to the step's first table.
    """
    if not result.tables:
        return None
    if not table_name:
        return result.tables[0].artifact_id
    suffix = f"_{table_name}"
    for reference in result.tables:
        if reference.path.replace("\\", "/").split("/")[-1].startswith(f"{result.step_id}{suffix}"):
            return reference.artifact_id
    return result.tables[0].artifact_id


def _template_for(metric_name: str) -> tuple[str, Severity, Confidence] | None:
    if metric_name in _METRIC_TEMPLATES:
        return _METRIC_TEMPLATES[metric_name]
    leaf = metric_name.rsplit("__", 1)[-1]
    if leaf in _METRIC_TEMPLATES:
        return _METRIC_TEMPLATES[leaf]
    if leaf.endswith("_change_pct"):
        return (
            "The measure changed by {value} between the first and last period.",
            Severity.MEDIUM,
            Confidence.MEDIUM,
        )
    if leaf in {"first_period_total", "last_period_total"}:
        return ("The period total was {value}.", Severity.INFO, Confidence.HIGH)
    if leaf in {"median", "mean", "total"}:
        return ("The {value} value of this measure was computed.", Severity.INFO, Confidence.HIGH)
    if leaf == "observed_total":
        return (
            "The measure totals {value} across the analysed rows.",
            Severity.INFO,
            Confidence.HIGH,
        )
    if leaf == "top_share":
        return (
            "The leading group value accounts for {value} of the total.",
            Severity.MEDIUM,
            Confidence.HIGH,
        )
    return None


#: A share or rate of exactly zero means "nothing notable", which the data-quality
#: section already states. It is not a finding.
_ZERO_IS_UNNOTABLE = ("_share", "_pct", "_rate", "missing_share")


def _is_notable(metric: MetricValue) -> bool:
    if metric.value is None:
        return False
    if not isinstance(metric.value, int | float) or isinstance(metric.value, bool):
        return True
    return not (
        float(metric.value) == 0.0 and metric.name.endswith(_ZERO_IS_UNNOTABLE)
    )


def _title_for(metric: MetricValue, tool_name: str = "", step_id: str = "") -> str:
    """A readable title carrying its provenance.

    The producing tool is always named, and the step id too when more than one
    step produced a metric with this name, so two findings are never presented as
    the same claim under the same heading.
    """
    subject, _, leaf = metric.name.rpartition("__")
    readable = leaf.replace("_", " ")
    if subject:
        readable = f"{subject}: {readable}"
    provenance = ", ".join(part for part in (tool_name, step_id) if part)
    return f"{readable} ({provenance})" if provenance else readable


def _repeated_metrics(results: list[ExecutionResult]) -> set[str]:
    seen: dict[str, int] = {}
    for result in results:
        if result.status != "succeeded":
            continue
        for metric in result.metrics:
            seen[metric.name] = seen.get(metric.name, 0) + 1
    return {name for name, count in seen.items() if count > 1}


def _tags_for(metric_name: str, tool_name: str) -> list[str]:
    tags = [tag for tag in ("trend", "comparison", "quality", "anomaly", "distribution") if tag in metric_name]
    if tool_name:
        tags.append(tool_name)
    return tags


def _format(metric: MetricValue) -> str:
    value = metric.value
    if not isinstance(value, int | float):
        return str(value)
    if metric.unit == "%":
        return f"{value:.1f}%"
    unit = f" {metric.unit}" if metric.unit else ""
    if abs(value) >= 1000:
        return f"{value:,.0f}{unit}"
    if isinstance(value, float):
        return f"{value:,.2f}{unit}"
    return f"{value:,}{unit}"


def _dedupe_findings(findings: list[Finding]) -> list[Finding]:
    seen: dict[str, Finding] = {}
    for finding in findings:
        seen.setdefault(finding.finding_id, finding)
    return list(seen.values())


def raise_if_unusable(metric: MetricValue) -> None:  # pragma: no cover - guard helper
    if metric.value is None:
        raise ToolExecutionError("metric has no value", metric=metric.name, retryable=False)