"""Deterministic, headless chart rendering and chart/table consistency checks."""

from insightflow_visualization.render import (
    PALETTE,
    ChartRenderer,
    RenderResult,
    render_charts,
)
from insightflow_visualization.verify import verify_chart_matches

__all__ = [
    "PALETTE",
    "ChartRenderer",
    "RenderResult",
    "render_charts",
    "verify_chart_matches",
]
