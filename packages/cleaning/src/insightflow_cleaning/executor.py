"""`CleaningExecutor`: apply approved operations and record exactly what happened.

The executor is the only component allowed to change data, and every change goes
through the change log. Rejected operations are carried forward as `rejected` so
the report can state what was *not* done.
"""

from __future__ import annotations

import time

import polars as pl
from insightflow_contracts import (
    ChangeLogEntry,
    CleaningExecution,
    CleaningOperation,
    CleaningPlan,
    DatasetSnapshot,
    ReviewLevel,
    Severity,
    ToolExecutionError,
    TransformKind,
    content_digest,
)

from insightflow_cleaning import transforms


class CleaningExecutor:
    version = "1"

    def apply(
        self,
        frame: pl.DataFrame,
        plan: CleaningPlan,
        snapshot: DatasetSnapshot,
        *,
        approved_operation_ids: set[str] | None = None,
        stop_on_error: bool = False,
    ) -> tuple[pl.DataFrame, CleaningExecution]:
        """Run the approved subset of `plan` against `frame`.

        `approved_operation_ids` of `None` means "only automatic operations";
        an explicit set means the user reviewed and decided.
        """
        working = frame
        entries: list[ChangeLogEntry] = []
        rejected: list[CleaningOperation] = []

        for operation in plan.operations:
            decision = self._decide(operation, approved_operation_ids)
            if decision == "skip":
                rejected.append(operation)
                continue
            try:
                working, entry = self._run(working, operation)
            except ToolExecutionError:
                rejected.append(operation)
                if stop_on_error:
                    raise
                continue
            except (pl.exceptions.PolarsError, ValueError, TypeError) as exc:
                # One operation that cannot run on this data (even if approved) is
                # recorded as not applied; it must not cost the whole job.
                rejected.append(operation)
                if stop_on_error:
                    raise ToolExecutionError(
                        f"cleaning operation failed: {type(exc).__name__}",
                        operation_id=operation.operation_id, retryable=False,
                    ) from exc
                continue
            entries.append(entry)

        to_version = snapshot.version + 1
        digest = content_digest(
            [entry.model_dump(mode="json") for entry in entries],
            length=24,
        )
        execution = CleaningExecution(
            dataset_id=snapshot.dataset_id,
            from_version=snapshot.version,
            to_version=to_version,
            applied=entries,
            rejected=rejected,
            transform_digest=digest,
        )
        return working, execution

    # ------------------------------------------------------------- decisions
    def _decide(
        self,
        operation: CleaningOperation,
        approved: set[str] | None,
    ) -> str:
        if approved is None:
            return "apply" if operation.review_level is ReviewLevel.AUTOMATIC else "skip"
        if operation.operation_id in approved:
            return "apply"
        # Automatic operations still apply unless the user explicitly rejected them.
        if operation.review_level is ReviewLevel.AUTOMATIC and operation.accepted is not False:
            return "apply"
        return "skip"

    # -------------------------------------------------------------- execution
    def _run(self, frame: pl.DataFrame, operation: CleaningOperation) -> tuple[pl.DataFrame, ChangeLogEntry]:
        kind = operation.kind
        column = operation.column
        rows_before = frame.height
        null_before = self._null_count(frame, column)

        frame_after, affected, detail = self._dispatch(frame, kind, operation)

        entry = ChangeLogEntry(
            operation_id=operation.operation_id,
            kind=kind,
            column=column,
            rows_before=rows_before,
            rows_after=frame_after.height,
            rows_affected=affected,
            null_count_before=null_before,
            null_count_after=self._null_count(frame_after, column),
            reason=operation.reason,
            reversible=operation.reversible,
            review_level=operation.review_level,
            severity=self._severity(operation),
            inverse_note=operation.inverse_note or operation.impact_note or detail,
        )
        return frame_after, entry

    def _dispatch(
        self, frame: pl.DataFrame, kind: TransformKind, operation: CleaningOperation
    ) -> tuple[pl.DataFrame, int, str]:
        column = self._require_column(operation)
        params = operation.parameters

        if kind is TransformKind.TRIM_WHITESPACE:
            result, affected = transforms.trim_whitespace(frame, column)
        elif kind is TransformKind.COLLAPSE_INTERNAL_WHITESPACE:
            result, affected = transforms.collapse_internal_whitespace(frame, column)
        elif kind is TransformKind.NORMALISE_CASE:
            result, affected = transforms.normalise_case(frame, column, case=str(params.get("case", "lower")))
        elif kind is TransformKind.NORMALISE_CATEGORIES:
            canonical = params.get("canonical")
            mapping = (
                dict(canonical)
                if canonical
                else transforms.canonical_category_map(frame.get_column(column))
            )
            result, affected = transforms.normalise_categories(frame, column, canonical=mapping)
        elif kind is TransformKind.PARSE_NUMBER:
            result, affected = transforms.parse_number(frame, column)
        elif kind is TransformKind.PARSE_BOOLEAN:
            result, affected = transforms.parse_boolean(frame, column)
        elif kind is TransformKind.PARSE_DATETIME:
            result, affected, fmt = transforms.parse_datetime(
                frame, column, formats=tuple(params["formats"]) if params.get("formats") else None
            )
            return result, affected, f"parsed with format {fmt}"
        elif kind is TransformKind.DROP_EXACT_DUPLICATE_ROWS:
            result, affected = transforms.drop_exact_duplicates(frame)
        elif kind is TransformKind.RENAME_COLUMN:
            result, affected = transforms.rename_column(frame, column, str(params["new_name"]))
        elif kind is TransformKind.FLAG_OUTLIER:
            result, affected = transforms.flag_outlier(
                frame,
                column,
                upper=float(params["upper"]),
                lower=float(params["lower"]),
                flag_column=params.get("flag_column"),
            )
        elif kind is TransformKind.FILL_NULL_WITH_MEDIAN:
            result, affected = transforms.fill_null_with_median(frame, column)
        elif kind is TransformKind.FILL_NULL_WITH_CONSTANT:
            result, affected = transforms.fill_null_with_constant(
                frame, column, value=params.get("value")
            )
        elif kind is TransformKind.CLIP_RANGE:
            result, affected = transforms.clip_range(
                frame, column, lower=float(params["lower"]), upper=float(params["upper"])
            )
        elif kind is TransformKind.MASK_PII:
            result, affected = transforms.mask_pii(frame, column, keep_last=int(params.get("keep_last", 0)))
        else:
            raise ToolExecutionError(
                "unsupported cleaning operation",
                kind=kind.value,
                retryable=False,
            )
        return result, affected, ""

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _require_column(operation: CleaningOperation) -> str:
        if not operation.column:
            raise ToolExecutionError(
                "operation requires a column",
                kind=operation.kind.value,
                retryable=False,
            )
        return operation.column

    @staticmethod
    def _null_count(frame: pl.DataFrame, column: str | None) -> int | None:
        if not column or column not in frame.columns:
            return None
        return int(frame.get_column(column).null_count())

    @staticmethod
    def _severity(operation: CleaningOperation) -> Severity:
        if operation.review_level is ReviewLevel.AUTOMATIC:
            return Severity.LOW
        if operation.review_level is ReviewLevel.RECOMMENDED:
            return Severity.MEDIUM
        return Severity.HIGH

    def new_snapshot(self, snapshot: DatasetSnapshot, execution: CleaningExecution) -> DatasetSnapshot:
        return snapshot.model_copy(
            update={
                "version": execution.to_version,
                "created_from": "cleaned",
                "transform_digest": execution.transform_digest,
            }
        )


def next_snapshot(snapshot: DatasetSnapshot, execution: CleaningExecution) -> DatasetSnapshot:
    """Advance a snapshot to the version produced by a cleaning execution."""
    return snapshot.model_copy(
        update={
            "version": execution.to_version,
            "created_from": "cleaned",
            "transform_digest": execution.transform_digest,
        }
    )


def apply_cleaning(
    frame: pl.DataFrame,
    plan: CleaningPlan,
    snapshot: DatasetSnapshot,
    *,
    approved_operation_ids: set[str] | None = None,
) -> tuple[pl.DataFrame, CleaningExecution]:
    """Apply the approved part of a cleaning plan and return the audit trail."""
    started = time.perf_counter()
    executor = CleaningExecutor()
    cleaned, execution = executor.apply(
        frame, plan, snapshot, approved_operation_ids=approved_operation_ids
    )
    elapsed = time.perf_counter() - started
    if elapsed > 300:  # pragma: no cover - defensive guard for pathological plans
        raise ToolExecutionError("cleaning exceeded its time budget", seconds=round(elapsed, 1))
    return cleaned, execution
