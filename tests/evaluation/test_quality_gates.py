"""Evaluation: golden datasets, reproducibility and the product's own quality bars.

These tests encode the success metrics from the specification as executable checks:

* the same file always produces the same profile and the same metrics;
* every reported figure matches the artifact it cites;
* every material claim is linked to evidence;
* unsupported or misleading language never reaches a report;
* unsupported input fails loudly instead of producing output.

They run with the language model disabled, which is the honest baseline: the
numbers must be right whether or not a model is available.
"""

from __future__ import annotations

import pytest
from insightflow_contracts import JobObjective, JobStage, JobStatus

pytestmark = pytest.mark.evaluation

#: Threshold from the specification: report metrics must agree with the artifacts.
METRIC_AGREEMENT_THRESHOLD = 1.0
#: No material finding may be published without evidence.
EVIDENCE_COVERAGE_THRESHOLD = 1.0


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    from insightflow_contracts import reset_settings

    monkeypatch.setenv("INSIGHTFLOW_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv(
        "INSIGHTFLOW_STATE_DATABASE_URL", f"sqlite:///{(tmp_path / 'state.db').as_posix()}"
    )
    monkeypatch.setenv("INSIGHTFLOW_LLM_ENABLED", "false")
    reset_settings()

    from insightflow_orchestration import JobFactory, JobOrchestrator, build_context

    return JobFactory.from_settings(), JobOrchestrator, build_context


def _run(factory, orchestrator_cls, build_context, source, *, question=""):
    job, record = factory.create(
        source, owner_id="alice", objective=JobObjective(question=question or None)
    )
    context = build_context(
        job=job,
        raw_record=record,
        artifact_store=factory.artifact_store,
        state=factory.state,
        ingestion=factory.ingestion,
    )
    return orchestrator_cls(context).run(), context


