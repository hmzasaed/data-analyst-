"""Normalisation of names and values into the internal table representation."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import polars as pl
from insightflow_contracts import derive_id

#: Values that mean "no data" in business exports. Kept explicit so that a
#: cleaning decision to drop them is visible rather than silent.
NULL_TOKENS: frozenset[str] = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "n/a",
        "na",
        "nan",
        "null",
        "none",
        "nil",
        "unknown",
        "(blank)",
        "#n/a",
        "#null!",
        "#value!",
        "#div/0!",
        "#ref!",
    }
)

_WHITESPACE = re.compile(r"\s+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_INTERNAL_COLLISION = "__"

@dataclass
class ColumnNameCollision:
    original: str
    normalised: str
    position: int
    reason: str


@dataclass
class NormalizedTable:
    """A table of strings with provenance, prior to type inference."""

    frame: pl.DataFrame
    original_columns: list[str] = field(default_factory=list)
    dataset_id: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def columns(self) -> list[str]:
        return list(self.frame.columns)

    @property
    def row_count(self) -> int:
        return self.frame.height

    def with_id(self, *, source_file_hash: str, sheet_name: str | None = None) -> NormalizedTable:
        self.dataset_id = derive_id(
            "ds",
            {
                "sha256": source_file_hash,
                "sheet": sheet_name,
                "columns": self.columns,
                "rows": self.row_count,
            },
        )
        return self

    def estimated_bytes(self) -> int:
        return int(self.frame.estimated_size())

    def to_parquet(self, path: str) -> None:
        self.frame.write_parquet(path)


def normalise_whitespace(value: str) -> str:
    """Collapse runs of whitespace and strip, preserving Unicode word content."""
    if value is None:
        return value
    return _WHITESPACE.sub(" ", value).strip()


def slugify(name: str) -> str:
    """Turn an arbitrary header into a stable, readable snake_case identifier.

    Unicode letters are preserved (`Café` stays `café`) so that non-English
    datasets keep meaningful column names instead of collapsing to noise.
    """
    text = unicodedata.normalize("NFKC", str(name)).strip()
    text = _CAMEL_BOUNDARY.sub("_", text)
    text = re.sub(r"[\s\-]+", "_", text)
    # Keep any alphanumeric character (Unicode-aware); everything else becomes "_".
    text = "".join(char if char.isalnum() else "_" for char in text)
    text = re.sub(r"_+", "_", text).strip("_").lower()
    return text or "column"


def normalise_column_names(
    names: list[str], *, fallback_prefix: str = "column"
) -> tuple[list[str], list[ColumnNameCollision]]:
    """Return unique, usable column names plus a record of every rename.

    Empty and duplicate headers are the two common failure modes; both are
    renamed deterministically and reported rather than silently dropped.
    """
    collisions: list[ColumnNameCollision] = []
    used: set[str] = set()
    resolved: list[str] = []

    for position, original in enumerate(names):
        candidate = slugify(original)
        if not original or not candidate or candidate == fallback_prefix:
            candidate = f"{fallback_prefix}_{position + 1}"
            collisions.append(
                ColumnNameCollision(str(original), candidate, position, "empty or unusable header")
            )
        if candidate in used:
            revised = f"{candidate}{_INTERNAL_COLLISION}{position + 1}"
            collisions.append(
                ColumnNameCollision(original, revised, position, "duplicate header")
            )
            candidate = revised
        used.add(candidate)
        resolved.append(candidate)

    return resolved, collisions


def is_null_token(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return normalise_whitespace(value).lower() in NULL_TOKENS
    # NaN is the only value that is not equal to itself.
    return isinstance(value, float) and value != value


#: Shared numeric-literal grammar. Profiling and cleaning both use this so that
#: "how many values parse as numbers" means the same thing in both stages.
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3})")
_NON_NUMERIC = re.compile(r"[^\d,.\-+eE]")
_PARENTHESISED = re.compile(r"^\((.+)\)$")


def clean_number_literal(value: str) -> str | None:
    """Strip currency symbols, thousands separators and trailing percent signs.

    Returns a string that is safe to hand to `cast(Float64)`, or None when the
    text carries no number at all. Accounting-style `(1,200)` becomes `-1200`.
    """
    text = _NON_NUMERIC.sub("", value.strip())
    if text.startswith("(") and text.endswith(")"):
        text = f"-{text[1:-1]}"
    text = _THOUSANDS.sub("", text)
    if not text or text in {"-", "+", ".", "-."}:
        return None
    return text
