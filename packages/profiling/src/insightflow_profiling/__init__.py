"""Deterministic dataset profiling.

`profile_dataset` is the only entry point the orchestrator uses. It is pure
computation: the same table always yields the same profile.
"""

from insightflow_profiling.issues import detect_column_issues, detect_table_issues
from insightflow_profiling.profiler import (
    DatasetProfiler,
    profile_frame,
    role_summary,
)
from insightflow_profiling.semantics import ColumnSemantics, detect_pii, infer_role
from insightflow_profiling.type_inference import TypeInference, infer_column_type

__all__ = [
    "ColumnSemantics",
    "DatasetProfiler",
    "TypeInference",
    "detect_column_issues",
    "detect_pii",
    "detect_table_issues",
    "infer_column_type",
    "infer_role",
    "profile_dataset",
    "role_summary",
]

#: The profiling tool name used in the tool registry and audit log.
PROFILER_TOOL = "profile_dataset"


def profile_dataset(frame, snapshot, *, original_columns: list[str] | None = None):
    """Profile a Polars frame into a `DatasetProfile`.

    `frame` is the normalised string table from ingestion; `snapshot` carries the
    dataset identity that the profile must agree with.
    """
    return profile_frame(frame, snapshot, original_columns=original_columns)
