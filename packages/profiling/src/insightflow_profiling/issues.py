"""The data-quality issue catalogue.

Each detector returns `QualityIssue` objects carrying the evidence a user needs to
decide what to do (how many rows, which values, what fraction of the column). A
detector never fixes anything; `insightflow_cleaning` consumes these issues and
proposes transformations separately.
"""

from __future__ import annotations

import re

import polars as pl
from insightflow_contracts import (
    ColumnProfile,
    ProfileWarning,
    QualityIssue,
    QualityIssueKind,
    Severity,
)

#: A column with more than this share of missing values is called out.
HIGH_NULL_FRACTION = 0.30
#: Duplicate rows at or above this share of the table are flagged HIGH.
HIGH_DUPLICATE_FRACTION = 0.05
#: Non-finite or out-of-range values are always at least MEDIUM.
CATEGORY_INCONSISTENCY_MIN = 2

_WHITESPACE = re.compile(r"^\s|\s$|\s{2,}")


def detect_column_issues(column: ColumnProfile, series: pl.Series) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    total = max(1, series.len())
    null_fraction = column.null_fraction

    if null_fraction > 0:
        severity = Severity.HIGH if null_fraction >= HIGH_NULL_FRACTION else Severity.MEDIUM
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.MISSING_VALUES,
                severity=severity,
                column=column.name,
                description=(
                    f"{column.null_count:,} of {series.len():,} rows "
                    f"({null_fraction:.1%}) have no value."
                ),
                affected_rows=column.null_count,
                affected_fraction=round(null_fraction, 4),
                recommended_action=(
                    "Leave missing values untouched and exclude them from aggregates, "
                    "or choose an explicit imputation."
                ),
            )
        )

    if column.unique_count <= 1:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.CONSTANT_COLUMN,
                severity=Severity.LOW,
                column=column.name,
                description="Column has a single distinct value and carries no information.",
                affected_rows=series.len(),
                affected_fraction=1.0,
                recommended_action="Exclude from analysis; safe to drop from the working set.",
            )
        )
    elif column.unique_count <= 2:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.NEAR_CONSTANT_COLUMN,
                severity=Severity.INFO,
                column=column.name,
                description="Column has only two distinct values.",
                affected_rows=series.len(),
                affected_fraction=1.0,
                recommended_action="Usable as a flag or split.",
            )
        )

    if column.parse_failure_fraction > 0:
        severity = (
            Severity.HIGH
            if column.parse_failure_fraction > 0.2
            else Severity.MEDIUM
        )
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.PARSE_FAILURES,
                severity=severity,
                column=column.name,
                description=(
                    f"{column.parse_failure_fraction:.1%} of non-null values could not be "
                    f"parsed as {column.inferred_type}."
                ),
                affected_rows=int(column.parse_failure_fraction * max(column.null_count * 0 + total - column.null_count, 0)),
                affected_fraction=round(column.parse_failure_fraction, 4),
                recommended_action=(
                    "Inspect the failing values before converting; keep the original text "
                    "alongside the parsed column."
                ),
            )
        )

    if column.category is not None:
        issues.extend(_category_issues(column, series))

    if column.inferred_type in {"number", "integer"}:
        issues.extend(_numeric_issues(column))

    if column.numeric is not None and column.numeric.non_finite_count:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.NON_FINITE_NUMBERS,
                severity=Severity.MEDIUM,
                column=column.name,
                description=(
                    f"{column.numeric.non_finite_count:,} values are infinite or not a number "
                    f"and will break arithmetic."
                ),
                affected_rows=column.numeric.non_finite_count,
                affected_fraction=round(column.numeric.non_finite_count / total, 4),
                recommended_action="Replace with null and count them separately in the report.",
            )
        )

    issues.extend(_whitespace_issues(column, series))
    return issues


def _category_issues(column: ColumnProfile, series: pl.Series) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    category = column.category
    if category is None:
        return issues

    if category.case_variants or category.whitespace_variants:
        affected = series.len() - series.n_unique()
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.INCONSISTENT_CATEGORIES,
                severity=Severity.MEDIUM,
                column=column.name,
                description=(
                    f"{category.normalised_forms} normalised forms cover "
                    f"{series.n_unique()} raw values: casing and whitespace differ "
                    f"between otherwise identical labels."
                ),
                affected_rows=affected,
                affected_fraction=round(affected / max(series.len(), 1), 4),
                evidence={
                    "case_variants": category.case_variants,
                    "whitespace_variants": category.whitespace_variants,
                },
                recommended_action="Trim and case-normalise, keeping the original column for reference.",
            )
        )

    if category.distinct_count > 200 and column.distinct_fraction > 0.5:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.HIGH_CARDINALITY,
                severity=Severity.MEDIUM,
                column=column.name,
                description=(
                    f"{category.distinct_count:,} distinct values across "
                    f"{series.len():,} rows; unlikely to be a usable grouping key."
                ),
                affected_rows=series.len(),
                affected_fraction=1.0,
                recommended_action="Group only by this column if the value list is short enough to read.",
            )
        )
    return issues


