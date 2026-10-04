"""Job creation and lifecycle helpers.

Separate from the orchestrator so the API can create and inspect jobs without
importing the execution machinery.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from insightflow_contracts import (
    ArtifactKind,
    Job,
    JobObjective,
    JobStatus,
    RawFileRecord,
    UnsupportedInputError,
    derive_id,
    get_settings,
    utc_now,
)
from insightflow_ingestion import IngestionService
from insightflow_storage import ArtifactStore, AuditEvent, StateStore


@dataclass
class JobFactory:
    """Creates jobs and holds the collaborators they need."""

    state: StateStore
    artifact_store: ArtifactStore
    ingestion: IngestionService

    @classmethod
    def from_settings(cls, *, state: StateStore | None = None) -> JobFactory:
        settings = get_settings()
        return cls(
            state=state or StateStore(settings.state_database_url),
            artifact_store=ArtifactStore(settings.artifact_path("store")),
            ingestion=IngestionService(settings.artifact_path("raw")),
        )

    # ------------------------------------------------------------------ create
    def create(
        self,
        source: str | Path,
        *,
        owner_id: str,
        filename: str | None = None,
        objective: JobObjective | None = None,
        sheet_name: str | None = None,
        consent_third_party_llm: bool = False,
    ) -> tuple[Job, RawFileRecord]:
        """Register an upload and create its job.

        Raises `UnsupportedInputError` rather than accepting a file the platform
        cannot parse, so the problem is visible at upload time rather than three
        stages later.
        """
        self.ingestion.owner_id = owner_id
        record = self.ingestion.register(source, filename=filename)

        sheets = self.ingestion.available_sheets(record)
        if sheet_name is not None and sheet_name not in sheets:
            raise UnsupportedInputError(
                f"sheet '{sheet_name}' is not in this workbook",
                available_sheets=sheets,
                suggestions=[f"Choose one of: {', '.join(sheets)}"] if sheets else None,
            )

        settings = get_settings()
        job = Job(
            job_id=derive_id(
                "job", {"sha256": record.sha256, "owner": owner_id, "at": utc_now().isoformat()}
            ),
            owner_id=owner_id,
            status=JobStatus.PENDING,
            source_artifact_id=record.artifact_id,
            source_filename=record.filename,
            source_file_hash=record.sha256,
            source_sheet=sheet_name,
            available_sheets=sheets,
            objective=objective or JobObjective(),
            retention_days=settings.retention_days,
            consent_processing=True,
            consent_third_party_llm=consent_third_party_llm,
            versions={"ingestion": "1"},
        )
        self.state.save_job(job)
        self.state.audit(
            job.job_id,
            owner_id,
            AuditEvent(
                actor="user",
                action="upload",
                detail={"sha256": record.sha256, "bytes": record.byte_size, "sheets": len(sheets)},
            ),
        )
        self.artifact_store.write_text(
            owner_id,
            job.job_id,
            ArtifactKind.AUDIT,
            "upload_record",
            record.model_dump_json(indent=2),
        )
        return job, record

    # ------------------------------------------------------------------ lookup
    def raw_record(self, job: Job) -> RawFileRecord:
        """Rebuild the upload record for a job from its stored metadata.

        Uses this factory's own ingestion service rather than ambient settings, so
        uploads and re-reads always resolve to the same directory.

        Re-registering re-verifies the stored file's hash against the one recorded at
        upload time, so tampering with the stored original is detected here.
        """
        self.ingestion.owner_id = job.owner_id
        path = self.ingestion.owner_dir() / "uploads" / f"{job.source_file_hash[:16]}{_suffix(job)}"
        if not path.exists():
            raise UnsupportedInputError(
                "the stored original for this job is missing",
                job_id=job.job_id,
                suggestion="Re-upload the file to create a new job.",
            )
        record = self.ingestion.register(path, filename=job.source_filename)
        if record.sha256 != job.source_file_hash:
            raise UnsupportedInputError(
                "the stored original no longer matches the hash recorded at upload",
                job_id=job.job_id,
                suggestion="Re-upload the file to create a new job.",
            )
        return record

    # --------------------------------------------------------------- lifecycle
    def mark_awaiting_review(self, job: Job) -> Job:
        job.status = JobStatus.AWAITING_REVIEW
        job.updated_at = utc_now()
        self.state.save_job(job)
        return job

    def cancel(self, job_id: str) -> bool:
        job = self.state.load_job(job_id)
        if job is None or job.status is JobStatus.COMPLETED:
            return False
        job.status = JobStatus.CANCELLED
        job.updated_at = utc_now()
        self.state.save_job(job)
        self.state.audit(job_id, job.owner_id, AuditEvent(actor="user", action="cancel"))
        return True

    def delete(self, job_id: str) -> bool:
        job = self.state.load_job(job_id)
        if job is None:
            return False
        self.artifact_store.purge_job(job.owner_id, job_id)
        self.state.delete_job(job_id)
        return True


def _suffix(job: Job) -> str:
    return Path(job.source_filename or "data.csv").suffix.lower() or ".csv"


def job_progress(job: Job) -> dict:
    """Progress payload for the UI."""
    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "stage": job.current_stage.value,
        "next_stage": (job.next_stage().value if job.next_stage() else None),
        "progress": job.progress,
        "dataset_version": job.dataset_version,
        "completed_stages": sorted(stage.value for stage in job.completed_stages()),
        "error": job.error,
    }