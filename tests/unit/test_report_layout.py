"""Exploratory analysis and the structured report: fidelity, determinism, safety."""

from __future__ import annotations

import polars as pl
import pytest
from insightflow_contracts import (
    PiiRisk,
    ReportChart,
    ReportDocument,
    ReportSection,
    ReportSectionKind,
    ReportTable,
)


@pytest.fixture
def eda(sales_frame, sales_profile, tmp_path):
    from insightflow_visualization.eda import run_eda

    return run_eda(sales_frame, sales_profile, tmp_path / "eda")


class TestExploratoryAnalysis:
    def test_core_views_are_produced(self, eda):
        charts = {chart.artifact.artifact_id for chart in eda.charts}
        tables = {table.artifact.artifact_id for table in eda.tables}
        assert {"eda_histograms", "eda_box_plots"} <= charts
        assert {"eda_column_overview", "eda_numeric_summary"} <= tables
        assert not eda.warnings

    def test_summary_table_agrees_with_its_csv_and_the_data(self, eda, sales_frame):
        import pandas as pd

        table = next(t for t in eda.tables if t.artifact.artifact_id == "eda_numeric_summary")
        stored = pd.read_csv(table.artifact.path).set_index("column")
        for column in stored.index:
            assert stored.loc[column, "mean"] == pytest.approx(sales_frame[column].mean())
            assert stored.loc[column, "max"] == pytest.approx(sales_frame[column].max())
        assert table.table.source_artifact_id == "eda_numeric_summary"

    def test_rendering_is_deterministic(self, sales_frame, sales_profile, tmp_path):
        from insightflow_visualization.eda import run_eda

        first = run_eda(sales_frame, sales_profile, tmp_path / "a")
        second = run_eda(sales_frame, sales_profile, tmp_path / "b")
        assert [c.artifact.sha256 for c in first.charts] == [c.artifact.sha256 for c in second.charts]
        assert [t.artifact.sha256 for t in first.tables] == [t.artifact.sha256 for t in second.tables]

    def test_personal_data_is_never_tabulated_by_value(self, sales_frame, sales_profile, tmp_path):
        from insightflow_visualization.eda import run_eda

        frame = sales_frame.with_columns(pl.lit("person@example.com").alias("contact"))
        profile = sales_profile.model_copy(deep=True)
        template = profile.columns[0].model_copy(
            update={"name": "contact", "pii_risk": PiiRisk.HIGH}
        )
        profile.columns.append(template)
        result = run_eda(frame, profile, tmp_path / "pii")
        for table in result.tables:
            if table.artifact.artifact_id == "eda_column_overview":
                continue  # lists the column's name and risk, never its values
            assert "person@example.com" not in str(table.table.rows)


class TestHtmlReport:
    def _document(self) -> ReportDocument:
        return ReportDocument(
            job_id="j", dataset_id="d", title="Report", objective="q",
            sections=[
                ReportSection(
                    kind=ReportSectionKind.DATA_QUALITY, heading="Quality", order=2,
                    body_markdown="### Heading\n\n- one\n- **two**\n\n| a | b |\n| --- | --- |\n| 1 | 2 |",
                    tables=[ReportTable(title="Stats", columns=["Column", "Mean"],
                                        rows=[["x", "1.5"]], numeric_columns=[1],
                                        source_artifact_id="eda_x")],
                    charts=[ReportChart(artifact_id="c1", title="A chart", caption="cap")],
                ),
                ReportSection(kind=ReportSectionKind.EXECUTIVE_SUMMARY, heading="Summary", order=0),
            ],
        )

    def test_markdown_blocks_become_real_html(self):
        from insightflow_reporting import render_html

        html = render_html(self._document())
        assert "<h3>Heading</h3>" in html
        assert "<li><strong>two</strong></li>" in html
        assert "<th>a</th>" in html and "<td>2</td>" in html

    def test_sections_are_numbered_in_order_with_inline_tables_and_charts(self):
        from insightflow_contracts import ArtifactKind, ArtifactRef
        from insightflow_reporting import render_html

        chart = ArtifactRef(artifact_id="c1", kind=ArtifactKind.CHART_PNG, path="c1.png")
        html = render_html(self._document(), charts=[chart], embedded={"c1": "data:image/png;base64,AA=="})
        assert html.index('id="executive_summary"') < html.index('id="data_quality"')
        assert 'class="num">1.5</td>' in html
        assert "eda_x.csv" in html
        assert 'src="data:image/png;base64,AA=="' in html
        assert "Additional charts" not in html

    def test_the_stylesheet_is_not_escaped(self):
        from insightflow_reporting import render_html

        html = render_html(self._document())
        assert ".report-section > h2" in html
        assert "&#34;Segoe UI&#34;" not in html

    def test_markdown_tables_cannot_inject_markup(self):
        from insightflow_reporting.html import _markdown_filter

        rendered = str(_markdown_filter("| <b>x</b> | y |\n| --- | --- |\n| <script>1</script> | z |"))
        assert "<script>" not in rendered and "&lt;script&gt;" in rendered
        assert "<b>" not in rendered
