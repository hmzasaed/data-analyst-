"""Owner-scoped artifact store.

Two rules:

* every read and write is scoped to an owner, so a job can never reach another
  tenant's files;
* paths are derived from artifact ids, never taken from user input, so a crafted
  filename cannot escape the artifact root.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    InsightFlowError,
    file_sha256,
)

#: How an artifact kind maps onto a directory under the job folder.
_KIND_DIRS: dict[ArtifactKind, str] = {
    ArtifactKind.RAW_FILE: "raw",
    ArtifactKind.DATASET_SNAPSHOT: "datasets",
    ArtifactKind.DATASET_CSV: "datasets",
    ArtifactKind.TABLE_CSV: "tables",
    ArtifactKind.TABLE_JSON: "tables",
    ArtifactKind.CHART_PNG: "charts",
    ArtifactKind.CHART_SVG: "charts",
    ArtifactKind.REPORT_HTML: "reports",
    ArtifactKind.REPORT_MARKDOWN: "reports",
    ArtifactKind.REPORT_PDF: "reports",
    ArtifactKind.REPORT_JSON: "reports",
    ArtifactKind.CODE: "code",
    ArtifactKind.LOG: "logs",
    ArtifactKind.AUDIT: "audit",
}

_SUFFIX: dict[ArtifactKind, str] = {
    ArtifactKind.DATASET_SNAPSHOT: ".parquet",
    ArtifactKind.DATASET_CSV: ".csv",
    ArtifactKind.TABLE_CSV: ".csv",
    ArtifactKind.TABLE_JSON: ".json",
    ArtifactKind.CHART_PNG: ".png",
    ArtifactKind.CHART_SVG: ".svg",
    ArtifactKind.REPORT_HTML: ".html",
    ArtifactKind.REPORT_MARKDOWN: ".md",
    ArtifactKind.REPORT_PDF: ".pdf",
    ArtifactKind.REPORT_JSON: ".json",
    ArtifactKind.CODE: ".py",
    ArtifactKind.LOG: ".log",
    ArtifactKind.AUDIT: ".jsonl",
    ArtifactKind.RAW_FILE: ".bin",
}


class AccessDeniedError(InsightFlowError):
    """A job tried to reach artifacts outside its own owner scope."""

    code = "access_denied"


class ArtifactStore:
    """Filesystem artifact storage with per-owner isolation."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    # ------------------------------------------------------------------ paths
    def owner_dir(self, owner_id: str) -> Path:
        safe = _safe_segment(owner_id)
        path = (self.root / safe).resolve()
        if not str(path).startswith(str(self.root)):
            raise AccessDeniedError("owner directory escapes the artifact root", owner_id=owner_id)
        return path

    def job_dir(self, owner_id: str, job_id: str) -> Path:
        safe = _safe_segment(job_id)
        path = (self.owner_dir(owner_id) / "jobs" / safe).resolve()
        if not str(path).startswith(str(self.owner_dir(owner_id))):
            raise AccessDeniedError("job directory escapes the owner directory", job_id=job_id)
        return path

    def path_for(self, owner_id: str, job_id: str, kind: ArtifactKind, artifact_id: str) -> Path:
        directory = self.job_dir(owner_id, job_id) / _KIND_DIRS.get(kind, "misc")
        return directory / f"{_safe_segment(artifact_id)}{_SUFFIX.get(kind, '')}"

    def resolve(self, reference: ArtifactRef, *, owner_id: str, job_id: str) -> Path:
        """Turn a stored reference back into a path, re-checking ownership.

        Reference paths are re-derived from the owner and job rather than trusted,
        so a tampered or copied artifact cannot be used to reach another job.
        """
        expected = self.path_for(owner_id, job_id, reference.kind, reference.artifact_id)
        if Path(reference.path).name != expected.name:
            raise AccessDeniedError(
                "artifact reference does not belong to this job",
                artifact_id=reference.artifact_id,
                expected_name=expected.name,
                provided_name=Path(reference.path).name,
            )
        return expected

    # ------------------------------------------------------------------ writes
    def write_bytes(
        self,
        owner_id: str,
        job_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        payload: bytes,
    ) -> ArtifactRef:
        path = self.path_for(owner_id, job_id, kind, artifact_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return ArtifactRef(
            artifact_id=artifact_id,
            kind=kind,
            path=str(path),
            row_count=_csv_row_count(payload) if kind is ArtifactKind.TABLE_CSV else None,
            byte_size=len(payload),
            sha256=file_sha256(path),
            mime_type=_mime_for(kind),
            created_at=datetime.now(UTC).isoformat(),
        )

    def adopt(self, owner_id: str, job_id: str, reference: ArtifactRef) -> ArtifactRef:
        """Copy an artifact written by a stage into the job's own folder."""
        source = Path(reference.path)
        if not source.exists():
            raise InsightFlowError(
                "artifact to adopt does not exist", artifact_id=reference.artifact_id
            )
        destination = self.path_for(owner_id, job_id, reference.kind, reference.artifact_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copyfile(source, destination)
        return ArtifactRef(
            artifact_id=reference.artifact_id,
            kind=reference.kind,
            path=str(destination),
            row_count=reference.row_count,
            byte_size=destination.stat().st_size,
            sha256=file_sha256(destination),
            mime_type=reference.mime_type or _mime_for(reference.kind),
            created_at=datetime.now(UTC).isoformat(),
        )

    def write_text(
        self,
        owner_id: str,
        job_id: str,
        kind: ArtifactKind,
        artifact_id: str,
        text: str,
    ) -> ArtifactRef:
        return self.write_bytes(owner_id, job_id, kind, artifact_id, text.encode("utf-8"))

    # ------------------------------------------------------------------- reads
    def read_bytes(self, owner_id: str, job_id: str, reference: ArtifactRef) -> bytes:
        path = self.resolve(reference, owner_id=owner_id, job_id=job_id)
        if not path.exists():
            raise InsightFlowError(
                "artifact is missing", artifact_id=reference.artifact_id
            )
        return path.read_bytes()

    def read_text(self, owner_id: str, job_id: str, reference: ArtifactRef) -> str:
        return self.read_bytes(owner_id, job_id, reference).decode("utf-8", errors="replace")

    def exists(self, owner_id: str, job_id: str, reference: ArtifactRef) -> bool:
        try:
            return self.resolve(reference, owner_id=owner_id, job_id=job_id).exists()
        except AccessDeniedError:
            return False

    def list_artifacts(self, owner_id: str, job_id: str, kind: ArtifactKind | None = None) -> list[str]:
        root = self.job_dir(owner_id, job_id)
        if not root.exists():
            return []
        directory = root / (_KIND_DIRS.get(kind, "")) if kind else root
        if not directory.exists():
            return []
        return sorted(str(path) for path in directory.rglob("*") if path.is_file())

    # --------------------------------------------------------------- retention
    def purge_expired(self, *, default_retention_days: int = 7, now: datetime | None = None) -> list[str]:
        """Delete job folders past their retention window. Returns purged job ids."""
        moment = now or datetime.now(UTC)
        cutoff = moment - timedelta(days=default_retention_days)
        purged: list[str] = []
        if not self.root.exists():
            return purged
        for owner in self.root.iterdir():
            jobs = owner / "jobs"
            if not jobs.is_dir():
                continue
            for job in jobs.iterdir():
                if not job.is_dir():
                    continue
                if _modified_before(job, cutoff):
                    shutil.rmtree(job, ignore_errors=True)
                    purged.append(job.name)
        return purged

    def purge_job(self, owner_id: str, job_id: str) -> bool:
        path = self.job_dir(owner_id, job_id)
        if not path.exists():
            return False
        shutil.rmtree(path, ignore_errors=True)
        return True


def _safe_segment(value: str) -> str:
    """Reduce a value to a single safe path segment."""
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value)
    cleaned = cleaned.strip("._") or "unnamed"
    return cleaned[:128]


def _csv_row_count(payload: bytes) -> int | None:
    """Data rows in a CSV payload, excluding the header.

    A `table_csv` artifact without a row count is a defect the `ArtifactRef`
    contract refuses, so the count is derived here rather than left to callers.
    """
    if len(payload) > 32 * 1024 * 1024:
        return None
    lines = payload.decode("utf-8", errors="replace").splitlines()
    return max(len([line for line in lines if line.strip()]) - 1, 0)


def _modified_before(path: Path, cutoff: datetime) -> bool:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    return stamp < cutoff


def _mime_for(kind: ArtifactKind) -> str:
    return {
        ArtifactKind.DATASET_SNAPSHOT: "application/vnd.apache.parquet",
        ArtifactKind.DATASET_CSV: "text/csv",
        ArtifactKind.TABLE_CSV: "text/csv",
        ArtifactKind.TABLE_JSON: "application/json",
        ArtifactKind.CHART_PNG: "image/png",
        ArtifactKind.CHART_SVG: "image/svg+xml",
        ArtifactKind.REPORT_HTML: "text/html",
        ArtifactKind.REPORT_MARKDOWN: "text/markdown",
        ArtifactKind.REPORT_PDF: "application/pdf",
        ArtifactKind.REPORT_JSON: "application/json",
        ArtifactKind.CODE: "text/x-python",
        ArtifactKind.LOG: "text/plain",
        ArtifactKind.AUDIT: "application/x-ndjson",
        ArtifactKind.RAW_FILE: "application/octet-stream",
    }.get(kind, "application/octet-stream")