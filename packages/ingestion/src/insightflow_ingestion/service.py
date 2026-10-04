"""`IngestionService`: register a file, validate it, and materialise a snapshot.

The service is the only component that turns user-supplied bytes into a
dataset. It writes an immutable raw snapshot plus a Parquet version so that every
downstream stage references a concrete, hashable artifact.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    DatasetSnapshot,
    RawFileRecord,
    UnsupportedInputError,
    derive_id,
    file_sha256,
    get_settings,
)

from insightflow_ingestion.normalize import NormalizedTable
from insightflow_ingestion.readers import ReadOptions, ReadResult, read_table
from insightflow_ingestion.safety import SafetyReport, assert_safe
from insightflow_ingestion.writer import write_parquet


@dataclass
class IngestionOutcome:
    """Everything the profiling stage needs, and nothing it does not."""

    record: RawFileRecord
    safety: SafetyReport
    read: ReadResult
    snapshot: DatasetSnapshot
    raw_artifact: ArtifactRef
    table: NormalizedTable
    dataset_path: Path
    elapsed_seconds: float


class IngestionService:
    """Filesystem-backed ingestion with a per-owner namespace."""

    def __init__(self, root: str | Path | None = None, *, owner_id: str = "anonymous") -> None:
        settings = get_settings()
        self.root = Path(root) if root is not None else settings.artifact_path("raw")
        self.owner_id = owner_id

    # ------------------------------------------------------------------ paths
    def owner_dir(self, owner_id: str | None = None) -> Path:
        return self.root / (owner_id or self.owner_id)

    def dataset_dir(self, owner_id: str | None = None, dataset_id: str = "") -> Path:
        return self.owner_dir(owner_id) / "datasets" / dataset_id

    # ------------------------------------------------------------- lifecycle
    def register(self, source: str | Path, *, filename: str | None = None) -> RawFileRecord:
        """Copy the upload into private storage and hash it.

        The original bytes are preserved verbatim so the snapshot can be re-verified
        against the hash long after processing.
        """
        settings = get_settings()
        source_path = Path(source)
        name = filename or source_path.name
        safety = assert_safe(
            source_path,
            max_bytes=settings.upload.max_bytes,
            allowed_extensions=settings.upload.allowed_extensions,
        )
        digest = file_sha256(source_path)

        target = self.owner_dir() / "uploads" / f"{digest[:16]}{source_path.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            shutil.copyfile(source_path, target)
        rehash = file_sha256(target)

        return RawFileRecord(
            artifact_id=derive_id("raw", {"sha256": digest}),
            owner_id=self.owner_id,
            filename=name,
            detected_format=safety.detected_format,
            byte_size=target.stat().st_size,
            sha256=digest,
            sha256_verified=rehash == digest,
            scan_status="passed" if safety.ok else "failed",
            notes=safety.reasons,
        )

    def available_sheets(self, record: RawFileRecord) -> list[str]:
        from insightflow_ingestion.readers import list_sheets

        path = self.stored_path(record)
        if record.detected_format == "xlsx":
            return list_sheets(path)
        return []

    def stored_path(self, record: RawFileRecord) -> Path:
        suffix = Path(record.filename).suffix.lower() or ".csv"
        return self.owner_dir() / "uploads" / f"{record.sha256[:16]}{suffix}"

    def ingest(
        self,
        record: RawFileRecord,
        *,
        sheet_name: str | None = None,
        max_rows: int | None = None,
        max_columns: int | None = None,
        has_header: bool = True,
    ) -> IngestionOutcome:
        """Parse the stored original and materialise an immutable snapshot."""
        started = time.perf_counter()
        settings = get_settings()
        path = self.stored_path(record)
        if not path.exists():
            raise UnsupportedInputError("stored original is missing", artifact_id=record.artifact_id)

        options = ReadOptions(
            sheet_name=sheet_name,
            has_header=has_header,
            max_rows=max_rows or settings.upload.max_rows,
            max_columns=max_columns or settings.upload.max_columns,
        )
        read = read_table(path, options)

        table = read.table.with_id(source_file_hash=record.sha256, sheet_name=read.sheet_name)
        if table.row_count == 0:
            raise UnsupportedInputError(
                "no data rows found after parsing",
                sheet=read.sheet_name,
                suggestions=["Check that the header row is on the first line."],
            )

        dataset_id = table.dataset_id or derive_id("ds", {"sha256": record.sha256})
        dataset_path = self.dataset_dir(dataset_id=dataset_id) / "v1.parquet"
        artifact = write_parquet(table.frame, dataset_path, artifact_id=f"{dataset_id}_v1")

        snapshot = DatasetSnapshot(
            dataset_id=dataset_id,
            artifact_id=artifact.artifact_id,
            version=1,
            row_count=table.row_count,
            column_count=len(table.columns),
            columns=table.columns,
            source_file_hash=record.sha256,
            sheet_name=read.sheet_name,
            created_from="raw",
        )
        raw_artifact = ArtifactRef(
            artifact_id=record.artifact_id,
            kind=ArtifactKind.RAW_FILE,
            path=str(path),
            byte_size=record.byte_size,
            sha256=record.sha256,
            mime_type=None,
        )

        return IngestionOutcome(
            record=record,
            safety=SafetyReport(
                ok=True,
                detected_format=record.detected_format,
                sheet_names=read.available_sheets,
            ),
            read=read,
            snapshot=snapshot,
            raw_artifact=raw_artifact,
            table=table,
            dataset_path=dataset_path,
            elapsed_seconds=time.perf_counter() - started,
        )
