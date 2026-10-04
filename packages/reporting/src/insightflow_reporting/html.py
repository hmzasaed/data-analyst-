"""HTML renderer.

The template is deliberately self-contained: no external CSS, fonts or scripts.
A report is often shared by email or as a downloaded file, and a report that
depends on a CDN silently loses its formatting or leaks the reader's IP address.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    ReportDocument,
    ReportSectionKind,
)
from jinja2 import Environment, StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup

_STYLE = """
:root {
  --ink: #17212b; --ink-2: #3b4754; --muted: #66727f; --line: #e3e8ee; --line-2: #d3dae2;
  --surface: #ffffff; --surface-2: #f6f8fa; --accent: #2f6fb3; --accent-soft: #e8f0fa;
  --warn-bg: #fff8e6; --warn-line: #e6c06a; --ok: #2e7d4f;
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; color: var(--ink); background: var(--surface-2);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.page { max-width: 72rem; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
.cover {
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 2rem 2rem 1.5rem; margin-bottom: 1.25rem;
}
.eyebrow { text-transform: uppercase; letter-spacing: .08em; font-size: .72rem; color: var(--accent); font-weight: 700; }
h1 { font-size: 1.95rem; line-height: 1.2; margin: .35rem 0 .5rem; letter-spacing: -.01em; }
.meta { color: var(--muted); font-size: .85rem; display: flex; flex-wrap: wrap; gap: .25rem 1.1rem; }
.meta code { font-size: .8rem; }
.question {
  margin: 1.1rem 0 0; padding: .8rem 1rem; background: var(--accent-soft);
  border-left: 4px solid var(--accent); border-radius: 0 8px 8px 0; font-size: .98rem;
}
.kpis {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(9.5rem, 1fr)); gap: .75rem;
  margin: 0 0 1.25rem;
}
.kpi { background: var(--surface); border: 1px solid var(--line); border-radius: 12px; padding: .85rem 1rem; }
.kpi .label { color: var(--muted); font-size: .74rem; text-transform: uppercase; letter-spacing: .06em; font-weight: 600; }
.kpi .value { font-size: 1.45rem; font-weight: 700; margin-top: .15rem; font-variant-numeric: tabular-nums; }
.kpi .note { color: var(--muted); font-size: .75rem; }
.layout { display: grid; grid-template-columns: 15rem minmax(0, 1fr); gap: 1.25rem; align-items: start; }
nav.toc {
  position: sticky; top: 1rem; background: var(--surface); border: 1px solid var(--line);
  border-radius: 12px; padding: 1rem 1rem .75rem; font-size: .86rem;
}
nav.toc h2 { font-size: .74rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 0 0 .5rem; border: 0; padding: 0; }
nav.toc ol { list-style: none; margin: 0; padding: 0; }
nav.toc li { margin: .15rem 0; }
nav.toc a { color: var(--ink-2); text-decoration: none; display: flex; gap: .5rem; padding: .2rem .35rem; border-radius: 6px; }
nav.toc a:hover { background: var(--surface-2); color: var(--accent); }
nav.toc .n { color: var(--muted); min-width: 1.4rem; font-variant-numeric: tabular-nums; }
.report-section {
  background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
  padding: 1.5rem 1.75rem 1.25rem; margin-bottom: 1.25rem; scroll-margin-top: 1rem;
}
.report-section > h2 {
  font-size: 1.28rem; margin: 0 0 1rem; padding-bottom: .6rem; border-bottom: 1px solid var(--line);
  display: flex; align-items: baseline; gap: .6rem;
}
.report-section > h2 .n {
  font-size: .8rem; color: var(--accent); background: var(--accent-soft); border-radius: 999px;
  padding: .1rem .55rem; font-variant-numeric: tabular-nums;
}
h3 { font-size: 1.05rem; margin: 1.5rem 0 .5rem; }
h4 { font-size: .97rem; margin: 1.25rem 0 .35rem; }
p { margin: .55rem 0; }
ul, ol { margin: .5rem 0; padding-left: 1.3rem; }
li { margin: .25rem 0; }
code { background: var(--surface-2); border: 1px solid var(--line); padding: .05rem .3rem; border-radius: 4px; font-size: .84em; }
blockquote { margin: .9rem 0; padding: .65rem 1rem; background: var(--surface-2); border-left: 3px solid var(--line-2); }
.table-block { margin: 1.25rem 0; }
.table-block h3 { margin: 0 0 .45rem; font-size: .98rem; }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 10px; }
table { border-collapse: collapse; width: 100%; font-size: .86rem; }
th, td { padding: .5rem .7rem; text-align: left; vertical-align: top; border-bottom: 1px solid var(--line); }
thead th {
  background: var(--surface-2); font-weight: 600; color: var(--ink-2); white-space: nowrap;
  position: sticky; top: 0;
}
tbody tr:last-child td { border-bottom: 0; }
tbody tr:nth-child(even) td { background: #fbfcfd; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.table-meta { color: var(--muted); font-size: .78rem; margin-top: .35rem; display: flex; flex-wrap: wrap; gap: .2rem 1rem; }
.section-body table { border: 1px solid var(--line); }
.charts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 1rem; margin: 1.25rem 0; }
figure.chart { margin: 0; border: 1px solid var(--line); border-radius: 10px; padding: .85rem; background: #fff; }
figure.chart.wide { grid-column: 1 / -1; }
figure.chart h3 { margin: 0 0 .5rem; font-size: .95rem; }
figure.chart img { width: 100%; height: auto; display: block; }
figure.chart figcaption { color: var(--muted); font-size: .8rem; margin-top: .5rem; line-height: 1.45; }
.callout {
  margin: 1rem 0; padding: .8rem 1rem; background: var(--warn-bg);
  border: 1px solid var(--warn-line); border-radius: 10px; font-size: .9rem;
}
.callout strong { display: block; margin-bottom: .2rem; }
.section-footer { color: var(--ink-2); font-size: .9rem; }
.muted { color: var(--muted); }
footer.report-footer { color: var(--muted); font-size: .82rem; padding: .5rem .25rem; }
@media (max-width: 900px) {
  .layout { grid-template-columns: 1fr; }
  nav.toc { position: static; }
  .charts { grid-template-columns: 1fr; }
}
@media (max-width: 560px) {
  .page { padding: 1rem .75rem 3rem; }
  .cover, .report-section { padding: 1.1rem 1rem; border-radius: 10px; }
  h1 { font-size: 1.5rem; }
}
@media print {
  body { background: #fff; font-size: 10.5pt; }
  .page { max-width: none; padding: 0; }
  .layout { display: block; }
  nav.toc { display: none; }
  .cover, .report-section, .kpi { border: 0; padding-left: 0; padding-right: 0; }
  .report-section { page-break-before: auto; }
  .report-section > h2 { page-break-after: avoid; }
  figure.chart, .table-block { page-break-inside: avoid; }
}
"""

#: Chart kinds are looked up in this map so the template stays free of logic.
_IMG_FOR_KIND = {ArtifactKind.CHART_PNG: "png", ArtifactKind.CHART_SVG: "svg"}


def _environment() -> Environment:
    """Jinja2 with autoescaping and sandboxing - report text is untrusted."""
    env = SandboxedEnvironment(
        undefined=StrictUndefined,
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["markdown"] = _markdown_filter
    return env


_TEMPLATE_NAME = "report.html.j2"


def _template_source() -> str:
    """Read the template that ships inside the package."""
    return (Path(__file__).resolve().parent / "templates" / _TEMPLATE_NAME).read_text(
        encoding="utf-8"
    )


def render_html(
    document: ReportDocument,
    *,
    charts: list[ArtifactRef] | None = None,
    tables: list[ArtifactRef] | None = None,
    chart_base_url: str = "/artifacts",
    embedded: dict[str, str] | None = None,
) -> str:
    """Render a report to a standalone HTML document.

    `embedded` maps chart artifact ids to data URIs, so a downloaded report still
    shows its charts without needing the artifact server.
    """
    env = _environment()
    template = env.from_string(_template_source())
    sources = _chart_sources(charts or [], chart_base_url, embedded or {})
    sections = document.ordered_sections()
    placed = {chart.artifact_id for section in sections for chart in section.charts}
    # Reports that place charts in sections omit unplaced ones on purpose (duplicates);
    # older documents with no placement show every chart at the end instead.
    loose = [
        {"artifact_id": artifact_id, "src": src}
        for artifact_id, src in sources.items()
        if not placed
    ]
    return template.render(
        document=document,
        # The stylesheet is a constant of this module, not report data; autoescaping
        # would turn its quotes and child selectors into entities and break it.
        style=Markup(_STYLE),  # noqa: S704
        sections=sections,
        sources=sources,
        loose_charts=loose,
        tables=tables or [],
        chart_count=len(sources),
        anomaly_kind=ReportSectionKind.ANOMALIES.value,
    )


def render_html_file(
    document: ReportDocument,
    destination: str | Path,
    **kwargs,
) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(document, **kwargs), encoding="utf-8")
    return path


def _chart_sources(
    charts: list[ArtifactRef], base_url: str, embedded: dict[str, str]
) -> dict[str, str]:
    """Artifact id -> image source, preferring an embedded data URI."""
    sources: dict[str, str] = {}
    for chart in charts:
        extension = _IMG_FOR_KIND.get(chart.kind)
        if extension is None:
            continue
        sources[chart.artifact_id] = (
            embedded.get(chart.artifact_id) or f"{base_url}/{chart.artifact_id}.{extension}"
        )
    return sources


# ---------------------------------------------------------------- markdown
_INLINE_CODE = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_ORDERED = re.compile(r"^\d+[.)]\s+(.*)$")
_UNORDERED = re.compile(r"^[-*]\s+(.*)$")
_TABLE_RULE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


def _inline(text: str) -> str:
    """Bold and inline code, on text that is already escaped."""
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _INLINE_CODE.sub(r"<code>\1</code>", text)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _markdown_filter(value: str) -> Markup:
    """Render the Markdown subset the composer emits: headings, lists, tables, quotes.

    Everything is escaped *before* any markup is added, and only a fixed set of
    tags is ever produced, so nothing from the dataset can inject HTML.
    A section heading is an <h2>, so body headings start at <h3>.
    """
    lines = html.escape(value or "", quote=False).splitlines()
    out: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            out.append("<p>" + " ".join(_inline(part) for part in paragraph) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index].rstrip()
        stripped = line.strip()
        if not stripped or stripped == "---":
            flush()
            index += 1
            continue
        heading = _HEADING.match(stripped)
        if heading:
            flush()
            level = min(max(len(heading.group(1)), 3), 4)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines) and _TABLE_RULE.match(lines[index + 1].strip()):
            flush()
            header = _cells(stripped)
            index += 2
            body: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                body.append(_cells(lines[index]))
                index += 1
            head = "".join(f"<th>{_inline(cell)}</th>" for cell in header)
            rows = "".join(
                "<tr>" + "".join(f"<td>{_inline(cell)}</td>" for cell in row) + "</tr>" for row in body
            )
            out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
            continue
        for pattern, tag in ((_UNORDERED, "ul"), (_ORDERED, "ol")):
            if pattern.match(stripped):
                flush()
                items: list[str] = []
                while index < len(lines):
                    current = lines[index].strip()
                    match = pattern.match(current)
                    if match:
                        items.append(match.group(1))
                    elif current and items and lines[index].startswith(("  ", "\t")):
                        items[-1] += " " + current
                    else:
                        break
                    index += 1
                out.append(f"<{tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + f"</{tag}>")
                break
        else:
            if stripped.startswith("&gt;"):
                flush()
                quoted: list[str] = []
                while index < len(lines) and lines[index].strip().startswith("&gt;"):
                    quoted.append(lines[index].strip()[4:].strip())
                    index += 1
                out.append("<blockquote>" + " ".join(_inline(q) for q in quoted) + "</blockquote>")
                continue
            # A line opening with a bold label ("**Dimensions:** ...") is its own paragraph.
            if paragraph and stripped.startswith("**"):
                flush()
            paragraph.append(stripped)
            if line.endswith("  "):
                flush()
            index += 1
            continue
        continue
    flush()
    # Safe by construction: every fragment above was escaped before tags were added.
    return Markup("\n".join(out))  # noqa: S704
