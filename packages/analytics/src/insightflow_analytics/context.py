"""`AnalysisContext`: everything a registered tool is allowed to touch.

A tool receives the context and validated parameters, and returns a `ToolOutcome`.
It cannot reach the filesystem, the network or the language model. Every table it
produces is written through the context's artifact sink, so outputs are hashed and
referenced by id in the report rather than pasted into prose.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl
from insightflow_contracts import (
    ArtifactRef,
    ChartRequest,
    DatasetProfile,
    DatasetSnapshot,
    MetricValue,
    ToolExecutionError,
    derive_id,
)
from insightflow_ingestion import write_table_csv

#: Polars dtypes a numeric column may have.
NUMERIC_TYPES = frozenset(
    {
        pl.Float64,
        pl.Float32,
        pl.Int64,
        pl.Int32,
        pl.Int16,
        pl.Int8,
        pl.UInt64,
        pl.UInt32,
        pl.UInt16,
        pl.UInt8,
    }
)


def is_numeric_dtype(dtype: object) -> bool:
    """True for any numeric polars dtype, including parameterised variants.

    `pl.Datetime` and friends are factories, not classes, so membership tests
    against a literal set are unreliable for parameterised dtypes. Polars'
    own predicates are used instead.
    """
    checker = getattr(dtype, "is_numeric", None)
    return bool(checker()) if callable(checker) else dtype in NUMERIC_TYPES


def is_temporal_dtype(dtype: object) -> bool:
    """True for Date, Datetime (any time unit) and String date-likes."""
    checker = getattr(dtype, "is_temporal", None)
    if callable(checker):
        return bool(checker())
    return dtype in {pl.Date, pl.String}


#: Plausible calendar years for a whole-number year column.
YEAR_RANGE = (1000, 2999)


def is_year_series(series: pl.Series) -> bool:
    """True for a whole-number column whose values all look like calendar years.

    Floats count when every value is whole: number parsing during cleaning turns
    `2019` into `2019.0`.
    """
    if not (series.dtype.is_integer() or series.dtype.is_float()):
        return False
    values = series.drop_nulls()
    if values.len() == 0:
        return False
    if series.dtype.is_float() and not bool((values == values.floor()).all()):
        return False
    low, high = YEAR_RANGE
    return bool(low <= values.min() and values.max() <= high)


def year_to_date(column: str) -> pl.Expr:
    """1 January of each year, so date tooling can treat years as periods."""
    return pl.date(pl.col(column).cast(pl.Int32), 1, 1).alias(column)


@dataclass
class ToolOutcome:
    """What a tool returns: metrics, tables, chart requests and warnings."""

    metrics: list[MetricValue] = field(default_factory=list)
    tables: list[pl.DataFrame] = field(default_factory=list)
    table_names: list[str] = field(default_factory=list)
    charts: list[ChartRequest] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)

    def metric(
        self,
        name: str,
        value: float | int | None,
        *,
        table: str | None = None,
        **kwargs: Any,
    ) -> MetricValue:
        """Record a computed metric, naming the evidence table it comes from."""
        item = MetricValue(name=name, value=value, table=table, **kwargs)
        self.metrics.append(item)
        return item

    def table(self, frame: pl.DataFrame, name: str) -> pl.DataFrame:
        if not name:
            raise ToolExecutionError("evidence table needs a name", retryable=False)
        self.tables.append(frame)
        self.table_names.append(name)
        return frame


@dataclass
class AnalysisContext:
    """Immutable-ish view of one dataset version plus an artifact sink."""

    frame: pl.DataFrame
    snapshot: DatasetSnapshot
    profile: DatasetProfile | None = None
    artifact_dir: Path | None = None
    max_rows: int = 5_000_000
    step_id: str = "step"
    owner_id: str = "anonymous"
    extras: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------- validation
    def require_columns(self, *names: str) -> None:
        missing = [name for name in names if name not in self.frame.columns]
        if missing:
            raise ToolExecutionError(
                "column not present in this dataset version",
                columns=missing,
                available=self.frame.columns[:50],
                retryable=False,
            )

    def numeric_column(self, name: str) -> pl.Expr:
        self.require_columns(name)
        if not is_numeric_dtype(self.frame.schema[name]):
            raise ToolExecutionError(
                "column is not numeric; parse it in the cleaning stage first",
                column=name,
                dtype=str(self.frame.schema[name]),
                retryable=False,
            )
        return pl.col(name)

    def temporal_column(self, name: str) -> pl.Expr:
        self.require_columns(name)
        # String is accepted because a plan can be validated before the date
        # parsing step has run. A whole-number year column (2019, 2020, ...) is a
        # time axis in its own right, common in annual statistics.
        dtype = self.frame.schema[name]
        if not (
            is_temporal_dtype(dtype)
            or dtype == pl.String
            or is_year_series(self.frame.get_column(name))
        ):
            raise ToolExecutionError(
                "column is not a date; parse it in the cleaning stage first",
                column=name,
                dtype=str(self.frame.schema[name]),
                retryable=False,
            )
        return pl.col(name)

    def category_column(self, name: str) -> pl.Expr:
        self.require_columns(name)
        if is_numeric_dtype(self.frame.schema[name]):
            raise ToolExecutionError(
                "column is not categorical and cannot be used as a grouping key",
                column=name,
                dtype=str(self.frame.schema[name]),
                retryable=False,
            )
        return pl.col(name)

    def guard_row_count(self) -> None:
        if self.frame.height > self.max_rows:
            raise ToolExecutionError(
                "dataset exceeds the row limit for this tool",
                rows=self.frame.height,
                limit=self.max_rows,
                retryable=False,
            )

    def filtered(self, *conditions: pl.Expr) -> pl.DataFrame:
        return self.frame.filter(pl.all_horizontal(conditions)) if conditions else self.frame

    # ---------------------------------------------------------------- writing
    def write_table(self, frame: pl.DataFrame, name: str) -> ArtifactRef:
        """Persist an evidence table and return its reference."""
        if self.artifact_dir is None:
            raise ToolExecutionError(
                "no artifact directory configured; tables cannot be persisted",
                retryable=False,
            )
        self.guard_row_count()
        path = self.artifact_dir / "tables" / f"{self.step_id}_{name}.csv"
        return write_table_csv(
            frame,
            path,
            artifact_id=derive_id(
                "tbl",
                {"step": self.step_id, "name": name, "rows": frame.head(200).rows(), "n": frame.height},
            ),
        )

    def write_chart_table(self, frame: pl.DataFrame, name: str) -> ArtifactRef:
        return self.write_table(frame, name)

    def elapsed(self, started: float) -> float:
        return time.perf_counter() - started
