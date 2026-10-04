"""Choosing what a model would predict, and whether that is regression or classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl
from insightflow_contracts import DatasetProfile

from insightflow_features.columns import Columns

#: A numeric target with at most this many distinct values is treated as classes.
MAX_CLASSES = 20
MIN_ROWS = 20


@dataclass
class TargetChoice:
    column: str | None
    task: str | None
    reason: str
    warnings: list[str]


def task_for(series: pl.Series) -> str | None:
    """`regression`, `classification`, or None when the column cannot be a target."""
    values = series.drop_nulls()
    if values.len() < MIN_ROWS:
        return None
    levels = values.n_unique()
    if levels < 2:
        return None
    if series.dtype.is_numeric() and series.dtype != pl.Boolean:
        # Few distinct values make classes only when they are whole numbers (codes,
        # ratings, counts); 100.0 / 123.5 / 147.0 is still a quantity.
        whole = series.dtype.is_integer() or bool((values == values.floor()).all())
        return "classification" if whole and levels <= min(10, MAX_CLASSES) else "regression"
    return "classification" if levels <= MAX_CLASSES else None


def choose_target(
    frame: pl.DataFrame,
    profile: DatasetProfile | None,
    *,
    question: str | None,
    requested: str | None,
) -> TargetChoice:
    """Requested column first, then one the question names, then the headline measure."""
    columns = Columns(frame, profile)
    warnings: list[str] = []

    if requested:
        match = _match_column(requested, frame.columns)
        if match is None:
            warnings.append(f"Requested target '{requested}' is not a column; choosing one instead.")
        elif columns.is_personal(match) or columns.is_identifier(match):
            warnings.append(f"Requested target '{match}' is an identifier or personal data; choosing one instead.")
        else:
            task = task_for(frame.get_column(match))
            if task:
                return TargetChoice(match, task, "chosen by you", warnings)
            warnings.append(
                f"Requested target '{match}' has too few rows or values to model; choosing one instead."
            )

    candidates = [*columns.measures(), *columns.categoricals(max_levels=MAX_CLASSES)]
    mentioned = _mentioned(question or "", candidates)
    for name in mentioned:
        task = task_for(frame.get_column(name))
        if task:
            return TargetChoice(name, task, "named in your question", warnings)

    headline = columns.headline_measure(columns.measures())
    if headline and task_for(frame.get_column(headline)):
        return TargetChoice(headline, task_for(frame.get_column(headline)), "the dataset's headline measure", warnings)

    for name in candidates:
        task = task_for(frame.get_column(name))
        if task:
            return TargetChoice(name, task, "the first column that can be modelled", warnings)

    return TargetChoice(None, None, "no column can serve as a target", warnings)


def _match_column(name: str, columns: list[str]) -> str | None:
    if name in columns:
        return name
    lowered = {column.lower(): column for column in columns}
    return lowered.get(name.strip().lower())


def _mentioned(question: str, candidates: list[str]) -> list[str]:
    """Candidates the question names, longest name first (so `unit_price` beats `price`)."""
    text = question.lower().replace("_", " ")
    hits = []
    for name in sorted(candidates, key=len, reverse=True):
        spoken = name.lower().replace("_", " ")
        if re.search(rf"\b{re.escape(spoken)}\b", text):
            hits.append(name)
    return hits
