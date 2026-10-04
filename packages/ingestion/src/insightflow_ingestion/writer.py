"""Writers for materialised dataset versions and evidence tables."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl
from insightflow_contracts import ArtifactKind, ArtifactRef, derive_id, file_sha256


def _write(path: Path, frame: pl.DataFrame, kind: ArtifactKind, *, artifact_id: str) -> ArtifactRef:
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind is ArtifactKind.DATASET_SNAPSHOT:
        frame.write_parquet(path)
        mime = "application/vnd.apache.parquet"
    elif kind is ArtifactKind.TABLE_CSV:
        frame.write_csv(path)
        mime = "text/csv"
    else:
        raise ValueError(f"unsupported write kind {kind}")
    return ArtifactRef(
        artifact_id=artifact_id,
        kind=kind,
        path=str(path),
        row_count=frame.height,
        byte_size=path.stat().st_size,
        sha256=file_sha256(path),
        mime_type=mime,
    )


def write_parquet(frame: pl.DataFrame, path: str | Path, *, artifact_id: str | None = None) -> ArtifactRef:
    """Persist a dataset version as Parquet and return its artifact reference."""
    resolved = artifact_id or derive_id("art", {"kind": "snapshot", "cols": frame.columns, "n": frame.height})
    return _write(Path(path), frame, ArtifactKind.DATASET_SNAPSHOT, artifact_id=resolved)


def write_table_csv(frame: pl.DataFrame, path: str | Path, *, artifact_id: str | None = None) -> ArtifactRef:
    """Persist an evidence table as CSV, which is what a user can download."""
    resolved = artifact_id or derive_id("tbl", {"cols": frame.columns, "n": frame.height, "rows": frame.head(50).rows()})
    return _write(Path(path), frame, ArtifactKind.TABLE_CSV, artifact_id=resolved)


def write_json(payload: Any, path: str | Path, *, artifact_id: str | None = None) -> ArtifactRef:
    resolved = artifact_id or derive_id("jsn", payload)
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return ArtifactRef(
        artifact_id=resolved,
        kind=ArtifactKind.REPORT_JSON,
        path=str(file_path),
        byte_size=file_path.stat().st_size,
        mime_type="application/json",
    )
