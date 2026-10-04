"""Exploratory data analysis for the report: statistics tables and seaborn charts.

The registered analysis tools answer the user's question. This module produces
the standard exploratory views every analytical report should open with -
column overview, descriptive statistics, distributions, correlations, segment
and trend views - using pandas, NumPy, seaborn and matplotlib.

The same rules as the rest of the platform apply:

* every table is computed here and written to a CSV artifact, and the report
  table cites it, so a figure in the report can be checked against stored output;
* charts are deterministic (fixed sampling seed, stable PNG metadata);
* columns flagged as personal data are never charted or tabulated by value.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import polars as pl
import seaborn as sns
from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    DatasetProfile,
    ReportSectionKind,
    ReportTable,
    SemanticRole,
    file_sha256,
)

EDA_VERSION = "1"

#: Charts are drawn from at most this many rows; tables use every row.
CHART_SAMPLE_ROWS = 20_000
SAMPLE_SEED = 0
MAX_MEASURES = 9
MAX_CATEGORICALS = 6
MAX_LEVELS_SHOWN = 12
DPI = 130

PALETTE = sns.color_palette("colorblind")
ACCENT = PALETTE[0]
_STABLE_METADATA = {"Software": None, "Creation Time": None}
THEME = {
    **sns.axes_style("whitegrid"),
    **sns.plotting_context("notebook", font_scale=0.85),
    "axes.edgecolor": "#c9d1d9",
    "grid.color": "#e6eaee",
    "axes.titleweight": "bold",
    "axes.titlesize": 10,
    "axes.titlelocation": "left",
}

_CATEGORICAL_ROLES = {
    SemanticRole.DIMENSION,
    SemanticRole.CATEGORY,
    SemanticRole.GEOGRAPHY,
    SemanticRole.FLAG,
}
_NOT_MEASURES = {SemanticRole.IDENTIFIER, SemanticRole.TIME, SemanticRole.FREE_TEXT}
#: Numeric codes that are labels, not quantities (`country_id`, `zip_code`).
_CODE_NAME = re.compile(r"(^|_)(id|code|key|zip|zipcode|postcode|postal_code)$")
#: Names that usually mark the headline business measure.
_HEADLINE_HINTS = ("revenue", "sales", "amount", "total", "profit", "gdp", "income", "value",
                   "price", "cost", "spend", "quantity", "units")


@dataclass
class EdaChart:
    artifact: ArtifactRef
    section: ReportSectionKind
    title: str
    caption: str
    wide: bool = True


@dataclass
class EdaTable:
    section: ReportSectionKind
    table: ReportTable
    artifact: ArtifactRef


@dataclass
class EdaResult:
    charts: list[EdaChart] = field(default_factory=list)
    tables: list[EdaTable] = field(default_factory=list)
    facts: dict[str, float | int | str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def note(self, section: ReportSectionKind, text: str) -> None:
        self.notes.setdefault(section.value, []).append(text)


# --------------------------------------------------------------------- entry
def run_eda(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    output_dir: str | Path,
) -> EdaResult:
    """Compute the exploratory tables and charts for `frame`."""
    builder = _EdaBuilder(frame, profile, Path(output_dir))
    with plt.rc_context(THEME):
        builder.build()
    return builder.result


class _EdaBuilder:
    def __init__(self, frame: pl.DataFrame, profile: DatasetProfile | None, output_dir: Path):
        self.frame = frame
        self.profile = profile
        self.columns = {c.name: c for c in (profile.columns if profile else [])}
        self.chart_dir = output_dir / "charts"
        self.table_dir = output_dir / "tables"
        self.result = EdaResult()
        self.measures = self._measures()
        self.categoricals = self._categoricals()
        self.time_column, self.time_is_year = self._time_column()
        self.primary = self._primary_measure()
        sampled = frame.height > CHART_SAMPLE_ROWS
        self.sample = frame.sample(n=CHART_SAMPLE_ROWS, seed=SAMPLE_SEED) if sampled else frame
        self.sample_note = (
            f" Drawn from a fixed random sample of {CHART_SAMPLE_ROWS:,} of {frame.height:,} rows."
            if sampled else ""
        )

    def build(self) -> None:
        steps = (
            self._column_overview,
            self._missing_values,
            self._numeric_summary,
            self._categorical_summary,
            self._histograms,
            self._box_plots,
            self._category_frequencies,
            self._correlations,
            self._segments,
            self._trend,
        )
        for step in steps:
            try:
                step()
            except Exception as exc:  # one failed view must not cost the whole report
                self.result.warnings.append(
                    f"Exploratory view '{step.__name__.strip('_')}' was skipped: {type(exc).__name__}."
                )
            finally:
                plt.close("all")
        self.result.facts.update(
            {
                "rows": self.frame.height,
                "columns": self.frame.width,
                "measures": len(self.measures),
                "categoricals": len(self.categoricals),
                "missing_cells": int(sum(self.frame.null_count().row(0))),
                "total_cells": int(self.frame.height * self.frame.width),
            }
        )

    # ---------------------------------------------------------- column roles
    def _is_personal(self, name: str) -> bool:
        column = self.columns.get(name)
        return column is not None and column.pii_risk.value in {"medium", "high"}

    def _measures(self) -> list[str]:
        names = []
        for name, dtype in self.frame.schema.items():
            column = self.columns.get(name)
            if not dtype.is_numeric() or self._is_personal(name):
                continue
            if column is not None and column.semantic_role in _NOT_MEASURES:
                continue
            if _CODE_NAME.search(name.lower()):
                continue
            if self.frame.get_column(name).drop_nulls().n_unique() < 2:
                continue
            names.append(name)
        return names

    def _categoricals(self) -> list[str]:
        names = []
        for name, dtype in self.frame.schema.items():
            column = self.columns.get(name)
            if self._is_personal(name) or dtype.is_numeric() or dtype.is_temporal():
                continue
            if column is not None and column.semantic_role not in _CATEGORICAL_ROLES:
                continue
            levels = self.frame.get_column(name).n_unique()
            if 2 <= levels <= 500:
                names.append(name)
        return names

    def _time_column(self) -> tuple[str | None, bool]:
        for name, dtype in self.frame.schema.items():
            if dtype.is_temporal():
                return name, False
        for name, dtype in self.frame.schema.items():
            column = self.columns.get(name)
            if column is not None and column.semantic_role is SemanticRole.TIME and dtype.is_numeric():
                values = self.frame.get_column(name).drop_nulls()
                if values.len() and _whole(values) and values.min() >= 1000 and values.max() <= 2999:
                    return name, True
        return None, False

    def _primary_measure(self) -> str | None:
        if not self.measures:
            return None

        def rank(name: str) -> tuple[int, int, int]:
            lowered = name.lower()
            hint = next((i for i, word in enumerate(_HEADLINE_HINTS) if word in lowered), len(_HEADLINE_HINTS))
            return (-hint, self.frame.get_column(name).count(), -self.measures.index(name))

        return max(self.measures, key=rank)

    # --------------------------------------------------------------- tables
    def _column_overview(self) -> None:
        rows, raw = [], []
        for column in (self.profile.columns if self.profile else [])[:80]:
            missing_pct = column.null_fraction * 100
            rows.append([
                column.name, column.inferred_type.value, column.semantic_role.value.replace("_", " "),
                f"{column.null_count:,}", f"{missing_pct:.1f}%", f"{column.unique_count:,}",
                column.pii_risk.value,
            ])
            raw.append({
                "column": column.name, "type": column.inferred_type.value,
                "role": column.semantic_role.value, "missing": column.null_count,
                "missing_pct": round(missing_pct, 4), "unique": column.unique_count,
                "pii_risk": column.pii_risk.value,
            })
        if rows:
            self._table(
                ReportSectionKind.DATASET_OVERVIEW, "eda_column_overview", "Column overview",
                ["Column", "Type", "Role", "Missing", "Missing %", "Unique values", "PII risk"],
                rows, pd.DataFrame(raw), numeric=[3, 4, 5],
                note="Types and roles are inferred from values and column names.",
            )

    def _missing_values(self) -> None:
        if not self.profile:
            return
        missing = sorted(
            (c for c in self.profile.columns if c.null_count),
            key=lambda c: (-c.null_fraction, c.name),
        )
        section = ReportSectionKind.DATA_QUALITY
        if not missing:
            self.result.note(section, "No column has missing values.")
            return
        raw = pd.DataFrame(
            {"column": [c.name for c in missing],
             "missing": [c.null_count for c in missing],
             "missing_pct": [round(c.null_fraction * 100, 4) for c in missing]}
        )
        self._table(
            section, "eda_missing_values", "Missing values by column",
            ["Column", "Missing", "Missing %"],
            [[c.name, f"{c.null_count:,}", f"{c.null_fraction:.1%}"] for c in missing[:30]],
            raw, numeric=[1, 2],
        )
        shown = raw.head(25)
        figure, axes = plt.subplots(figsize=(8, max(2.2, 0.32 * len(shown) + 1)))
        sns.barplot(data=shown, x="missing_pct", y="column", color=PALETTE[3], ax=axes)
        axes.set_xlabel("Missing (% of rows)")
        axes.set_ylabel("")
        axes.set_xlim(0, max(100.0, float(shown["missing_pct"].max())))
        for patch, value in zip(axes.patches, shown["missing_pct"], strict=False):
            axes.annotate(f"{value:.1f}%", (patch.get_width(), patch.get_y() + patch.get_height() / 2),
                          xytext=(4, 0), textcoords="offset points", va="center", fontsize=8)
        self._chart(figure, section, "eda_missing_values", "Missing values by column",
                    "Share of rows with no value, per column. Computed on the uploaded data before cleaning.",
                    wide=False)

    def _numeric_summary(self) -> None:
        if not self.measures:
            self.result.note(ReportSectionKind.DESCRIPTIVE_STATISTICS,
                             "No numeric measure column is available for descriptive statistics.")
            return
        data = self.frame.select(self.measures[:20]).to_pandas().astype("float64")
        described = data.describe(percentiles=[0.25, 0.5, 0.75]).T
        described["skew"] = data.skew()
        described["kurtosis"] = data.kurt()
        described["missing"] = data.isna().sum()
        described.index.name = "column"
        raw = described.reset_index()
        rows = [
            [row["column"], f"{int(row['count']):,}", _fmt(row["mean"]), _fmt(row["std"]), _fmt(row["min"]),
             _fmt(row["25%"]), _fmt(row["50%"]), _fmt(row["75%"]), _fmt(row["max"]),
             _fmt(row["skew"], 2), _fmt(row["kurtosis"], 2)]
            for _, row in raw.iterrows()
        ]
        self._table(
            ReportSectionKind.DESCRIPTIVE_STATISTICS, "eda_numeric_summary", "Numeric columns",
            ["Column", "Count", "Mean", "Std dev", "Min", "25%", "Median", "75%", "Max", "Skew", "Kurtosis"],
            rows, raw, numeric=list(range(1, 11)),
            note="Count excludes missing values. Skew above 1 or below -1 indicates a strongly lopsided distribution.",
        )

    def _categorical_summary(self) -> None:
        if not self.categoricals:
            return
        raw, rows = [], []
        for name in self.categoricals[:20]:
            series = self.frame.get_column(name).cast(pl.String)
            counts = _value_counts(series)
            top, top_count = (counts.row(0) if counts.height else ("-", 0))
            filled = series.count()
            share = top_count / filled if filled else 0.0
            raw.append({"column": name, "non_missing": filled, "unique": series.n_unique(),
                        "most_common": top, "most_common_count": top_count, "most_common_share": share})
            rows.append([name, f"{filled:,}", f"{series.drop_nulls().n_unique():,}", _shorten(str(top), 32),
                         f"{top_count:,}", f"{share:.1%}"])
        self._table(
            ReportSectionKind.DESCRIPTIVE_STATISTICS, "eda_categorical_summary", "Categorical columns",
            ["Column", "Non-missing", "Unique", "Most common", "Count", "Share"],
            rows, pd.DataFrame(raw), numeric=[1, 2, 4, 5],
        )

    # --------------------------------------------------------------- charts
    def _histograms(self) -> None:
        measures = self.measures[:MAX_MEASURES]
        if not measures:
            return
        data = self.sample.select(measures).to_pandas().astype("float64")
        figure, axes_grid = _grid(len(measures), height=2.7)
        for axes, name in zip(axes_grid, measures, strict=False):
            values = data[name].dropna()
            # A density curve over a handful of distinct values suggests values that do not exist.
            kde = values.nunique() > 10
            sns.histplot(values, bins=30, kde=kde, color=ACCENT, edgecolor="white", linewidth=0.4, ax=axes)
            axes.axvline(values.mean(), color=PALETTE[1], linewidth=1.3, label="mean")
            axes.axvline(values.median(), color=PALETTE[2], linewidth=1.3, linestyle="--", label="median")
            axes.set_title(_shorten(name, 34))
            axes.set_xlabel("")
            axes.set_ylabel("Rows")
        axes_grid[0].legend(frameon=False, fontsize=7, loc="upper right")
        self._chart(figure, ReportSectionKind.DISTRIBUTIONS, "eda_histograms",
                    "Distribution of numeric columns",
                    "Histograms with a smoothed density curve; solid line = mean, dashed = median."
                    + self.sample_note)

    def _box_plots(self) -> None:
        measures = self.measures[:MAX_MEASURES]
        if not measures:
            return
        data = self.sample.select(measures).to_pandas().astype("float64")
        figure, axes_grid = _grid(len(measures), height=1.6)
        for axes, name in zip(axes_grid, measures, strict=False):
            sns.boxplot(x=data[name].dropna(), color=PALETTE[0], fliersize=2, linewidth=0.9,
                        flierprops={"alpha": 0.5}, ax=axes)
            axes.set_title(_shorten(name, 34))
            axes.set_xlabel("")
        self._chart(figure, ReportSectionKind.DISTRIBUTIONS, "eda_box_plots",
                    "Spread and outliers",
                    "Box = middle 50% of values, line = median, whiskers = 1.5 x IQR; points beyond are "
                    "unusual relative to the column, not necessarily errors." + self.sample_note)

    def _category_frequencies(self) -> None:
        names = self.categoricals[:MAX_CATEGORICALS]
        if not names:
            return
        figure, axes_grid = _grid(len(names), height=2.9, columns=min(2, len(names)))
        for axes, name in zip(axes_grid, names, strict=False):
            counts = _value_counts(self.frame.get_column(name).cast(pl.String)).head(MAX_LEVELS_SHOWN)
            shown = counts.to_pandas()
            shown["label"] = shown["value"].map(lambda v: _shorten(str(v), 24))
            sns.barplot(data=shown, x="count", y="label", color=PALETTE[2], ax=axes)
            axes.set_title(_shorten(name, 34))
            axes.set_xlabel("Rows")
            axes.set_ylabel("")
        self._chart(figure, ReportSectionKind.DISTRIBUTIONS, "eda_category_frequencies",
                    "Most frequent categories",
                    f"Row counts for the {MAX_LEVELS_SHOWN} most frequent values of each grouping column, "
                    "computed on every row.")

    def _correlations(self) -> None:
        section = ReportSectionKind.RELATIONSHIPS
        measures = self.measures[:15]
        if len(measures) < 2:
            self.result.note(section, "Fewer than two numeric measures are available, so no correlations were computed.")
            return
        data = self.frame.select(measures).to_pandas().astype("float64")
        matrix = data.corr(method="pearson", min_periods=3)

        size = min(11.0, 1.5 + 0.62 * len(measures))
        figure, axes = plt.subplots(figsize=(size, size * 0.82))
        mask = np.triu(np.ones_like(matrix, dtype=bool), k=1)
        sns.heatmap(matrix, mask=mask, cmap="vlag", vmin=-1, vmax=1, center=0, square=True,
                    annot=len(measures) <= 12, fmt=".2f", annot_kws={"fontsize": 7},
                    linewidths=0.5, linecolor="white", cbar_kws={"shrink": 0.7, "label": "Pearson r"},
                    ax=axes)
        axes.grid(False)
        axes.set_xticklabels([_shorten(t.get_text(), 18) for t in axes.get_xticklabels()], rotation=40, ha="right")
        axes.set_yticklabels([_shorten(t.get_text(), 22) for t in axes.get_yticklabels()], rotation=0)
        self._chart(figure, section, "eda_correlation_heatmap", "Correlation matrix",
                    "Pearson correlation on rows where both values are present. Correlation measures "
                    "association only; it does not show that one column affects another.")

        pairs = []
        for i, left in enumerate(measures):
            for right in measures[i + 1:]:
                value = matrix.loc[left, right]
                if pd.notna(value):
                    both = int(data[[left, right]].dropna().shape[0])
                    pairs.append((left, right, float(value), both))
        pairs.sort(key=lambda item: (-abs(item[2]), item[0], item[1]))
        if not pairs:
            return
        raw = pd.DataFrame(pairs, columns=["column_a", "column_b", "pearson_r", "rows"])
        self._table(
            section, "eda_top_correlations", "Strongest correlations",
            ["Column A", "Column B", "Pearson r", "Strength", "Rows used"],
            [[a, b, f"{r:+.3f}", _strength(r), f"{n:,}"] for a, b, r, n in pairs[:10]],
            raw, numeric=[2, 4],
        )

        left, right, value, _ = pairs[0]
        sample = self.sample.select([left, right]).drop_nulls().to_pandas().astype("float64")
        if len(sample) >= 3:
            figure, axes = plt.subplots(figsize=(6.2, 4.4))
            sns.regplot(data=sample, x=left, y=right, ci=None, ax=axes,
                        scatter_kws={"s": 12, "alpha": 0.45, "color": ACCENT, "edgecolor": "none"},
                        line_kws={"color": PALETTE[1], "linewidth": 1.8})
            axes.set_xlabel(_shorten(left, 40))
            axes.set_ylabel(_shorten(right, 40))
            self._chart(figure, section, "eda_top_pair_scatter",
                        f"{_shorten(left, 30)} vs {_shorten(right, 30)}",
                        f"The most strongly correlated pair (r = {value:+.2f}), with a least-squares line."
                        + self.sample_note, wide=False)

    def _segments(self) -> None:
        if not self.primary or not self.categoricals:
            return
        section = ReportSectionKind.TRENDS_AND_SEGMENTS
        dimension = min(
            (n for n in self.categoricals if 2 <= self.frame.get_column(n).n_unique() <= 50),
            key=lambda n: (self.frame.get_column(n).null_count(), self.categoricals.index(n)),
            default=None,
        )
        if dimension is None:
            return
        measure = self.primary
        grouped = (
            self.frame.select([pl.col(dimension).cast(pl.String).fill_null("(missing)").alias("segment"),
                               pl.col(measure).cast(pl.Float64)])
            .group_by("segment")
            .agg(pl.len().alias("rows"), pl.col(measure).sum().alias("total"),
                 pl.col(measure).mean().alias("mean"), pl.col(measure).median().alias("median"))
            .sort(["total", "segment"], descending=[True, False])
        )
        overall = grouped["total"].sum()
        grouped = grouped.with_columns((pl.col("total") / overall * 100 if overall else pl.lit(None)).alias("share_pct"))
        raw = grouped.to_pandas()
        self._table(
            section, f"eda_segments_{_slug(dimension)}", f"{measure} by {dimension}",
            [dimension, "Rows", "Total", "Mean", "Median", "Share of total"],
            [[_shorten(str(r.segment), 32), f"{int(r.rows):,}", _fmt(r.total), _fmt(r.mean), _fmt(r.median),
              "-" if pd.isna(r.share_pct) else f"{r.share_pct:.1f}%"] for r in raw.head(20).itertuples()],
            raw, numeric=[1, 2, 3, 4, 5],
        )

        shown = raw.head(15).copy()
        shown["label"] = shown["segment"].map(lambda v: _shorten(str(v), 24))
        figure, axes = plt.subplots(figsize=(6.2, max(2.6, 0.34 * len(shown) + 1)))
        sns.barplot(data=shown, x="total", y="label", color=ACCENT, ax=axes)
        axes.set_xlabel(f"Total {_shorten(measure, 30)}")
        axes.set_ylabel("")
        self._chart(figure, section, f"eda_segment_totals_{_slug(dimension)}",
                    f"Total {_shorten(measure, 30)} by {_shorten(dimension, 24)}",
                    "Sum over every row in each group, largest first.", wide=False)

        top_levels = shown["segment"].head(10).tolist()
        sample = (
            self.sample.select([pl.col(dimension).cast(pl.String).fill_null("(missing)").alias("segment"),
                                pl.col(measure).cast(pl.Float64)])
            .filter(pl.col("segment").is_in(top_levels)).drop_nulls().to_pandas()
        )
        if len(sample) >= 5:
            sample["label"] = sample["segment"].map(lambda v: _shorten(str(v), 24))
            order = [_shorten(str(v), 24) for v in top_levels]
            figure, axes = plt.subplots(figsize=(6.2, max(2.6, 0.34 * len(order) + 1)))
            sns.boxplot(data=sample, x=measure, y="label", order=order, color=PALETTE[4],
                        fliersize=2, linewidth=0.9, ax=axes)
            axes.set_xlabel(_shorten(measure, 40))
            axes.set_ylabel("")
            self._chart(figure, section, f"eda_segment_spread_{_slug(dimension)}",
                        f"Spread of {_shorten(measure, 30)} within each {_shorten(dimension, 24)}",
                        "How values vary inside each group, for the largest groups." + self.sample_note,
                        wide=False)

    def _trend(self) -> None:
        if not self.time_column or not self.primary:
            return
        section = ReportSectionKind.TRENDS_AND_SEGMENTS
        name, measure = self.time_column, self.primary
        if self.time_is_year:
            period = pl.date(pl.col(name).cast(pl.Int32), 1, 1)
            grain = "year"
        else:
            stamps = self.frame.get_column(name).drop_nulls()
            if stamps.len() < 2:
                return
            span = (stamps.max() - stamps.min()).days if hasattr(stamps.max() - stamps.min(), "days") else 0
            grain, every = ("day", "1d") if span <= 62 else ("month", "1mo") if span <= 1100 else ("quarter", "1q")
            period = pl.col(name).cast(pl.Datetime).dt.truncate(every)
        series = (
            self.frame.select(period.alias("period"), pl.col(measure).cast(pl.Float64))
            .drop_nulls("period")
            .group_by("period").agg(pl.len().alias("rows"), pl.col(measure).sum().alias("total"))
            .sort("period")
        )
        if series.height < 2:
            return
        raw = series.to_pandas()
        raw["change_pct"] = raw["total"].pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan) * 100
        raw["rolling_mean_3"] = raw["total"].rolling(3, min_periods=1).mean()
        label = {"year": "%Y", "quarter": "%Y-%m", "month": "%Y-%m", "day": "%Y-%m-%d"}[grain]
        raw["label"] = pd.to_datetime(raw["period"]).dt.strftime(label)
        self._table(
            section, "eda_trend", f"{measure} per {grain}",
            [grain.title(), "Rows", f"Total {measure}", "Change vs previous"],
            [[r.label, f"{int(r.rows):,}", _fmt(r.total), "-" if pd.isna(r.change_pct) else f"{r.change_pct:+.1f}%"]
             for r in raw.tail(36).itertuples()],
            raw.drop(columns=["label"]), numeric=[1, 2, 3],
            note=None if len(raw) <= 36 else f"The latest 36 of {len(raw)} periods are shown; the CSV has all of them.",
        )
        figure, axes = plt.subplots(figsize=(10, 4.2))
        positions = np.arange(len(raw))
        axes.fill_between(positions, raw["total"], color=ACCENT, alpha=0.12)
        axes.plot(positions, raw["total"], color=ACCENT, linewidth=2, marker="o" if len(raw) <= 40 else None,
                  markersize=3.5, label=f"total per {grain}")
        axes.plot(positions, raw["rolling_mean_3"], color=PALETTE[1], linewidth=1.5, linestyle="--",
                  label="3-period rolling mean")
        step = max(1, len(raw) // 14)
        axes.set_xticks(positions[::step])
        axes.set_xticklabels(raw["label"].iloc[::step], rotation=40, ha="right")
        axes.set_ylabel(f"Total {_shorten(measure, 30)}")
        axes.legend(frameon=False, fontsize=8)
        self._chart(figure, section, "eda_trend", f"{_shorten(measure, 40)} over time",
                    f"Sum of {measure} per {grain} across every row, with a 3-period rolling mean. "
                    "The first and last periods may be partial.")

    # ------------------------------------------------------------- writers
    def _chart(self, figure, section: ReportSectionKind, chart_id: str, title: str,
               caption: str, *, wide: bool = True) -> None:
        self.chart_dir.mkdir(parents=True, exist_ok=True)
        path = self.chart_dir / f"{chart_id}.png"
        figure.tight_layout()
        figure.savefig(path, dpi=DPI, metadata=_STABLE_METADATA, facecolor="white")
        plt.close(figure)
        self.result.charts.append(EdaChart(
            artifact=ArtifactRef(
                artifact_id=chart_id, kind=ArtifactKind.CHART_PNG, path=str(path),
                byte_size=path.stat().st_size, sha256=file_sha256(path), mime_type="image/png",
            ),
            section=section, title=title, caption=caption, wide=wide,
        ))

    def _table(self, section: ReportSectionKind, table_id: str, title: str, columns: list[str],
               rows: list[list[str]], raw: pd.DataFrame, *, numeric: list[int] | None = None,
               note: str | None = None) -> None:
        self.table_dir.mkdir(parents=True, exist_ok=True)
        path = self.table_dir / f"{table_id}.csv"
        raw.to_csv(path, index=False, lineterminator="\n")
        artifact = ArtifactRef(
            artifact_id=table_id, kind=ArtifactKind.TABLE_CSV, path=str(path),
            row_count=len(raw), byte_size=path.stat().st_size, sha256=file_sha256(path),
            mime_type="text/csv",
        )
        self.result.tables.append(EdaTable(
            section=section,
            table=ReportTable(title=title, columns=columns, rows=rows, note=note,
                              source_artifact_id=table_id, numeric_columns=numeric or []),
            artifact=artifact,
        ))


# --------------------------------------------------------------------- helpers
def _grid(count: int, *, height: float, columns: int | None = None):
    columns = columns or min(3, count)
    rows = math.ceil(count / columns)
    figure, axes = plt.subplots(rows, columns, figsize=(3.6 * columns + 0.6, height * rows + 0.4), squeeze=False)
    flat = list(axes.flat)
    for spare in flat[count:]:
        spare.set_visible(False)
    return figure, flat[:count]


def _value_counts(series: pl.Series) -> pl.DataFrame:
    """Counts per value, most frequent first, ties alphabetical: stable across runs."""
    counts = series.drop_nulls().value_counts(sort=False, name="count")
    counts = counts.rename({counts.columns[0]: "value"})
    return counts.sort(["count", "value"], descending=[True, False])


def _whole(values: pl.Series) -> bool:
    if values.dtype.is_integer():
        return True
    return bool((values == values.floor()).all())


def _fmt(value, digits: int | None = None) -> str:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return "-"
    number = float(value)
    if digits is not None:
        return f"{number:,.{digits}f}"
    if number != 0 and abs(number) < 0.01:
        return f"{number:.3g}"
    if abs(number) >= 1000 or number.is_integer():
        return f"{number:,.0f}" if abs(number) >= 100_000 or number.is_integer() else f"{number:,.1f}"
    return f"{number:,.2f}"


def _strength(r: float) -> str:
    size = abs(r)
    label = "strong" if size >= 0.7 else "moderate" if size >= 0.4 else "weak" if size >= 0.2 else "negligible"
    if label == "negligible":
        return label
    return f"{label} {'positive' if r > 0 else 'negative'}"


def _shorten(text: str, limit: int = 28) -> str:
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower())[:40].strip("_") or "column"


__all__ = ["EDA_VERSION", "EdaChart", "EdaResult", "EdaTable", "run_eda"]
