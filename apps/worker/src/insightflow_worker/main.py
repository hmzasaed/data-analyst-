"""Queue worker.

The worker claims pending jobs and drives them through the orchestrator. The queue
is the job table itself: `FOR UPDATE SKIP LOCKED`-style claiming via an atomic status
transition keeps several workers from processing the same job, which means the MVP
needs no Redis or Celery while still being multi-worker safe on SQLite and
PostgreSQL.

Set `INSIGHTFLOW_WORKER_INLINE=1` to run a single job in the foreground, which is
what the integration tests do.
"""

from __future__ import annotations

import os
import signal
import sys
import time
from dataclasses import dataclass

import structlog
from insightflow_contracts import JobStatus, get_settings
from insightflow_orchestration import JobFactory, JobOrchestrator, build_context
from insightflow_storage import ArtifactStore, StateStore

log = structlog.get_logger("insightflow.worker")

POLL_INTERVAL_SECONDS = float(os.getenv("INSIGHTFLOW_POLL_SECONDS", "2.0"))
MAX_JOBS = int(os.getenv("INSIGHTFLOW_WORKER_MAX_JOBS", "0"))


@dataclass
class Worker:
    state: StateStore
    factory: JobFactory

    def __init__(self) -> None:
        settings = get_settings()
        self.state = StateStore(settings.state_database_url)
        artifacts = ArtifactStore(settings.artifact_path("store"))
        from insightflow_ingestion import IngestionService

        self.factory = JobFactory(
            state=self.state,
            artifact_store=artifacts,
            ingestion=IngestionService(settings.artifact_path("raw")),
        )

    # ------------------------------------------------------------------ claim
    def claim(self) -> list[str]:
        """Atomically move pending jobs to running and return their ids.

        The status transition is the claim: only the worker's own successful update
        wins, so two workers polling at once cannot both take the same job.
        """
        claimed: list[str] = []
        for job in self.state.jobs_pending():
            if job.status is not JobStatus.PENDING:
                continue
            if self.state.claim_job(job.job_id) is not None:
                claimed.append(job.job_id)
        return claimed

    # ------------------------------------------------------------------- run
    def run_once(self) -> int:
        """Process every currently pending job. Returns how many were run."""
        job_ids = self.claim()
        for job_id in job_ids:
            self.run_job(job_id)
        return len(job_ids)

    def run_job(self, job_id: str) -> bool:
        job = self.state.load_job(job_id)
        if job is None:
            log.warning("job disappeared", job_id=job_id)
            return False
        log.info("job.start", job_id=job_id, stage=job.current_stage.value, version=job.dataset_version)
        try:
            record = self.factory.raw_record(job)
            context = build_context(
                job=job,
                raw_record=record,
                artifact_store=self.factory.artifact_store,
                state=self.state,
                ingestion=self.factory.ingestion,
            )
            result = JobOrchestrator(context).run(resume=True)
        except Exception as exc:
            log.exception("job.crashed", job_id=job_id)
            result = self._fail(job, exc)
            return False

        log.info(
            "job.finished",
            job_id=job_id,
            status=result.status.value,
            dataset_version=result.dataset_version,
        )
        return result.status is JobStatus.COMPLETED

    def _fail(self, job, exc: Exception):
        from insightflow_contracts import utc_now

        job.status = JobStatus.FAILED
        job.error = f"[worker_error] {type(exc).__name__}: {exc}"
        job.updated_at = utc_now()
        self.state.save_job(job)
        from insightflow_storage import AuditEvent

        self.state.audit(
            job.job_id,
            job.owner_id,
            AuditEvent(actor="worker", action="job.crashed", outcome="error",
                       detail={"error": type(exc).__name__}),
        )
        return job

    # ------------------------------------------------------------------ loop
    def serve_forever(self) -> None:
        stopping = {"now": False}

        def handle_signal(signum, _frame):
            log.info("worker.stopping", signal=signum)
            stopping["now"] = True

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        processed = 0
        log.info("worker.started", poll_seconds=POLL_INTERVAL_SECONDS)
        while not stopping["now"]:
            try:
                count = self.run_once()
            except Exception:
                log.exception("worker.poll_failed")
                count = 0
            processed += count
            if MAX_JOBS and processed >= MAX_JOBS:
                log.info("worker.job_limit_reached", processed=processed)
                break
            if not count:
                time.sleep(POLL_INTERVAL_SECONDS)
        log.info("worker.stopped", processed=processed)


def main() -> None:
    worker = Worker()
    if os.getenv("INSIGHTFLOW_WORKER_INLINE", "false").lower() in {"1", "true", "yes"}:
        processed = worker.run_once()
        log.info("worker.inline_done", processed=processed)
        return
    worker.serve_forever()


if __name__ == "__main__":
    sys.exit(main())