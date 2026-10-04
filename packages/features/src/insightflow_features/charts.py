"""Report charts for feature selection, in the same seaborn theme as the EDA views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import pandas as pd
import seaborn as sns
from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    CrossValidation,
    FeatureScore,
    file_sha256,
)
from insightflow_visualization.eda import DPI, PALETTE, THEME

_STABLE_METADATA = {"Software": None, "Creation Time": None}
TOP_N = 20


@dataclass
class FeatureChart:
    artifact: ArtifactRef
    title: str
    caption: str
    wide: bool


def render_feature_charts(
    scores: list[FeatureScore], cv: CrossValidation | None, target: str, output_dir: Path
) -> list[FeatureChart]:
    charts: list[FeatureChart] = []
    with plt.rc_context(THEME):
        ranked = sorted((s for s in scores if s.combined_rank is not None), key=lambda s: s.combined_rank or 0)
        ranked = ranked[:TOP_N]
        if ranked:
            raw = pd.DataFrame(
                {
                    "Mutual information": [s.mutual_information for s in ranked],
                    "|Correlation|": [s.correlation for s in ranked],
                    "RF importance": [s.importance for s in ranked],
                },
                index=[f"{'[x]' if s.selected else '[ ]'} {_short(s.feature)}" for s in ranked],
            ).astype("float64")
            raw = raw.dropna(axis=1, how="all")
            # Each method on its own 0-1 scale, so one dominant feature does not hide the rest.
            scaled = raw / raw.max().replace(0, 1)
            figure, axes = plt.subplots(figsize=(7.8, max(3.2, 0.36 * len(raw) + 1.4)))
            sns.heatmap(scaled, cmap="Blues", vmin=0, vmax=1, annot=raw, fmt=".2f",
                        annot_kws={"fontsize": 7}, linewidths=0.6, linecolor="white",
                        cbar_kws={"label": "Relative to the best feature (0-1)", "shrink": 0.6}, ax=axes)
            axes.grid(False)
            axes.set_ylabel("")
            axes.xaxis.tick_top()
            axes.tick_params(axis="x", labelsize=8, length=0)
            for label, score in zip(axes.get_yticklabels(), ranked, strict=False):
                label.set_color("#17212b" if score.selected else "#8a96a3")
            charts.append(_save(figure, output_dir, "feature_importance",
                                f"Feature ranking for predicting {target}",
                                f"The {len(raw)} highest-ranked candidates ([x] = selected, darker label). Cells show each "
                                "method's raw score, shaded relative to the best feature for that method. "
                                "Scores describe association with the target in this data, not that a "
                                "feature affects it.", wide=True))
        if cv and cv.all_features_mean is not None and cv.selected_mean is not None:
            data = pd.DataFrame({
                "set": [f"All candidates ({cv.all_feature_count})", f"Selected ({cv.selected_feature_count})"],
                "score": [cv.all_features_mean, cv.selected_mean],
                "spread": [cv.all_features_std or 0.0, cv.selected_std or 0.0],
            })
            figure, axes = plt.subplots(figsize=(5.6, 3.4))
            axes.bar(data["set"], data["score"], yerr=data["spread"], capsize=6,
                     color=["#b8c2cc", PALETTE[0]], width=0.55)
            for position, value in enumerate(data["score"]):
                axes.annotate(f"{value:.3f}", (position, value), xytext=(0, 6), textcoords="offset points",
                              ha="center", fontsize=9)
            axes.set_ylabel(_metric_label(cv.metric))
            charts.append(_save(figure, output_dir, "feature_cv_comparison",
                                "Cross-validated score: all candidates vs selected",
                                f"Mean {_metric_label(cv.metric)} over {cv.folds} folds; bars show one standard "
                                "deviation. Higher is better.", wide=False))
    return charts


def _save(figure, output_dir: Path, chart_id: str, title: str, caption: str, *, wide: bool) -> FeatureChart:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{chart_id}.png"
    figure.tight_layout()
    figure.savefig(path, dpi=DPI, metadata=_STABLE_METADATA, facecolor="white")
    plt.close(figure)
    return FeatureChart(
        artifact=ArtifactRef(artifact_id=chart_id, kind=ArtifactKind.CHART_PNG, path=str(path),
                             byte_size=path.stat().st_size, sha256=file_sha256(path), mime_type="image/png"),
        title=title, caption=caption, wide=wide,
    )


def _metric_label(metric: str) -> str:
    return {"r2": "R²", "balanced_accuracy": "balanced accuracy"}.get(metric, metric)


def _short(text: str, limit: int = 34) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"