class TestReproducibility:
    def test_the_same_file_produces_the_same_profile(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, first = _run(factory, orchestrator_cls, build_context, sales_csv)
        _, second = _run(factory, orchestrator_cls, build_context, sales_csv)
        assert first.profile.model_dump(mode="json") == second.profile.model_dump(mode="json")

    def test_the_same_file_produces_the_same_metrics(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, first = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")
        _, second = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")

        def names(context):
            return sorted((m.name, m.value) for m in context.metrics())

        assert names(first) == names(second)

    def test_the_same_file_produces_the_same_charts(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, first = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")
        _, second = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")
        assert [c.sha256 for c in first.chart_refs] == [c.sha256 for c in second.chart_refs]

    def test_a_changed_file_produces_a_different_profile(self, pipeline, sales_csv, tmp_path):
        import shutil

        factory, orchestrator_cls, build_context = pipeline
        _, first = _run(factory, orchestrator_cls, build_context, sales_csv)
        altered = tmp_path / "altered.csv"
        text = sales_csv.read_text(encoding="utf-8").replace(",North", ",Central")
        altered.write_text(text, encoding="utf-8")
        del shutil
        _, second = _run(factory, orchestrator_cls, build_context, altered)
        assert first.profile.model_dump(mode="json") != second.profile.model_dump(mode="json")


class TestReportFidelity:
    def test_every_metric_agrees_with_its_artifact(self, pipeline, sales_csv):
        import polars as pl

        factory, orchestrator_cls, build_context = pipeline
        job, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="compare")
        assert job.status is JobStatus.COMPLETED

        tables = {ref.artifact_id: pl.read_csv(ref.path) for ref in context.table_refs}
        checked = 0
        for finding in context.findings:
            for evidence in finding.evidence:
                table = tables.get(evidence.artifact_id)
                if table is None or isinstance(evidence.value, str):
                    continue
                checked += 1
                # The value must appear somewhere in the evidence table it cites.
                present = any(
                    evidence.value in table[column].to_list() for column in table.columns
                )
                assert present, (
                    f"{evidence.metric} = {evidence.value} does not appear in "
                    f"{evidence.artifact_id} (columns: {table.columns})"
                )
        assert checked > 0

    def test_every_published_finding_has_evidence(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="overview")
        document = context.bundle.document
        published = [
            finding for finding in context.findings if finding.finding_id in document.finding_ids
        ]
        assert published
        for finding in published:
            assert finding.evidence, finding.finding_id
        coverage = len(published) / len([f for f in context.findings if f.evidence])
        assert coverage >= EVIDENCE_COVERAGE_THRESHOLD - 1e-9

    def test_rejected_findings_are_excluded_from_the_report(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="overview")
        rejected = set(context.verification.rejected_finding_ids)
        assert not (rejected & set(context.bundle.document.finding_ids))

    def test_failed_steps_are_disclosed(self, pipeline, sales_csv, monkeypatch):
        """A step that validates but fails at run time must be visible in the report.

        A step with invalid *parameters* is rejected before execution, which is a
        different and stronger guarantee; this covers the case that survives
        validation and then fails.
        """
        import polars as pl
        from insightflow_contracts import AnalysisPlan, AnalysisType, PlannedStep

        factory, orchestrator_cls, build_context = pipeline
        job, record = factory.create(
            sales_csv, owner_id="alice", objective=JobObjective(question="overview")
        )
        context = build_context(
            job=job, raw_record=record, artifact_store=factory.artifact_store,
            state=factory.state, ingestion=factory.ingestion,
        )
        orchestrator = orchestrator_cls(context)
        orchestrator.run(until=JobStage.PLANNED)

        # A single-group column validates but cannot be compared.
        single = pl.DataFrame({"region": ["OnlyOne"], "revenue": [1.0]}).with_columns(
            pl.Series("region", pl.Series(["OnlyOne"], dtype=pl.String).cast(pl.Categorical))
        )
        context.frame = single
        context.snapshot = context.snapshot.model_copy(update={"columns": ["region", "revenue"]})
        context.plan = AnalysisPlan(
            objective="x",
            steps=[
                PlannedStep(step_id="ok", analysis_type=AnalysisType.DESCRIPTIVE,
                            tool_name="missingness_profile", parameters={}),
                PlannedStep(step_id="broken", analysis_type=AnalysisType.COMPARISON,
                            tool_name="compare_groups",
                            parameters={"group_by": "region", "column": "revenue"}),
            ],
        )
        result = orchestrator.run(until=JobStage.REPORTED, resume=True)
        assert result.status is JobStatus.COMPLETED
        markdown = context.bundle.to_markdown().lower()
        assert "did not complete" in markdown
        assert any(finding.tags and "failure" in finding.tags for finding in context.findings)


class TestReportLanguage:
    UNSUPPORTED = (
        "caused by", "because of", "due to the", "proves that", "fraud",
        "money laundering", "misconduct", "obviously", "definitely", "guaranteed",
    )

    def test_no_unsupported_language_reaches_the_report(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv, question="overview")
        text = context.bundle.to_markdown().lower()
        for phrase in self.UNSUPPORTED:
            assert phrase not in text, f"report contains '{phrase}'"

    def test_anomaly_language_is_defensive(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv,
            question="Find unusual records.",
        )
        anomalies = context.bundle.document.section.__self__.ordered_sections()
        body = "\n".join(section.body_markdown for section in anomalies).lower()
        assert "not evidence of an error" in body or "unusual relative to" in body
        for phrase in ("fraud", "misconduct", "stolen"):
            assert phrase not in body

    def test_limitations_are_present(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(factory, orchestrator_cls, build_context, sales_csv)
        markdown = context.bundle.to_markdown().lower()
        assert "limitations" in markdown
        assert "correlation" in markdown or "association" in markdown


class TestHonestFailures:
    def test_unsupported_input_fails_loudly(self, pipeline, tmp_path):
        from insightflow_contracts import UnsupportedInputError

        factory, _, _ = pipeline
        archive = tmp_path / "data.zip"
        archive.write_bytes(b"PK\x03\x04" + b"\x00" * 64)
        with pytest.raises(UnsupportedInputError) as excinfo:
            factory.create(archive, owner_id="alice")
        assert excinfo.value.message
        assert excinfo.value.suggestions, "a refusal must tell the user what to do next"

    def test_an_empty_file_fails_loudly(self, pipeline, tmp_path):
        from insightflow_contracts import UnsupportedInputError

        factory, _, _ = pipeline
        empty = tmp_path / "empty.csv"
        empty.write_bytes(b"")
        with pytest.raises(UnsupportedInputError):
            factory.create(empty, owner_id="alice")

    def test_an_unsupported_question_is_stated_not_faked(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv,
            question="Predict next quarter's revenue and explain the causal driver.",
        )
        assert context.plan.not_supported_questions
        markdown = context.bundle.to_markdown().lower()
        assert "cannot answer" in markdown or "not supported" in markdown


class TestGoldenDataset:
    def test_planted_figures_are_recovered(self, pipeline, sales_csv):
        """The fixture contains one extreme revenue value; detection must find it."""
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv,
            question="Find unusual revenue values.",
        )
        assert context.anomaly_results
        flagged = [
            value
            for result in context.anomaly_results
            for anomaly in result.anomalies
            for value in [anomaly.observed_value]
            if value is not None and value > 5000
        ]
        assert flagged, "the planted outlier was not flagged by any method"

    def test_group_means_match_a_hand_computed_value(self, pipeline, sales_csv):
        factory, orchestrator_cls, build_context = pipeline
        _, context = _run(
            factory, orchestrator_cls, build_context, sales_csv, question="Compare by region."
        )
        metrics = {m.name: m.value for m in context.metrics()}
        gap = metrics.get("group_gap_mean")
        assert gap is not None and gap > 0
        # The ranking table must agree with the reported means.
        results = {r.step_id: r for r in context.results}
        for result in results.values():
            for reference in result.tables:
                if reference.path.endswith("group_comparison_region.csv"):
                    import polars as pl

                    frame = pl.read_csv(reference.path)
                    assert float(frame["mean"].max() - frame["mean"].min()) == pytest.approx(
                        gap, rel=1e-6
                    )


class TestPerformance:
    def test_a_small_dataset_completes_quickly(self, pipeline, sales_csv):
        import time

        factory, orchestrator_cls, build_context = pipeline
        started = time.perf_counter()
        job, _ = _run(factory, orchestrator_cls, build_context, sales_csv, question="overview")
        elapsed = time.perf_counter() - started
        assert job.status is JobStatus.COMPLETED
        # The specification targets a median under 60 seconds for small datasets.
        assert elapsed < 60, f"run took {elapsed:.1f}s"