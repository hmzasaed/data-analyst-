"""`ReportComposer`: assemble a business-readable report from verified findings.

The composer may add *words*. It may not add *facts*. Every number, table and
chart reference in the report is copied from a verified finding, a computed metric
or a stored artifact, and anything unverified is either omitted or shown with a
visible "awaiting verification" banner.

Without a language model the composer still produces a complete, correct report -
just in plainer language. That is deliberate: the LLM improves readability, not
correctness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from insightflow_contracts import (
    AnomalyResult,
    ArtifactRef,
    ChartRequest,
    CleaningExecution,
    DatasetProfile,
    ExecutionResult,
    FeatureEngineeringResult,
    FeatureSelectionResult,
    Finding,
    FindingStatus,
    MetricValue,
    PreprocessingResult,
    ReportChart,
    ReportDocument,
    ReportKpi,
    ReportSection,
    ReportSectionKind,
    ReportTable,
    VerificationResult,
)
from pydantic import BaseModel

from insightflow_agents.llm import LLMClient
from insightflow_agents.prompts import PromptBuilder

COMPOSER_VERSION = "2"

#: Reading order of the report. Sections are numbered in this order.
SECTION_ORDER: tuple[ReportSectionKind, ...] = (
    ReportSectionKind.EXECUTIVE_SUMMARY,
    ReportSectionKind.DATASET_OVERVIEW,
    ReportSectionKind.DATA_QUALITY,
    ReportSectionKind.PREPROCESSING,
    ReportSectionKind.DESCRIPTIVE_STATISTICS,
    ReportSectionKind.DISTRIBUTIONS,
    ReportSectionKind.RELATIONSHIPS,
    ReportSectionKind.TRENDS_AND_SEGMENTS,
    ReportSectionKind.FEATURES,
    ReportSectionKind.KEY_FINDINGS,
    ReportSectionKind.ANOMALIES,
    ReportSectionKind.METHODOLOGY,
    ReportSectionKind.LIMITATIONS,
    ReportSectionKind.NEXT_STEPS,
    ReportSectionKind.REPRODUCIBILITY,
)

#: Where an analysis tool's chart belongs, by the tool's analysis type.
_CHART_SECTION_FOR_TYPE = {
    "trend": ReportSectionKind.TRENDS_AND_SEGMENTS,
    "comparison": ReportSectionKind.TRENDS_AND_SEGMENTS,
    "descriptive": ReportSectionKind.DISTRIBUTIONS,
    "distribution": ReportSectionKind.DISTRIBUTIONS,
    "relationship": ReportSectionKind.RELATIONSHIPS,
    "anomaly": ReportSectionKind.ANOMALIES,
}

#: Sections dropped from the report when they have nothing to show.
_OPTIONAL_SECTIONS = frozenset({
    ReportSectionKind.DESCRIPTIVE_STATISTICS,
    ReportSectionKind.DISTRIBUTIONS,
    ReportSectionKind.RELATIONSHIPS,
    ReportSectionKind.TRENDS_AND_SEGMENTS,
})

#: How many findings a business reader will tolerate in the executive summary.
MAX_HEADLINE_FINDINGS = 5


@dataclass
class ComposerInput:
    """Everything the composer is allowed to draw on."""

    job_id: str
    objective: str
    profile: DatasetProfile
    findings: list[Finding]
    verification: VerificationResult
    metrics: list[MetricValue] = field(default_factory=list)
    charts: list[ArtifactRef] = field(default_factory=list)
    tables: list[ArtifactRef] = field(default_factory=list)
    anomalies: list[AnomalyResult] = field(default_factory=list)
    cleaning: CleaningExecution | None = None
    execution_results: list[ExecutionResult] = field(default_factory=list)
    plan_objective: str = ""
    not_supported: list[str] = field(default_factory=list)
    source_filename: str | None = None
    is_exploratory: bool = False
    #: An `insightflow_visualization.eda.EdaResult`; typed loosely so agents stay free of plotting imports.
    eda: Any = None
    preprocessing: PreprocessingResult | None = None
    chart_requests: list[ChartRequest] = field(default_factory=list)
    feature_engineering: FeatureEngineeringResult | None = None
    feature_selection: FeatureSelectionResult | None = None
    feature_charts: list[dict] = field(default_factory=list)


class ReportComposer:
    version = COMPOSER_VERSION

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or LLMClient.from_settings()

    def compose(self, payload: ComposerInput) -> ReportDocument:
        verified, pending = self._partition(payload)

        sections: list[ReportSection] = [
            self._executive_summary(payload, verified, pending),
            self._dataset_overview(payload),
            self._data_quality(payload),
            self._key_findings(verified),
            self._anomalies(payload),
            self._methodology(payload),
            self._limitations(payload),
            self._reproducibility(payload),
            self._next_steps(payload, verified),
            self._preprocessing(payload),
            self._features(payload),
            self._exploratory(
                ReportSectionKind.DESCRIPTIVE_STATISTICS, "Descriptive statistics",
                "Summary statistics for every numeric measure and grouping column, computed on "
                "every analysed row.",
            ),
            self._exploratory(
                ReportSectionKind.DISTRIBUTIONS, "Distributions",
                "How the values of each column are spread: shape, centre, outliers and the most "
                "frequent categories.",
            ),
            self._exploratory(
                ReportSectionKind.RELATIONSHIPS, "Relationships between measures",
                "Pairwise correlations between numeric measures. A correlation describes how two "
                "columns move together; it is an association, not evidence that one affects the other.",
            ),
            self._exploratory(
                ReportSectionKind.TRENDS_AND_SEGMENTS, "Trends and segments",
                "How the headline measure changes over time and differs between groups.",
            ),
        ]
        sections = [section for section in sections if section is not None]
        self._attach_visuals(sections, payload)
        rank = {kind: index for index, kind in enumerate(SECTION_ORDER)}
        for section in sections:
            section.order = rank.get(section.kind, len(rank))
        sections = [
            section for section in sections
            if section.kind not in _OPTIONAL_SECTIONS or section.tables or section.charts
            or section.footer_markdown
        ]

        finding_ids = [finding.finding_id for finding in verified]
        artifact_ids = [
            *(
                artifact.artifact_id
                for artifact in [*payload.charts, *payload.tables]
            )
        ]
        return ReportDocument(
            job_id=payload.job_id,
            dataset_id=payload.profile.dataset_id,
            title=self._title(payload),
            objective=payload.objective,
            sections=sections,
            finding_ids=finding_ids,
            artifact_ids=artifact_ids,
            is_exploratory=payload.is_exploratory,
            generated_at=datetime.now(UTC).isoformat(),
            composer_model=self.client.settings.reasoning_model
            if self.client.enabled
            else "deterministic",
            report_version=COMPOSER_VERSION,
            source_filename=payload.source_filename,
            kpis=self._kpis(payload, verified),
        )

    # ----------------------------------------------------------- structure
    def _kpis(self, payload: ComposerInput, verified: list[Finding]) -> list[ReportKpi]:
        profile = payload.profile
        cells = profile.row_count * max(profile.column_count, 1)
        missing = sum(column.null_count for column in profile.columns)
        kpis = [
            ReportKpi(label="Rows", value=f"{profile.row_count:,}"),
            ReportKpi(label="Columns", value=f"{profile.column_count:,}"),
            ReportKpi(label="Missing cells", value=f"{missing / cells:.1%}" if cells else "0%",
                      note=f"{missing:,} of {cells:,}"),
            ReportKpi(label="Duplicate rows", value=f"{profile.duplicate_row_count:,}"),
            ReportKpi(label="Verified findings", value=f"{len(verified):,}"),
            *(
                [ReportKpi(
                    label="Selected features",
                    value=f"{len(payload.feature_selection.selected):,}",
                    note=f"of {payload.feature_selection.candidate_count:,} for {payload.feature_selection.target_column}",
                )]
                if payload.feature_selection and payload.feature_selection.selected
                else []
            ),
            ReportKpi(label="Charts", value=f"{len(payload.charts):,}"),
        ]
        if payload.preprocessing is not None:
            kpis.insert(4, ReportKpi(
                label="Preprocessing",
                value="applied" if payload.preprocessing.analysis_changed else "export only",
                note=f"{len(payload.preprocessing.steps)} logged step(s)",
            ))
        return kpis

    def _exploratory(self, kind: ReportSectionKind, heading: str, intro: str) -> ReportSection:
        # The intro is held back and only kept when there is something to introduce.
        return ReportSection(kind=kind, heading=heading, body_markdown=intro)

    def _attach_visuals(self, sections: list[ReportSection], payload: ComposerInput) -> None:
        """Place every exploratory and analysis chart or table in its section."""
        by_kind = {section.kind: section for section in sections}
        eda = payload.eda
        notes: dict[str, list[str]] = dict(eda.notes) if eda is not None else {}
        if eda is not None:
            for item in eda.tables:
                section = by_kind.get(item.section)
                if section is not None:
                    section.tables.append(item.table)
                    section.artifact_ids.append(item.table.source_artifact_id or "")
            for chart in eda.charts:
                section = by_kind.get(chart.section)
                if section is not None:
                    section.charts.append(ReportChart(
                        artifact_id=chart.artifact.artifact_id, title=chart.title,
                        caption=chart.caption, wide=chart.wide,
                    ))
                    section.artifact_ids.append(chart.artifact.artifact_id)

        # Charts from the registered analysis tools, grouped by what they analyse.
        rendered = {artifact.artifact_id for artifact in payload.charts}
        step_types = {
            result.step_id: _analysis_type(result.tool_name) for result in payload.execution_results
        }
        eda_heatmap = eda is not None and any(
            chart.artifact.artifact_id == "eda_correlation_heatmap" for chart in eda.charts
        )
        for request in payload.chart_requests:
            if request.chart_id not in rendered:
                continue
            if eda_heatmap and request.kind.value == "heatmap":
                # Same correlations, already shown annotated; the tool's table stays as evidence.
                continue
            kind = _CHART_SECTION_FOR_TYPE.get(
                step_types.get(request.analysis_step_id or "", ""),
                ReportSectionKind.TRENDS_AND_SEGMENTS,
            )
            section = by_kind.get(kind)
            if section is None:
                continue
            section.charts.append(ReportChart(
                artifact_id=request.chart_id, title=request.title,
                caption=" ".join(filter(None, [request.caption, request.caveat])) or None,
                # Tool charts are drawn at a full 9x5in figure size; at half width their
                # labels become illegible.
                wide=True,
            ))
            section.artifact_ids.append(request.chart_id)

        for section in by_kind.values():
            extra = notes.get(section.kind.value, [])
            if section.kind in _OPTIONAL_SECTIONS and not (section.tables or section.charts):
                # Keep an empty exploratory section only to say why it is empty.
                section.body_markdown = ""
                section.footer_markdown = "\n\n".join(extra)
            elif extra:
                section.footer_markdown = "\n\n".join(filter(None, [section.footer_markdown, *extra]))

    def _features(self, payload: ComposerInput) -> ReportSection | None:
        engineering, selection = payload.feature_engineering, payload.feature_selection
        kind = ReportSectionKind.FEATURES
        heading = "Feature engineering and selection"
        if engineering is None and selection is None:
            return None
        skipped = (engineering.skipped_reason if engineering else None) or (
            selection.skipped_reason if selection else None
        )
        if skipped:
            return ReportSection(kind=kind, heading=heading, body_markdown=skipped)

        source = {
            "model": f"proposed by a language model (`{engineering.model_used}`) and validated by code",
            "deterministic_fallback": "rule-based (the model's proposal was not usable)",
        }.get(engineering.source, "rule-based")
        lines = [
            "Two agents prepare the data for predictive modelling. This does not change any "
            "figure elsewhere in this report: engineered features exist only in the downloadable "
            "feature set.",
            "",
            f"- **Target:** `{engineering.target_column}` ({engineering.task}), "
            f"{engineering.target_reason}.",
            f"- **Feature engineering agent:** {len(engineering.created)} feature(s) created, "
            f"{len(engineering.rejected)} proposal(s) rejected; proposals were {source}.",
        ]
        if selection is not None:
            lines.append(
                f"- **Feature selection agent:** {len(selection.selected)} of "
                f"{selection.candidate_count} candidate(s) kept, ranked on {selection.rows_used:,} rows."
            )
            if selection.explanation:
                lines.extend(["", selection.explanation])
        section = ReportSection(
            kind=kind, heading=heading, body_markdown="\n".join(lines),
            warnings=[*(engineering.warnings or []), *((selection.warnings if selection else []) or [])],
        )
        if engineering.created:
            section.tables.append(ReportTable(
                title="Engineered features",
                columns=["Feature", "Kind", "Inputs", "Source", "Why"],
                rows=[[item.spec.name, item.spec.kind.replace("_", " "), ", ".join(item.spec.inputs),
                       item.source, item.spec.rationale] for item in engineering.created],
            ))
        if engineering.rejected:
            section.tables.append(ReportTable(
                title="Rejected proposals",
                columns=["Proposed feature", "Kind", "Reason"],
                rows=[[item.name, item.kind.replace("_", " "), item.reason] for item in engineering.rejected[:30]],
            ))
        if selection is not None and selection.scores:
            section.tables.append(ReportTable(
                title="Feature ranking",
                columns=["Rank", "Feature", "Origin", "Mutual information", "|Correlation|", "Importance", "Status"],
                rows=[[str(score.combined_rank or "-"), score.feature, score.origin,
                       _metric(score.mutual_information), _metric(score.correlation),
                       _metric(score.importance), score.status]
                      for score in selection.scores[:30]],
                numeric_columns=[0, 3, 4, 5],
                source_artifact_id="feature_scores",
                note=None if len(selection.scores) <= 30 else
                f"The top 30 of {len(selection.scores)} candidates are shown; the CSV has all of them.",
            ))
            cv = selection.cross_validation
            if cv is not None:
                section.tables.append(ReportTable(
                    title=f"Cross-validation ({cv.folds} folds, {cv.model})",
                    columns=["Feature set", "Features", f"Mean {cv.metric}", "Std dev"],
                    rows=[["All candidates", f"{cv.all_feature_count:,}", _metric(cv.all_features_mean),
                           _metric(cv.all_features_std)],
                          ["Selected", f"{cv.selected_feature_count:,}", _metric(cv.selected_mean),
                           _metric(cv.selected_std)]],
                    numeric_columns=[1, 2, 3],
                ))
        for chart in payload.feature_charts:
            section.charts.append(ReportChart(**chart))
            section.artifact_ids.append(chart["artifact_id"])
        if selection is not None and selection.methods:
            section.footer_markdown = "**Methods:** " + "; ".join(selection.methods) + "."
        return section

    def _preprocessing(self, payload: ComposerInput) -> ReportSection:
        result = payload.preprocessing
        kind = ReportSectionKind.PREPROCESSING
        if result is None:
            return ReportSection(kind=kind, heading="Data preprocessing",
                                 body_markdown="Preprocessing did not run for this job.")
        options = result.options
        analysis = [step for step in result.steps if step.scope == "analysis"]
        export = [step for step in result.steps if step.scope == "export"]
        applied = (
            f"**{len(analysis)} change(s) were applied**, and the dataset version advanced."
            if analysis
            else "**No changes were applied**, so the figures reflect the cleaned data."
        )
        lines = [
            "Preprocessing runs after cleaning and has two separate scopes:",
            "",
            "- **Analysed data** - optional changes to the data every figure in this report is "
            "computed from. " + applied,
            "- **Model-ready export** - a separate copy prepared for machine learning (scaled, "
            "encoded, with date features). It is offered as a download and does not feed any "
            "figure in this report.",
            "",
            f"**Settings:** missing numbers = `{options.missing_numeric}`, missing categories = "
            f"`{options.missing_categorical}`, outliers = `{options.outliers}`, scaling = "
            f"`{options.scaling}`, one-hot encoding = "
            f"`{'on' if options.encode_categoricals else 'off'}`, date features = "
            f"`{'on' if options.date_features else 'off'}`.",
        ]
        if result.rows_before != result.rows_after:
            lines.extend(["", f"**Rows analysed:** {result.rows_before:,} before preprocessing, "
                          f"{result.rows_after:,} after."])
        section = ReportSection(
            kind=kind, heading="Data preprocessing", body_markdown="\n".join(lines),
            warnings=list(result.warnings),
        )
        if analysis:
            section.tables.append(ReportTable(
                title="Changes to the analysed data",
                columns=["Step", "Column", "What was done", "Values / rows affected"],
                rows=[[step.action.replace("_", " "), step.column or "all", step.detail,
                       f"{step.rows_affected:,}"] for step in analysis],
                numeric_columns=[3],
            ))
            changed = sorted(set(result.missing_before) | set(result.missing_after))
            if changed:
                section.tables.append(ReportTable(
                    title="Missing values before and after",
                    columns=["Column", "Before", "After", "Change"],
                    rows=[
                        [name, f"{result.missing_before.get(name, 0):,}",
                         f"{result.missing_after.get(name, 0):,}",
                         f"{result.missing_after.get(name, 0) - result.missing_before.get(name, 0):+,}"]
                        for name in changed[:30]
                    ],
                    numeric_columns=[1, 2, 3],
                ))
        if export:
            section.tables.append(ReportTable(
                title="Model-ready export",
                columns=["Step", "Column", "What was done"],
                rows=[[step.action.replace("_", " "), step.column or "-", step.detail]
                      for step in export],
                note=(f"Result: {result.export.rows:,} rows x {result.export.columns:,} columns, "
                      "downloadable from the job's Preprocessing tab.") if result.export else None,
            ))
        if result.export and result.export.dropped_columns:
            section.tables.append(ReportTable(
                title="Columns left out of the model-ready export",
                columns=["Column", "Reason"],
                rows=[[item.column, item.reason] for item in result.export.dropped_columns[:40]],
            ))
        return section

    # --------------------------------------------------------------- sections
    def _title(self, payload: ComposerInput) -> str:
        name = payload.source_filename or "uploaded dataset"
        stem = name.rsplit(".", 1)[0].replace("_", " ").replace("-", " ").strip() or "Dataset"
        return f"Analysis of {stem}"

    def _partition(self, payload: ComposerInput) -> tuple[list[Finding], list[Finding]]:
        verified = [
            finding
            for finding in payload.findings
            if finding.status is FindingStatus.VERIFIED
            or finding.finding_id in payload.verification.verified_finding_ids
        ]
        pending = [
            finding
            for finding in payload.findings
            if finding.finding_id not in {f.finding_id for f in verified}
        ]
        return verified, pending

    def _executive_summary(
        self, payload: ComposerInput, verified: list[Finding], pending: list[Finding]
    ) -> ReportSection:
        ranked = _rank(verified)[:MAX_HEADLINE_FINDINGS]
        body = self._prose(payload, verified, ranked)

        lines = [body, ""]
        if ranked:
            lines.append("### Key findings")
            for finding in ranked:
                lines.append(
                    f"- **{finding.title}** — {finding.statement} "
                    f"(confidence: {finding.confidence.value})"
                )
        else:
            lines.append(
                "No finding cleared verification for this dataset, so no claim is made about "
                "its contents."
            )

        quality_caveat = self._worst_quality_caveat(payload)
        if quality_caveat:
            lines.extend(["", f"**Most important data-quality caveat:** {quality_caveat}"])

        if pending:
            lines.extend(
                [
                    "",
                    f"{len(pending)} candidate finding(s) did not clear verification and are "
                    "excluded from the conclusions below.",
                ]
            )

        return ReportSection(
            kind=ReportSectionKind.EXECUTIVE_SUMMARY,
            heading="Executive summary",
            body_markdown="\n".join(lines).strip(),
            finding_ids=[finding.finding_id for finding in ranked],
            order=0,
        )

    def _dataset_overview(self, payload: ComposerInput) -> ReportSection:
        profile = payload.profile
        coverage = profile.time_coverage
        lines = [
            f"**Source file:** {payload.source_filename or 'uploaded dataset'}",
            f"**Dimensions:** {profile.row_count:,} rows x {profile.column_count} columns",
            "**Column types:** "
            + ", ".join(
                f"{column.name} ({column.inferred_type.value})"
                for column in profile.columns[:12]
            ),
        ]
        if coverage and coverage.min and coverage.max:
            lines.append(
                f"**Time coverage:** {coverage.min} to {coverage.max}"
                + (f" ({coverage.span_days} days)" if coverage.span_days else "")
            )
        measures = [c for c in profile.columns if c.semantic_role.value == "measure"]
        if measures:
            lines.append("**Key measures:** " + ", ".join(c.name for c in measures[:8]))
        dimensions = [
            c
            for c in profile.columns
            if c.semantic_role.value in {"dimension", "geography", "category"}
        ]
        if dimensions:
            lines.append("**Grouping fields:** " + ", ".join(c.name for c in dimensions[:8]))

        pii = [c for c in profile.columns if c.pii_risk.value in {"medium", "high"}]
        if pii:
            lines.append(
                "**Personal data detected:** "
                + ", ".join(f"{c.name} ({c.pii_risk.value} risk)" for c in pii)
                + ". Values from these columns are not reproduced in this report."
            )

        return ReportSection(
            kind=ReportSectionKind.DATASET_OVERVIEW,
            heading="Dataset overview",
            body_markdown="\n".join(lines),
            order=1,
        )

    def _data_quality(self, payload: ComposerInput) -> ReportSection:
        profile = payload.profile
        lines: list[str] = []

        incomplete = [c for c in profile.columns if c.null_fraction > 0]
        if incomplete:
            lines.append("**Missing values**")
            for column in sorted(incomplete, key=lambda c: c.null_fraction, reverse=True)[:8]:
                lines.append(f"- `{column.name}`: {column.null_fraction:.1%} of rows")
            lines.append("")

        if profile.duplicate_row_count:
            lines.append(
                f"**Duplicate rows:** {profile.duplicate_row_count:,} exact duplicate rows were "
                "detected."
            )
            lines.append("")

        if profile.quality_issues:
            lines.append("**Other issues detected**")
            for issue in profile.quality_issues[:8]:
                target = f"`{issue.column}` " if issue.column else ""
                lines.append(f"- [{issue.severity.value}] {target}{issue.description}")
            lines.append("")

        if payload.cleaning and payload.cleaning.applied:
            lines.append("**Cleaning applied**")
            lines.append("")
            lines.append("| Change | Rows affected | Reason | Reversible |")
            lines.append("| --- | --- | --- | --- |")
            for entry in payload.cleaning.applied[:12]:
                column = f"`{entry.column}`" if entry.column else "table"
                reversible = "yes" if entry.reversible else "no (restore from raw)"
                reason = entry.reason.replace("|", "/")
                lines.append(
                    f"| {entry.kind.value} on {column} | {entry.rows_affected:,} | "
                    f"{reason[:110]} | {reversible} |"
                )
            lines.append("")
            rejected = payload.cleaning.rejected
            if rejected:
                lines.append(
                    f"{len(rejected)} proposed change(s) were **not** applied because they were "
                    "not approved."
                )
        elif not payload.cleaning:
            lines.append("No cleaning transformations were applied to this dataset.")

        if payload.profile.highest_pii_risk().value in {"medium", "high"}:
            lines.append("")
            lines.append(
                "Personal data was detected. Values from those columns are excluded from "
                "this report."
            )

        return ReportSection(
            kind=ReportSectionKind.DATA_QUALITY,
            heading="Data-quality assessment",
            body_markdown="\n".join(lines).strip(),
            order=2,
        )

    def _key_findings(self, verified: list[Finding]) -> ReportSection:
        if not verified:
            return ReportSection(
                kind=ReportSectionKind.KEY_FINDINGS,
                heading="Key findings",
                body_markdown=(
                    "No finding in this run was supported by a verified computed value, so this "
                    "section is intentionally empty. See the data-quality and limitations "
                    "sections for what was and was not analysed."
                ),
                order=3,
            )

        blocks: list[str] = []
        for finding in _rank(verified):
            evidence_lines = "\n".join(
                f"  - `{evidence.metric}` = {_render(evidence.value)}"
                + (f" {evidence.unit}" if evidence.unit else "")
                + (f" (compared with {evidence.comparator})" if evidence.comparator else "")
                for evidence in finding.evidence
            )
            block = [
                f"#### {finding.title}",
                "",
                finding.statement,
                "",
                f"**Evidence** ({finding.evidence[0].artifact_id})",
                evidence_lines,
                "",
                f"**Confidence:** {finding.confidence.value}  ",
                f"**Severity:** {finding.severity.value}",
            ]
            if finding.limitations:
                block.extend(["", "**Limitations**"])
                block.extend(f"- {limitation}" for limitation in finding.limitations)
            blocks.append("\n".join(block))

        return ReportSection(
            kind=ReportSectionKind.KEY_FINDINGS,
            heading="Key findings",
            body_markdown="\n\n".join(blocks),
            finding_ids=[finding.finding_id for finding in verified],
            order=3,
        )

    def _anomalies(self, payload: ComposerInput) -> ReportSection:
        if not payload.anomalies:
            return ReportSection(
                kind=ReportSectionKind.ANOMALIES,
                heading="Anomalies",
                body_markdown="No anomaly scan was run for this dataset.",
                order=4,
            )

        lines: list[str] = [
            "**A flagged record is unusual relative to a stated baseline. It is not evidence "
            "of an error, mistake or wrongdoing, and it may be entirely legitimate.**",
            "",
        ]
        for result in payload.anomalies:
            lines.append(
                f"### `{result.target_column}` - {result.method.value}"
            )
            lines.append("")
            lines.append(
                f"- Flagged: **{result.flagged_count:,}** of {result.evaluated_count:,} evaluated rows"
                + (f" ({result.flagged_count / max(result.evaluated_count, 1):.1%})")
            )
            if result.skipped_count:
                lines.append(f"- Not scored: {result.skipped_count:,} rows")
            lines.append(f"- Baseline: {result.anomalies[0].baseline.description if result.anomalies else 'n/a'}")
            for limitation in result.limitations:
                lines.append(f"- Limitation: {limitation}")
            if result.anomalies:
                lines.extend(["", "| Flagged value | Compared against | Severity | Why flagged |"])
                lines.append("| --- | --- | --- | --- |")
                for item in result.anomalies[:8]:
                    baseline = _baseline_cell(item)
                    reason = item.reason.replace("|", "/")
                    lines.append(
                        f"| {_fmt(item.observed_value)} | {baseline} | {item.severity.value} | "
                        f"{reason[:120]} |"
                    )
            lines.append("")

        lines.append("**Suggested review order:** start with high-severity flags, then check "
                     "whether the surrounding periods and groups corroborate them.")

        return ReportSection(
            kind=ReportSectionKind.ANOMALIES,
            heading="Anomalies",
            body_markdown="\n".join(lines).strip(),
            order=4,
        )

    def _methodology(self, payload: ComposerInput) -> ReportSection:
        lines = [
            "Every figure in this report was computed by a registered analysis tool and stored "
            "as an artifact before the report was written. No number was produced by a language "
            "model.",
            "",
            "**Tools used**",
            "",
        ]
        for result in payload.execution_results:
            if result.status != "succeeded":
                continue
            lines.append(
                f"- `{result.tool_name}` v{result.tool_version} on dataset version "
                f"{result.dataset_version} - {len(result.metrics)} metric(s), "
                f"{len(result.tables)} table(s)"
            )
        failed = [r for r in payload.execution_results if r.status == "failed"]
        if failed:
            lines.extend(["", "**Steps that did not complete**", ""])
            for result in failed:
                lines.append(
                    f"- `{result.tool_name}` (step `{result.step_id}`): {result.errors[0]}"
                )

        lines.extend([
            "",
            "**Exploratory analysis:** descriptive statistics, distributions, correlations, "
            "segment and trend views were computed with pandas and NumPy and drawn with seaborn "
            "and matplotlib. Charts over many rows use a fixed random sample, stated in each "
            "caption; tables use every row.",
        ])
        if payload.charts or payload.tables:
            lines.extend([
                "",
                f"This report includes **{len(payload.charts)} chart(s)** and "
                f"**{len(payload.tables)} evidence table(s)**. Each table cites the stored CSV "
                "that holds its unrounded values; the full list is in the appendix.",
            ])
        return ReportSection(
            kind=ReportSectionKind.METHODOLOGY,
            heading="Methodology",
            body_markdown="\n".join(lines).strip(),
            order=5,
        )

    def _limitations(self, payload: ComposerInput) -> ReportSection:
        lines: list[str] = []

        profile = payload.profile
        incomplete = [c for c in profile.columns if c.null_fraction > 0.2]
        if incomplete:
            lines.append(
                "**Missing fields:** "
                + ", ".join(f"`{c.name}` ({c.null_fraction:.0%})" for c in incomplete[:6])
                + ". Aggregates over these columns exclude missing rows, which can bias totals "
                "downward."
            )
        if profile.row_count < 30:
            lines.append(
                f"**Small sample:** the dataset has {profile.row_count:,} rows. Differences "
                "between groups are unstable and should not drive decisions on their own."
            )
        if payload.anomalies and any(r.evaluated_count < 30 for r in payload.anomalies):
            lines.append(
                "**Anomaly detection with limited history:** some scans had too few observations "
                "for a reliable baseline."
            )

        lines.append(
            "**No causal claims:** where this report describes relationships, they are "
            "associations observed in the data. Establishing cause would require a different "
            "study design."
        )

        rejected = payload.verification.rejected_finding_ids
        if rejected:
            lines.append(
                f"**Rejected claims:** {len(rejected)} candidate finding(s) failed verification "
                "and are excluded. They remain in the audit log for review."
            )
        if payload.verification.unsupported_claims:
            lines.append("")
            lines.append("**Verification notes**")
            lines.extend(f"- {note}" for note in payload.verification.unsupported_claims[:6])

        if payload.not_supported:
            lines.extend(["", "**Questions this dataset cannot answer**", ""])
            lines.extend(f"- {item}" for item in payload.not_supported[:8])

        return ReportSection(
            kind=ReportSectionKind.LIMITATIONS,
            heading="Limitations",
            body_markdown="\n".join(lines).strip(),
            order=6,
        )

    def _reproducibility(self, payload: ComposerInput) -> ReportSection:
        lines = [
            f"- **Job:** `{payload.job_id}`",
            f"- **Dataset:** `{payload.profile.dataset_id}`",
            f"- **Source file hash:** `{payload.profile.source_file_hash}`",
            f"- **Rows x columns:** {payload.profile.row_count:,} x {payload.profile.column_count}",
            f"- **Profiler version:** {payload.profile.profiler_version}",
            f"- **Report generated:** {datetime.now(UTC).isoformat()}",
            "",
            "Re-running the same file produces the same profile and the same computed metrics. "
            "Every table and chart in this report is stored as an artifact and can be "
            "downloaded alongside it.",
        ]
        return ReportSection(
            kind=ReportSectionKind.REPRODUCIBILITY,
            heading="Reproducibility",
            body_markdown="\n".join(lines),
            order=7,
        )

    def _next_steps(self, payload: ComposerInput, verified: list[Finding]) -> ReportSection:
        questions = _follow_up_questions(payload, verified)
        body = "Suggested next questions, based on what this dataset can answer:"
        body += "\n" + "\n".join(f"{index}. {q}" for index, q in enumerate(questions, start=1))
        return ReportSection(
            kind=ReportSectionKind.NEXT_STEPS,
            heading="Suggested next questions",
            body_markdown=body,
            order=8,
        )

    # ------------------------------------------------------------------ prose
    def _prose(self, payload: ComposerInput, verified: list[Finding], ranked: list[Finding]) -> str:
        """Executive-summary prose. Falls back to a deterministic opening."""
        fallback = self._deterministic_prose(payload, verified)
        if not self.client.enabled or not ranked:
            return fallback

        layers = PromptBuilder().with_role("report-composer").complete()
        layers.context = (
            f"Dataset: {payload.profile.row_count:,} rows x {payload.profile.column_count} columns."
            f" Question: {payload.objective}"
        )
        layers.objective = payload.objective
        layers.evidence = json.dumps(
            [
                {
                    "finding_id": finding.finding_id,
                    "statement": finding.statement,
                    "confidence": finding.confidence.value,
                    "limitations": finding.limitations,
                }
                for finding in ranked
            ],
            indent=2,
        )
        layers.style = "Two short paragraphs. Plain business language. No new numbers."

        summary, _ = self.client.complete_json(
            system=layers.system_message(),
            user=layers.user_message(),
            schema=ExecutiveSummary,
            complex_reasoning=True,
        )
        if summary is None or not summary.body.strip():
            return fallback
        return summary.body.strip()

    def _deterministic_prose(self, payload: ComposerInput, verified: list[Finding]) -> str:
        coverage = payload.profile.time_coverage
        period = ""
        if coverage and coverage.min and coverage.max:
            period = f" covering {coverage.min} to {coverage.max}"
        scope = "this exploratory overview" if payload.is_exploratory else payload.objective
        opening = (
            f"This report covers {payload.profile.row_count:,} rows and "
            f"{payload.profile.column_count} columns{period}. It was produced in response to "
            f"{_sentence(scope)}"
        )
        if not verified:
            return (
                f"{opening} No conclusion is stated, because no candidate finding could be "
                "supported by a verified computed value from this dataset."
            )
        high = len([f for f in verified if f.severity.value in {"medium", "high"}])
        return (
            f"{opening} {len(verified)} finding(s) were verified against computed values, "
            f"{high} of them at medium or high importance. Each finding below links to the "
            "artifact it came from."
        )

    def _worst_quality_caveat(self, payload: ComposerInput) -> str | None:
        worst = max(
            (c for c in payload.profile.columns if c.null_fraction > 0),
            key=lambda c: c.null_fraction,
            default=None,
        )
        if worst is None:
            if payload.profile.duplicate_row_count:
                return (
                    f"{payload.profile.duplicate_row_count:,} duplicate rows were detected; "
                    "totals may double-count if they were not removed."
                )
            return None
        return (
            f"`{worst.name}` is missing in {worst.null_fraction:.1%} of rows, so any figure "
            f"involving it is calculated on partial data."
        )


# --------------------------------------------------------------------- helpers
class ExecutiveSummary(BaseModel):
    """Model output for the summary prose: text only, no figures."""

    body: str


def _metric(value: float | None) -> str:
    return "-" if value is None else f"{value:.3f}"


def _analysis_type(tool_name: str) -> str:
    from insightflow_analytics import REGISTRY

    tool = REGISTRY.get(tool_name)
    spec = getattr(tool, "spec", None)
    return str(getattr(spec, "analysis_type", "") or "")


def _rank(findings: list[Finding]) -> list[Finding]:
    severity_order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    confidence_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        findings,
        key=lambda f: (severity_order.get(f.severity.value, 4), confidence_order.get(f.confidence.value, 3)),
    )


def _render(value) -> str:
    if value is None:
        return "not computed"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:,.4g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _baseline_cell(item) -> str:
    """A table cell describing what the value was compared against.

    Rolling and forest baselines have no single pair of bounds, so showing
    'n/a to n/a' would be misleading. The baseline centre is shown instead.
    """
    baseline = item.baseline
    if baseline.lower_bound is not None or baseline.upper_bound is not None:
        return f"{_fmt(baseline.lower_bound)} to {_fmt(baseline.upper_bound)}"
    if baseline.centre is not None:
        return f"trailing centre {_fmt(baseline.centre)}"
    return baseline.kind


def _sentence(text: str) -> str:
    """Add a full stop only when one is not already there."""
    stripped = text.strip()
    if not stripped:
        return "no specific question."
    return stripped if stripped[-1] in ".?!" else f"{stripped}."


def _fmt(value: float | None) -> str:
    if value is None:
        return "n/a"
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    return f"{value:,.4g}"


def _follow_up_questions(payload: ComposerInput, verified: list[Finding]) -> list[str]:
    questions: list[str] = []
    profile = payload.profile

    if profile.time_coverage:
        questions.append(
            "Break the change over time down by the largest grouping field to see which "
            "segment drove it."
        )
    dimensions = [
        c for c in profile.columns if c.semantic_role.value in {"dimension", "geography"}
    ]
    if dimensions and profile.row_count >= 30:
        questions.append(
            f"Compare `{dimensions[0].name}` across time rather than in total, to separate "
            "mix shift from volume change."
        )
    if any(f.tags and "anomaly" in f.tags for f in verified):
        questions.append(
            "Drill into the flagged periods and check whether an operational event "
            "(campaign, outage, pricing change) lines up with them."
        )
    incomplete = [c for c in profile.columns if c.null_fraction > 0.1]
    if incomplete:
        questions.append(
            f"Establish whether the missing values in `{incomplete[0].name}` are random or "
            "systematic, because that changes how the results should be read."
        )
    questions.append(
        "Add a target or prior-period column so the numbers can be judged against a goal "
        "rather than only against each other."
    )
    return questions[:5]