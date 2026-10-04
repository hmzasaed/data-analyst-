"""Deterministic cleaning transforms.

Every function here has the same shape: take a frame, return a new frame plus a
`ChangeLogEntry` describing exactly what moved. Nothing is deleted unless the
change is *exact duplicate rows*, and that is itself reversible from the raw
snapshot. Type conversion replaces the string column in place but is reported as
irreversible at value level, pointing back to the snapshot as the recovery path.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import polars as pl
from insightflow_contracts import (
    ChangeLogEntry,
    ReviewLevel,
    Severity,
    ToolExecutionError,
    TransformKind,
)
from insightflow_ingestion import clean_number_literal

#: Date formats tried, in order, when the user has not pinned one.
DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y%m%d",
    "%d/%m/%Y %H:%M",
    "%b %d, %Y",
    "%d %b %Y",
)

DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
)

_WHITESPACE_RUN = re.compile(r"\s+")

_TRUE_TOKENS = ("true", "yes", "y", "t", "1")
_FALSE_TOKENS = ("false", "no", "n", "f", "0")

MAX_EXAMPLES = 5


def _entry(
    operation_id: str,
    kind: TransformKind,
    *,
    column: str | None,
    rows_before: int,
    rows_after: int,
    rows_affected: int,
    reason: str,
    reversible: bool,
    review_level: ReviewLevel,
    severity: Severity = Severity.LOW,
    null_before: int | None = None,
    null_after: int | None = None,
    inverse_note: str | None = None,
) -> ChangeLogEntry:
    return ChangeLogEntry(
        operation_id=operation_id,
        kind=kind,
        column=column,
        rows_before=rows_before,
        rows_after=rows_after,
        rows_affected=rows_affected,
        null_count_before=null_before,
        null_count_after=null_after,
        reason=reason,
        reversible=reversible,
        review_level=review_level,
        severity=severity,
        inverse_note=inverse_note,
    )


# ---------------------------------------------------------------- whitespace / case


def trim_whitespace(frame: pl.DataFrame, column: str) -> tuple[pl.DataFrame, int]:
    series = frame.get_column(column)
    before = series
    after = series.str.strip_chars()
    changed = (before != after) & before.is_not_null()
    return frame.with_columns(after.alias(column)), int(changed.sum())


def collapse_internal_whitespace(frame: pl.DataFrame, column: str) -> tuple[pl.DataFrame, int]:
    series = frame.get_column(column)
    collapsed = series.str.replace_all(_WHITESPACE_RUN.pattern, " ").str.strip_chars()
    changed = (series != collapsed) & series.is_not_null()
    return frame.with_columns(collapsed.alias(column)), int(changed.sum())


def normalise_case(frame: pl.DataFrame, column: str, *, case: str = "lower") -> tuple[pl.DataFrame, int]:
    series = frame.get_column(column)
    if case == "upper":
        after = series.str.to_uppercase()
    elif case == "title":
        after = series.str.to_titlecase()
    else:
        after = series.str.to_lowercase()
    changed = (series != after) & series.is_not_null()
    return frame.with_columns(after.alias(column)), int(changed.sum())


def canonical_category_map(series: pl.Series) -> dict[str, str]:
    """Build a deterministic raw-label -> canonical-label mapping.

    For each trimmed, lower-cased form the most frequent raw spelling wins; ties
    break on the lexicographically smallest spelling so the mapping is stable
    across runs. This is what lets category normalisation be a deterministic
    function of the data rather than a guess made by a language model.
    """
    counts: dict[str, dict[str, int]] = {}
    for value in series.drop_nulls().to_list():
        raw = str(value)
        key = raw.strip().lower()
        bucket = counts.setdefault(key, {})
        bucket[raw] = bucket.get(raw, 0) + 1

    mapping: dict[str, str] = {}
    for bucket in counts.values():
        winner = min(bucket.items(), key=lambda item: (-item[1], item[0]))[0]
        for raw in bucket:
            mapping[raw] = winner
    return mapping


def normalise_categories(
    frame: pl.DataFrame, column: str, *, canonical: dict[str, str]
) -> tuple[pl.DataFrame, int]:
    """Map raw labels onto a canonical spelling using an explicit mapping."""
    series = frame.get_column(column)
    mapping = pl.Series(column, [canonical.get(str(value), str(value)) for value in series.to_list()])
    changed = (series != mapping) & series.is_not_null()
    return frame.with_columns(mapping.alias(column)), int(changed.sum())


# ------------------------------------------------------------------ type parsing


def parse_number(frame: pl.DataFrame, column: str) -> tuple[pl.DataFrame, int]:
    """Convert currency/thousands-formatted text to Float64.

    Unparseable text becomes null and is counted by the caller via the change log,
    so nothing is silently coerced to zero. The returned count is the number of rows
    the conversion touched.
    """
    series = frame.get_column(column)
    cleaned = pl.Series(
        column,
        [clean_number_literal(str(value)) if value is not None else None for value in series.to_list()],
        dtype=pl.String,
    )
    parsed = cleaned.cast(pl.Float64, strict=False)
    return frame.with_columns(parsed.alias(column)), int(series.is_not_null().sum())


def parse_boolean(frame: pl.DataFrame, column: str) -> tuple[pl.DataFrame, int]:
    series = frame.get_column(column)
    lowered = series.str.to_lowercase().str.strip_chars()
    values = [
        None if value is None else (True if value in _TRUE_TOKENS else False if value in _FALSE_TOKENS else None)
        for value in lowered.to_list()
    ]
    parsed = pl.Series(column, values, dtype=pl.Boolean)
    affected = int(((series.is_not_null()) & (parsed.is_not_null())).sum())
    return frame.with_columns(parsed.alias(column)), affected


def parse_datetime(
    frame: pl.DataFrame, column: str, *, formats: tuple[str, ...] | None = None
) -> tuple[pl.DataFrame, int, str | None]:
    """Parse a text column into Datetime using the format that fits the most rows.

    Values that do not parse become null rather than being guessed at. The returned
    count is the number of rows the conversion touched; how many of those succeeded
    is visible in the change log as the rise in null count.
    """
    series = frame.get_column(column).cast(pl.String, strict=False).str.strip_chars()
    candidates = formats or DATE_FORMATS
    total = max(series.len(), 1)
    best: tuple[int, pl.Series, str] | None = None

    for fmt in candidates:
        try:
            parsed = series.str.strptime(pl.Datetime, fmt, strict=False)
        except Exception:
            continue
        successes = int(parsed.is_not_null().sum())
        if best is None or successes > best[0]:
            best = (successes, parsed, fmt)
        if successes == total:
            break

    if best is None:
        raise ToolExecutionError(
            "no supported datetime format parsed any value",
            column=column,
            formats=list(candidates),
            retryable=False,
        )

    _, parsed, fmt = best
    return frame.with_columns(parsed.alias(column)), int(series.is_not_null().sum()), fmt


def parse_date(frame: pl.DataFrame, column: str, *, formats: tuple[str, ...] | None = None):
    converted, count, fmt = parse_datetime(frame, column, formats=formats)
    return converted.with_columns(pl.col(column).dt.date().alias(column)), count, fmt


# --------------------------------------------------------------------- structural


def drop_exact_duplicates(frame: pl.DataFrame, keep: str = "first") -> tuple[pl.DataFrame, int]:
    unique = frame.unique(keep=keep, maintain_order=True)
    return unique, frame.height - unique.height


def rename_column(frame: pl.DataFrame, column: str, new_name: str) -> tuple[pl.DataFrame, int]:
    return frame.rename({column: new_name}), frame.height


def flag_outlier(
    frame: pl.DataFrame, column: str, *, upper: float, lower: float, flag_column: str | None = None
) -> tuple[pl.DataFrame, int]:
    """Add a boolean flag column. The measure itself is never altered."""
    target = flag_column or f"{column}__outlier_flag"
    values = frame.get_column(column)
    flag = ((values > upper) | (values < lower)).fill_null(False)
    affected = int(flag.sum())
    return frame.with_columns(flag.alias(target)), affected


def fill_null_with_median(frame: pl.DataFrame, column: str) -> tuple[pl.DataFrame, int]:
    values = frame.get_column(column)
    if not values.dtype.is_numeric():
        raise ToolExecutionError(
            "a median fill needs a numeric column",
            column=column, dtype=str(values.dtype), retryable=False,
        )
    median = values.drop_nulls().median()
    if median is None:
        raise ToolExecutionError(
            "the column has no values to take a median from", column=column, retryable=False
        )
    if values.dtype.is_integer():
        median = round(median)
    filled = values.fill_null(pl.lit(median).cast(values.dtype))
    affected = int((values.is_null() & filled.is_not_null()).sum())
    return frame.with_columns(filled.alias(column)), affected


def fill_null_with_constant(frame: pl.DataFrame, column: str, *, value: object) -> tuple[pl.DataFrame, int]:
    values = frame.get_column(column)
    filled = values.fill_null(value)
    affected = int((values.is_null() & filled.is_not_null()).sum())
    return frame.with_columns(filled.alias(column)), affected


def clip_range(frame: pl.DataFrame, column: str, *, lower: float, upper: float) -> tuple[pl.DataFrame, int]:
    values = frame.get_column(column)
    clipped = values.clip(lower_bound=lower, upper_bound=upper)
    affected = int(((values < lower) | (values > upper)).fill_null(False).sum())
    return frame.with_columns(clipped.alias(column)), affected


def mask_pii(frame: pl.DataFrame, column: str, *, keep_last: int = 0) -> tuple[pl.DataFrame, int]:
    """Redact a column to a coarse hint (e.g. `a***@***.com`), never a partial value.

    Masking is deliberately lossy: keeping the tail of an identifier re-creates much
    of the identifier, which defeats the purpose.
    """
    series = frame.get_column(column)
    values = series.to_list()
    masked: list[str | None] = []
    changed = 0
    for value in values:
        if value is None:
            masked.append(None)
            continue
        text = str(value)
        head = text[:1]
        hint = f"{head}{'*' * 4}" + (f"[last{keep_last}]" if keep_last else "")
        masked.append(hint)
        changed += 1
    return frame.with_columns(pl.Series(column, masked, dtype=pl.String).alias(column)), changed


TransformFn = Callable[..., tuple[pl.DataFrame, int]]
