"""Deterministic logical type inference from text columns.

Inference is evidence-based: a column is only called numeric or temporal if a
sufficient share of its non-null values parse. Mixed columns are reported with a
`parse_failure_fraction` rather than silently coerced, so a column like
`revenue` containing the string "n/a" twice does not become text.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl
from insightflow_contracts import InferredType
from insightflow_ingestion import clean_number_literal

#: Minimum non-null rows before type inference is allowed to call anything.
MIN_ROWS_FOR_INFERENCE = 5

#: Share of non-null values that must parse for the type to be accepted.
PARSE_ACCEPT_THRESHOLD = 0.95

#: Below this, the type is still reported but flagged as mixed. Numbers need a
#: higher bar than dates: a date format that fits two thirds of a column is a date
#: column with some bad rows, whereas a number that fits two thirds could be an
#: identifier or a code. Ambiguous day/month ordering is common in exported data, so
#: the date bar is deliberately lower.
PARSE_WARN_THRESHOLD = 0.80
DATE_PARSE_WARN_THRESHOLD = 0.60

#: Distinct values at or below this make a text column a category.
CATEGORY_MAX_DISTINCT = 50
CATEGORY_MAX_DISTINCT_FRACTION = 0.50
CATEGORY_MAX_AVG_LENGTH = 60.0

#: Even a large absolute count is a category if it is this small: a 3-value
#: column is a category in a 10,000-row table.
CATEGORY_ALWAYS_MAX = 5

BOOLEAN_TRUE_TOKENS = frozenset({"true", "yes", "y", "t", "1"})
BOOLEAN_FALSE_TOKENS = frozenset({"false", "no", "n", "f", "0"})
BOOLEAN_TOKENS = BOOLEAN_TRUE_TOKENS | BOOLEAN_FALSE_TOKENS

DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%m-%d-%Y",
    "%d.%m.%Y",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%Y%m%d",
    "%b %d, %Y",
    "%d %b %Y",
    "%B %d, %Y",
)

DATETIME_FORMATS: tuple[str, ...] = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
)


@dataclass(frozen=True)
class TypeInference:
    inferred_type: InferredType
    parse_failure_fraction: float
    distinct_count: int
    detail: str = ""

    @property
    def is_mixed(self) -> bool:
        return (
            self.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}
            and self.parse_failure_fraction > 0.0
        )


def _non_null(series: pl.Series) -> pl.Series:
    return series.drop_nulls()


def _numeric_success(series: pl.Series) -> float:
    """Fraction of non-null values that parse as a number.

    Uses the same grammar as the cleaning transform, so the parse-failure rate
    reported here is the rate the user will see when the conversion is applied.
    """
    non_null = _non_null(series)
    if non_null.len() == 0:
        return 0.0
    cleaned = pl.Series(
        series.name,
        [clean_number_literal(str(value)) if value is not None else None for value in non_null.to_list()],
        dtype=pl.String,
    )
    parsed = cleaned.cast(pl.Float64, strict=False)
    return parsed.is_not_null().sum() / non_null.len()


def _looks_boolean(series: pl.Series) -> float:
    """Share of non-null values drawn from the boolean token set.

    A column using four spellings ("yes", "no", "true", "false") is still boolean:
    what matters is that the spellings collapse onto two logical values.
    """
    non_null = _non_null(series)
    if non_null.len() == 0:
        return 0.0
    lowered = non_null.str.to_lowercase().str.strip_chars()
    ok = lowered.is_in(list(BOOLEAN_TOKENS)).sum()
    return ok / non_null.len()


def _logical_boolean_distinct(series: pl.Series) -> int:
    """How many distinct true/false values a column collapses to."""
    non_null = _non_null(series)
    if non_null.len() == 0:
        return 0
    lowered = non_null.str.to_lowercase().str.strip_chars().to_list()
    return len(
        {value in BOOLEAN_TRUE_TOKENS for value in lowered if value in BOOLEAN_TOKENS}
    )


def _date_success(series: pl.Series) -> tuple[float, str | None]:
    """Best parse rate across known formats, and the winning format."""
    non_null = _non_null(series)
    if non_null.len() == 0:
        return 0.0, None
    best_rate, best_format = 0.0, None
    for fmt in DATE_FORMATS:
        try:
            parsed = non_null.str.strptime(pl.Date, fmt, strict=False)
        except Exception:
            continue
        rate = parsed.is_not_null().sum() / non_null.len()
        if rate > best_rate:
            best_rate, best_format = rate, fmt
        if best_rate == 1.0:
            break
    return best_rate, best_format


def _datetime_success(series: pl.Series) -> float:
    non_null = _non_null(series)
    if non_null.len() == 0:
        return 0.0
    best = 0.0
    for fmt in DATETIME_FORMATS:
        try:
            parsed = non_null.str.strptime(pl.Datetime, fmt, strict=False)
        except Exception:
            continue
        rate = parsed.is_not_null().sum() / non_null.len()
        best = max(best, rate)
        if best == 1.0:
            break
    return best


#: Direct dtype -> logical type, for frames that are already typed.
_TYPED_MAP: dict[object, InferredType] = {
    pl.Float64: InferredType.NUMBER,
    pl.Float32: InferredType.NUMBER,
    pl.Int64: InferredType.INTEGER,
    pl.Int32: InferredType.INTEGER,
    pl.Int16: InferredType.INTEGER,
    pl.Int8: InferredType.INTEGER,
    pl.UInt64: InferredType.INTEGER,
    pl.UInt32: InferredType.INTEGER,
    pl.UInt16: InferredType.INTEGER,
    pl.UInt8: InferredType.INTEGER,
    pl.Date: InferredType.DATE,
    pl.Datetime: InferredType.DATETIME,
    pl.Boolean: InferredType.BOOLEAN,
}


def infer_column_type(series: pl.Series, *, row_count: int) -> TypeInference:
    """Infer the logical type of one column.

    Ingestion hands over text columns, so inference usually parses strings. A frame
    that is already typed (for example after a cleaning step) is mapped straight
    from its dtype rather than being re-parsed.
    """
    dtype = series.dtype
    if dtype != pl.String:
        direct = _TYPED_MAP.get(dtype)
        if direct is not None:
            return TypeInference(
                direct, 0.0, series.n_unique(), f"column is already {dtype}"
            )
        if dtype == pl.Null:
            return TypeInference(InferredType.UNKNOWN, 1.0, 0, "column is entirely null")
        return TypeInference(
            InferredType.TEXT, 0.0, series.n_unique(), f"non-text column of type {dtype}"
        )

    non_null_count = _non_null(series).len()
    distinct = series.n_unique()
    null_fraction = 0.0 if row_count == 0 else series.null_count() / row_count

    if non_null_count == 0:
        return TypeInference(InferredType.UNKNOWN, 1.0, distinct, "column is entirely null")

    avg_length = float(_non_null(series).str.len_chars().mean() or 0.0)
    numeric_rate = _numeric_success(series)
    boolean_rate = _looks_boolean(series)
    datetime_rate = _datetime_success(series)
    date_rate, date_format = _date_success(series)
    time_rate = max(datetime_rate, date_rate)

    if boolean_rate >= PARSE_ACCEPT_THRESHOLD and _logical_boolean_distinct(series) <= 2:
        return TypeInference(InferredType.BOOLEAN, 1.0 - boolean_rate, distinct, "boolean token set")

    if numeric_rate >= PARSE_ACCEPT_THRESHOLD and distinct > 2:
        integral = _is_integral(_non_null(series))
        inferred = InferredType.INTEGER if integral else InferredType.NUMBER
        return TypeInference(
            inferred,
            1.0 - numeric_rate,
            distinct,
            f"{numeric_rate:.0%} of values parse as numbers",
        )

    if time_rate >= PARSE_ACCEPT_THRESHOLD:
        if datetime_rate >= date_rate:
            return TypeInference(InferredType.DATETIME, 1.0 - datetime_rate, distinct, "datetime literals")
        return TypeInference(
            InferredType.DATE,
            1.0 - date_rate,
            distinct,
            f"date literals matching {date_format or 'a known format'}",
        )

    # Nothing dominated, but one type is clearly the majority. Report it as that
    # type *with* a parse-failure fraction rather than falling back to "category",
    # so a measure with a few stray labels is still recognisable as a measure.
    majority_rate = max(numeric_rate, time_rate)
    if majority_rate >= (
        DATE_PARSE_WARN_THRESHOLD if time_rate > numeric_rate else PARSE_WARN_THRESHOLD
    ):
        majority = InferredType.NUMBER if numeric_rate >= time_rate else InferredType.DATE
        return TypeInference(
            majority,
            1.0 - majority_rate,
            distinct,
            f"{majority_rate:.0%} parse as a single type; mixed content present",
        )

    # Nothing dominated: fall back on shape. A column is a category when it has very
    # few distinct values in absolute terms, or few relative to the row count.
    looks_like_category = distinct <= CATEGORY_ALWAYS_MAX or (
        distinct <= CATEGORY_MAX_DISTINCT
        and row_count > 0
        and distinct / row_count <= CATEGORY_MAX_DISTINCT_FRACTION
    )
    if looks_like_category and avg_length <= CATEGORY_MAX_AVG_LENGTH:
        return TypeInference(InferredType.CATEGORY, 0.0, distinct, "low-cardinality short strings")

    if null_fraction > 0.9:
        return TypeInference(InferredType.UNKNOWN, 1.0, distinct, "almost entirely null")

    return TypeInference(
        InferredType.TEXT,
        0.0,
        distinct,
        f"{distinct} distinct free-text values",
    )


def _is_integral(series: pl.Series) -> bool:
    cleaned = pl.Series(
        series.name,
        [clean_number_literal(str(value)) if value is not None else None for value in series.to_list()],
        dtype=pl.String,
    )
    try:
        as_float = cleaned.cast(pl.Float64, strict=False).drop_nulls()
    except Exception:  # pragma: no cover - defensive
        return False
    if as_float.len() == 0:
        return False
    return bool((as_float == as_float.round(0)).all())
