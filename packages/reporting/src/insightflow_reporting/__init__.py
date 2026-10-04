"""Report rendering and export.

Renderers are deterministic and self-contained: the HTML has no external assets,
so a report can be emailed or archived without losing its formatting.
"""

from insightflow_reporting.bundle import (
    ReportBundle,
    build_bundle,
    embed_charts,
)
from insightflow_reporting.html import render_html, render_html_file
from insightflow_reporting.markdown import render_markdown

__all__ = [
    "ReportBundle",
    "build_bundle",
    "embed_charts",
    "render_html",
    "render_html_file",
    "render_markdown",
]