def _numeric_issues(column: ColumnProfile) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    numeric = column.numeric
    if numeric is None or numeric.count == 0:
        return issues

    if numeric.negative_count and numeric.min is not None and numeric.min == numeric.max < 0:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.IMPLAUSIBLE_VALUES,
                severity=Severity.MEDIUM,
                column=column.name,
                description="Every value in this measure is negative.",
                affected_rows=numeric.count,
                affected_fraction=1.0,
                recommended_action="Confirm the sign convention before aggregating.",
            )
        )
    return issues


def _whitespace_issues(column: ColumnProfile, series: pl.Series) -> list[QualityIssue]:
    if column.inferred_type not in {"category", "text", "unknown"}:
        return []
    non_null = series.drop_nulls()
    if non_null.len() == 0:
        return []
    sample = non_null.head(2_000).cast(pl.String, strict=False)
    values = [value for value in sample.to_list() if isinstance(value, str)]

    padded = sum(1 for value in values if _WHITESPACE.search(value))
    unbalanced = sum(1 for value in values if _has_unbalanced_brackets(value))

    issues: list[QualityIssue] = []
    if padded:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.WHITESPACE_ISSUES,
                severity=Severity.LOW,
                column=column.name,
                description=f"{padded:,} sampled values have leading, trailing or repeated whitespace.",
                affected_rows=padded,
                affected_fraction=round(padded / len(values), 4),
                recommended_action="Trim surrounding whitespace; reversible and safe to apply automatically.",
            )
        )
    if unbalanced:
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.UNBALANCED_TEXT,
                severity=Severity.LOW,
                column=column.name,
                description=f"{unbalanced:,} sampled values contain unbalanced brackets or quotes.",
                affected_rows=unbalanced,
                affected_fraction=round(unbalanced / len(values), 4),
                recommended_action="Review manually; do not repair automatically.",
            )
        )
    return issues


def _has_unbalanced_brackets(value: str) -> bool:
    """True when a bracketing character appears without its partner."""
    pairs = {"(": ")", "[": "]", "{": "}", '"': '"'}
    stack: list[str] = []
    for char in value:
        if char in pairs:
            if pairs[char] == char:
                continue  # straight quotes are ambiguous; never reported
            stack.append(char)
        elif char in pairs.values():
            if not stack or pairs[stack.pop()] != char:
                return True
    return bool(stack)


def detect_table_issues(frame: pl.DataFrame, columns: list[ColumnProfile]) -> list[QualityIssue]:
    """Issues that need the whole table: duplicates and unusable headers."""
    issues: list[QualityIssue] = []
    total_rows = frame.height
    if total_rows == 0:
        return issues

    duplicate_rows = total_rows - len(frame.unique())
    if duplicate_rows:
        fraction = duplicate_rows / total_rows
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.DUPLICATE_ROWS,
                severity=Severity.HIGH if fraction >= HIGH_DUPLICATE_FRACTION else Severity.MEDIUM,
                description=(
                    f"{duplicate_rows:,} of {total_rows:,} rows "
                    f"({fraction:.1%}) are exact duplicates."
                ),
                affected_rows=duplicate_rows,
                affected_fraction=round(fraction, 4),
                recommended_action=(
                    "Remove exact duplicate rows only. The raw snapshot is retained, so this "
                    "is reversible."
                ),
            )
        )

    if len(frame.columns) != len(set(frame.columns)):
        issues.append(
            QualityIssue(
                kind=QualityIssueKind.DUPLICATE_HEADER,
                severity=Severity.MEDIUM,
                description="Column headers are not unique after normalisation.",
                recommended_action="Columns were renamed deterministically; check the mapping.",
            )
        )

    for column in columns:
        if column.name.startswith("column_") and column.original_name is not None:
            issues.append(
                QualityIssue(
                    kind=QualityIssueKind.UNUSABLE_COLUMN_NAME,
                    severity=Severity.LOW,
                    column=column.name,
                    description=f"Header '{column.original_name}' had no usable characters.",
                    recommended_action=f"Renamed to '{column.name}'; confirm the mapping.",
                )
            )
    return issues


def empty_file_warning() -> ProfileWarning:
    return ProfileWarning(code="empty_table", message="The selected table has a header but no data rows.")


def constant_table_warning(columns: list[ColumnProfile]) -> ProfileWarning:
    return ProfileWarning(
        code="no_usable_columns",
        message=(
            "No column carries enough variation to analyse "
            f"({len([c for c in columns if c.unique_count > 1])} of {len(columns)} vary)."
        ),
    )
