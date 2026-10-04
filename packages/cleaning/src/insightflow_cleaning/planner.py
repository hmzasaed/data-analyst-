"""`CleaningPlanner`: turns quality issues into reviewable transformation proposals.

Policy, in order of importance:

1. never silently drop a row (only exact duplicates, and even then visibly);
2. never impute or clip without asking;
3. every proposal states how many rows it touches and whether it can be undone.
"""

from __future__ import annotations

from insightflow_contracts import (
    CleaningOperation,
    CleaningPlan,
    ColumnProfile,
    DatasetProfile,
    InferredType,
    PiiRisk,
    QualityIssue,
    QualityIssueKind,
    ReviewLevel,
    SemanticRole,
    TransformKind,
    derive_id,
)

from insightflow_cleaning.transforms import DATE_FORMATS

#: Share of non-null values that must be numeric before conversion is automatic.
AUTO_PARSE_FRACTION = 0.98


#: A median fill is not offered for columns missing more than this share of values.
MAX_IMPUTABLE_MISSING = 0.5

class CleaningPlanner:
    version = "1"

    def plan(self, profile: DatasetProfile) -> CleaningPlan:
        operations: list[CleaningOperation] = []
        operations.extend(self._table_level(profile))
        for column in profile.columns:
            operations.extend(self._column_level(profile, column))
        operations.extend(self._pii_operations(profile))

        return CleaningPlan(
            dataset_id=profile.dataset_id,
            dataset_version=1,
            operations=operations,
            notes=[
                "Raw data is never modified in place; every change produces a new dataset version.",
                "Operations marked 'recommended' or 'needs review' are not applied until approved.",
            ],
            source_file_hash=profile.source_file_hash,
        )

    # ------------------------------------------------------------- table level
    def _table_level(self, profile: DatasetProfile) -> list[CleaningOperation]:
        operations: list[CleaningOperation] = []
        for issue in profile.quality_issues:
            if issue.kind is not QualityIssueKind.DUPLICATE_ROWS:
                continue
            operations.append(
                self._operation(
                    profile,
                    TransformKind.DROP_EXACT_DUPLICATE_ROWS,
                    column=None,
                    issue=issue,
                    affected_rows=issue.affected_rows,
                    review_level=ReviewLevel.AUTOMATIC,
                    reversible=True,
                    reason=(
                        f"Removed {issue.affected_rows:,} exact duplicate rows "
                        f"({issue.affected_fraction:.1%} of the table)."
                    ),
                    inverse_note="Restore from the raw snapshot; the original is retained unchanged.",
                    impact_note=f"Row count drops from {profile.row_count:,} to {profile.row_count - issue.affected_rows:,}.",
                )
            )
        return operations

    # ------------------------------------------------------------ column level
    def _column_level(self, profile: DatasetProfile, column: ColumnProfile) -> list[CleaningOperation]:
        operations: list[CleaningOperation] = []
        issues = self._issues_for(profile, column.name)
        non_null_rows = max(profile.row_count - column.null_count, 0)

        if self._needs_whitespace(column):
            operations.append(
                self._operation(
                    profile,
                    TransformKind.TRIM_WHITESPACE,
                    column=column.name,
                    issue=self._first(issues, QualityIssueKind.WHITESPACE_ISSUES),
                    affected_rows=self._affected(issues, QualityIssueKind.WHITESPACE_ISSUES),
                    review_level=ReviewLevel.AUTOMATIC,
                    reversible=True,
                    reason=f"Trimmed leading and trailing whitespace in '{column.name}'.",
                    inverse_note="Original values are preserved in the raw snapshot.",
                )
            )

        if column.category is not None and (
            column.category.case_variants or column.category.whitespace_variants
        ):
            issue = self._first(issues, QualityIssueKind.INCONSISTENT_CATEGORIES)
            operations.append(
                self._operation(
                    profile,
                    TransformKind.NORMALISE_CATEGORIES,
                    column=column.name,
                    issue=issue,
                    affected_rows=self._affected(issues, QualityIssueKind.INCONSISTENT_CATEGORIES),
                    review_level=ReviewLevel.RECOMMENDED,
                    reversible=True,
                    reason=(
                        f"'{column.name}' has {column.category.distinct_count} raw labels for "
                        f"{column.category.normalised_forms} distinct values once case and "
                        f"whitespace are normalised."
                    ),
                    inverse_note="Map canonical labels back to the raw values in the snapshot.",
                    impact_note="Grouping by this column will treat differently-cased labels as one group.",
                )
            )

        if column.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}:
            operations.append(
                self._operation(
                    profile,
                    TransformKind.PARSE_NUMBER,
                    column=column.name,
                    issue=self._first(issues, QualityIssueKind.PARSE_FAILURES),
                    affected_rows=round(column.parse_failure_fraction * non_null_rows),
                    review_level=self._parse_review_level(column),
                    reversible=False,
                    reason=(
                        f"Parsed '{column.name}' to a number"
                        + (
                            f"; {column.parse_failure_fraction:.1%} of values did not parse "
                            "and become null."
                            if column.parse_failure_fraction
                            else " with no loss of values."
                        )
                    ),
                    inverse_note="Re-read the column from the raw snapshot to restore the original text.",
                    impact_note=(
                        "Aggregates on this column are only possible after this conversion."
                        if column.parse_failure_fraction
                        else None
                    ),
                )
            )

        if column.inferred_type in {InferredType.DATE, InferredType.DATETIME}:
            operations.append(
                self._operation(
                    profile,
                    TransformKind.PARSE_DATETIME,
                    column=column.name,
                    issue=self._first(issues, QualityIssueKind.PARSE_FAILURES),
                    affected_rows=round(column.parse_failure_fraction * non_null_rows),
                    parameters={"formats": list(DATE_FORMATS)},
                    review_level=self._parse_review_level(column),
                    reversible=False,
                    reason=(
                        f"Parsed '{column.name}' as a date"
                        + (
                            f"; {column.parse_failure_fraction:.1%} of values did not parse."
                            if column.parse_failure_fraction
                            else " with no loss of values."
                        )
                    ),
                    inverse_note="Re-read the column from the raw snapshot.",
                    impact_note=(
                        "Time-series analysis is only possible after this step."
                        if column.parse_failure_fraction
                        else None
                    ),
                )
            )

        # A median exists only for numbers. Imputing an identifier, or a column that
        # is mostly empty, would invent data rather than fill a gap.
        imputable = (
            column.inferred_type in {InferredType.NUMBER, InferredType.INTEGER}
            and column.semantic_role is not SemanticRole.IDENTIFIER
            and non_null_rows > 0
            and column.null_fraction <= MAX_IMPUTABLE_MISSING
        )
        for kind, label in (
            (QualityIssueKind.MISSING_VALUES, "missing values"),
            (QualityIssueKind.NON_FINITE_NUMBERS, "non-finite numbers"),
            (QualityIssueKind.IMPLAUSIBLE_VALUES, "implausible values"),
        ):
            issue = self._first(issues, kind)
            if issue is None or not imputable:
                continue
            operations.append(
                self._operation(
                    profile,
                    TransformKind.FILL_NULL_WITH_MEDIAN,
                    column=column.name,
                    issue=issue,
                    affected_rows=issue.affected_rows,
                    review_level=ReviewLevel.NEEDS_REVIEW,
                    reversible=False,
                    reason=(
                        f"'{column.name}' has {label} in {issue.affected_rows:,} rows. "
                        "Imputing the median is offered for review only."
                    ),
                    inverse_note="Imputation cannot be undone on the cleaned copy; the raw snapshot can.",
                    impact_note="Imputation removes these rows' contribution to variation and should be stated in the report.",
                )
            )
            break  # one median fill per column, whichever issue prompted it
        return operations

    def _pii_operations(self, profile: DatasetProfile) -> list[CleaningOperation]:
        operations: list[CleaningOperation] = []
        for column in profile.columns:
            if column.pii_risk in {PiiRisk.HIGH, PiiRisk.MEDIUM}:
                operations.append(
                    self._operation(
                        profile,
                        TransformKind.MASK_PII,
                        column=column.name,
                        issue=None,
                        affected_rows=0,
                        review_level=ReviewLevel.NEEDS_REVIEW,
                        reversible=True,
                        reason=(
                            f"'{column.name}' looks like personal data "
                            f"({column.pii_risk.value} risk: {', '.join(column.pii_signals) or 'name or value pattern'})."
                        ),
                        inverse_note="Values remain in the raw snapshot; only the analysis copy is masked.",
                        impact_note="Masking prevents this column from being grouped or counted by value.",
                    )
                )
        return operations

    # ---------------------------------------------------------------- helpers
    def _operation(
        self,
        profile: DatasetProfile,
        kind: TransformKind,
        *,
        column: str | None,
        issue: QualityIssue | None,
        affected_rows: int,
        review_level: ReviewLevel,
        reversible: bool,
        reason: str,
        inverse_note: str,
        impact_note: str | None = None,
        parameters: dict[str, object] | None = None,
    ) -> CleaningOperation:
        operation_id = derive_id(
            "op",
            {"dataset": profile.dataset_id, "kind": kind.value, "column": column, "params": parameters or {}},
        )
        return CleaningOperation(
            operation_id=operation_id,
            kind=kind,
            column=column,
            parameters=parameters or {},
            reason=reason,
            affected_rows=max(affected_rows, 0),
            review_level=review_level,
            reversible=reversible,
            issue_kind=issue.kind.value if issue else None,
            inverse_kind=self._inverse_kind(kind),
            inverse_note=inverse_note,
            impact_note=impact_note,
        )

    @staticmethod
    def _inverse_kind(kind: TransformKind) -> TransformKind | None:
        return {
            TransformKind.TRIM_WHITESPACE: TransformKind.KEEP_ORIGINAL_COLUMN,
            TransformKind.NORMALISE_CASE: TransformKind.KEEP_ORIGINAL_COLUMN,
            TransformKind.COLLAPSE_INTERNAL_WHITESPACE: TransformKind.KEEP_ORIGINAL_COLUMN,
            TransformKind.NORMALISE_CATEGORIES: TransformKind.KEEP_ORIGINAL_COLUMN,
            TransformKind.DROP_EXACT_DUPLICATE_ROWS: TransformKind.KEEP_ORIGINAL_COLUMN,
            TransformKind.MASK_PII: TransformKind.KEEP_ORIGINAL_COLUMN,
        }.get(kind)

    @staticmethod
    def _needs_whitespace(column: ColumnProfile) -> bool:
        if column.category is None:
            return False
        return bool(column.category.whitespace_variants)

    @staticmethod
    def _parse_review_level(column: ColumnProfile) -> ReviewLevel:
        if column.parse_failure_fraction == 0:
            return ReviewLevel.AUTOMATIC
        if column.parse_failure_fraction <= 1 - AUTO_PARSE_FRACTION:
            return ReviewLevel.AUTOMATIC
        return ReviewLevel.RECOMMENDED

    @staticmethod
    def _issues_for(profile: DatasetProfile, column: str) -> list[QualityIssue]:
        return [issue for issue in profile.quality_issues if issue.column == column]

    @staticmethod
    def _first(issues: list[QualityIssue], kind: QualityIssueKind) -> QualityIssue | None:
        for issue in issues:
            if issue.kind is kind:
                return issue
        return None

    @staticmethod
    def _affected(issues: list[QualityIssue], kind: QualityIssueKind) -> int:
        issue = CleaningPlanner._first(issues, kind)
        return issue.affected_rows if issue else 0


def plan_cleaning(profile: DatasetProfile) -> CleaningPlan:
    """Propose cleaning operations for a profiled dataset."""
    return CleaningPlanner().plan(profile)
