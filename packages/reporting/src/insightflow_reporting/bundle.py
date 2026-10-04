"""Report rendering: HTML, Markdown and structured JSON."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    Finding,
    ReportDocument,
    VerificationResult,
)

from insightflow_reporting.html import render_html, render_html_file
from insightflow_reporting.markdown import render_markdown


@dataclass
class ReportBundle:
    """A report plus the artifacts needed to read it."""

    document: ReportDocument
    findings: list[Finding] = field(default_factory=list)
    verification: VerificationResult | None = None
    charts: list[ArtifactRef] = field(default_factory=list)
    tables: list[ArtifactRef] = field(default_factory=list)

    def to_markdown(self) -> str:
        return render_markdown(self.document, charts=self.charts, tables=self.tables)

    def to_html(self, **kwargs) -> str:
        return render_html(self.document, charts=self.charts, tables=self.tables, **kwargs)

    def to_dict(self) -> dict:
        """Machine-readable export: the full provenance record for a run."""
        return {
            "document": self.document.model_dump(mode="json"),
            "findings": [finding.model_dump(mode="json") for finding in self.findings],
            "verification": self.verification.model_dump(mode="json") if self.verification else None,
            "artifacts": {
                "charts": [chart.model_dump(mode="json") for chart in self.charts],
                "tables": [table.model_dump(mode="json") for table in self.tables],
            },
        }

    def write(self, directory: str | Path) -> dict[str, Path]:
        """Write html, markdown and json exports. Returns the paths written."""
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        written = {
            "html": render_html_file(
                self.document, target / "report.html", charts=self.charts, tables=self.tables
            ),
            "markdown": _write_text(target / "report.md", self.to_markdown()),
            "json": _write_text(
                target / "report.json", json.dumps(self.to_dict(), indent=2, default=str)
            ),
        }
        return written


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def embed_charts(chart_paths: list[ArtifactRef]) -> dict[str, str]:
    """Read chart images into data URIs for a self-contained HTML export."""
    import base64

    embedded: dict[str, str] = {}
    for chart in chart_paths:
        path = Path(chart.path)
        if not path.exists():
            continue
        mime = "image/svg+xml" if chart.kind is ArtifactKind.CHART_SVG else "image/png"
        payload = base64.b64encode(path.read_bytes()).decode("ascii")
        embedded[chart.artifact_id] = f"data:{mime};base64,{payload}"
    return embedded


def build_bundle(
    document: ReportDocument,
    *,
    findings: list[Finding] | None = None,
    verification: VerificationResult | None = None,
    charts: list[ArtifactRef] | None = None,
    tables: list[ArtifactRef] | None = None,
) -> ReportBundle:
    return ReportBundle(
        document=document,
        findings=findings or [],
        verification=verification,
        charts=charts or [],
        tables=tables or [],
    )