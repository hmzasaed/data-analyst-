"""FastAPI application: upload, review, progress, findings and exports.

Design choices worth stating:

* authentication is a single owner header rather than a full identity provider.
  The MVP needs to prove tenant isolation, and an opaque owner id is enough to do
  that honestly; swapping in real auth later changes one dependency.
* artifacts are served only through an owner-checked route, never from a static
  directory mount.
* where a job runs is `INSIGHTFLOW_EXECUTION_MODE`:
  - `inline` runs it inside the request (tests, scripts);
  - `background` returns at once and runs it on a thread in this process, so a UI
    can poll the lifecycle live (the default for `insightflow-api` local runs);
  - `worker` only queues it; `insightflow-worker` claims and runs it.
  Unset, the legacy `INSIGHTFLOW_INLINE_EXECUTION` flag picks `inline` or `worker`.
"""

from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from insightflow_contracts import (
    ArtifactKind,
    ArtifactRef,
    DatasetProfile,
    FeatureEngineeringResult,
    FeatureOptions,
    FeatureSelectionResult,
    Job,
    JobObjective,
    JobStage,
    JobStatus,
    PreprocessingOptions,
    PreprocessingResult,
    UnsupportedInputError,
    get_settings,
)
from insightflow_ingestion import IngestionService
from insightflow_orchestration import JobFactory, JobOrchestrator, build_context, job_progress
from insightflow_orchestration.persistence import JobSnapshot, load_json, load_snapshot
from insightflow_reporting import build_bundle, embed_charts
from insightflow_storage import ArtifactStore, AuditEvent, StateStore
from insightflow_storage.artifacts import _KIND_DIRS
from pydantic import BaseModel, Field

settings = get_settings()
log = logging.getLogger("insightflow.api")

#: Running the pipeline inside the request is for development only.
INLINE = os.getenv("INSIGHTFLOW_INLINE_EXECUTION", "true").lower() in {"1", "true", "yes"}
EXECUTION_MODE = os.getenv("INSIGHTFLOW_EXECUTION_MODE", "inline" if INLINE else "worker").lower()
if EXECUTION_MODE not in {"inline", "background", "worker"}:
    raise RuntimeError(f"INSIGHTFLOW_EXECUTION_MODE must be inline, background or worker, not {EXECUTION_MODE!r}")
INLINE = EXECUTION_MODE == "inline"

#: Background jobs get their own small pool so a long analysis never starves the
#: request threadpool. Jobs in flight are tracked so a double click cannot run one twice.
_executor = ThreadPoolExecutor(
    max_workers=int(os.getenv("INSIGHTFLOW_BACKGROUND_WORKERS", "2")), thread_name_prefix="job"
)
_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()

#: The built frontend (apps/web/dist), served at `/` when present.
WEB_DIST = Path(os.getenv("INSIGHTFLOW_WEB_DIST", Path(__file__).resolve().parents[3] / "web" / "dist"))

MAX_UPLOAD_BYTES = settings.upload.max_bytes

state = StateStore(settings.state_database_url)
artifacts = ArtifactStore(settings.artifact_path("store"))
factory = JobFactory(
    state=state,
    artifact_store=artifacts,
    ingestion=IngestionService(settings.artifact_path("raw")),
)


# ------------------------------------------------------------------ dependencies


async def current_owner(
    x_owner_id: Annotated[str | None, Header(alias="X-Owner-Id")] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve the owning tenant.

    The owner id is never taken from the request body or the path, so a user cannot
    address another tenant's job by guessing an id.
    """
    if x_owner_id:
        owner = x_owner_id.strip()
    elif authorization and authorization.lower().startswith("bearer "):
        owner = authorization.split(" ", 1)[1].strip()
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Provide the X-Owner-Id header (or a bearer token) to identify the tenant.",
        )
    if not owner or len(owner) > 128:
        raise HTTPException(status_code=400, detail="Invalid owner identifier.")
    return owner


Owner = Annotated[str, Depends(current_owner)]


def _load(job_id: str, owner: str) -> Job:
    job = state.load_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' does not exist")
    if job.owner_id != owner:
        # 404 rather than 403: do not confirm that another tenant's job exists.
        raise HTTPException(status_code=404, detail=f"job '{job_id}' does not exist")
    return job


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.artifact_root.mkdir(parents=True, exist_ok=True)
    settings.artifact_path("policies").mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="InsightFlow",
    version="0.1.0",
    summary="Secure agentic data analysis platform",
    description=(
        "Upload a CSV or XLSX file, review the detected data quality, and receive a report "
        "in which every material claim links to a computed value and a stored artifact."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "INSIGHTFLOW_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------- schemas
class UploadResponse(BaseModel):
    job_id: str
    status: str
    filename: str
    file_hash: str
    available_sheets: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProgressResponse(BaseModel):
    job_id: str
    status: str
    stage: str
    next_stage: str | None = None
    progress: float
    dataset_version: int
    completed_stages: list[str] = Field(default_factory=list)
    error: str | None = None
    filename: str | None = None
    question: str | None = None
    require_review: bool = False
    created_at: str | None = None
    updated_at: str | None = None
    stages: list[dict[str, Any]] = Field(default_factory=list)


class ReviewRequest(BaseModel):
    """Approve or reject individual cleaning operations."""

    accept: list[str] = Field(default_factory=list)
    reject: list[str] = Field(default_factory=list)
    sheet_name: str | None = None
    question: str | None = None
    #: Continue the job once the decision is recorded, if it is waiting on one.
    resume: bool = True


class JobListItem(BaseModel):
    job_id: str
    status: str
    filename: str | None = None
    stage: str
    progress: float
    created_at: str


# ---------------------------------------------------------------------- routes
@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "llm_enabled": settings.llm.enabled,
        "sandbox_runtime": settings.sandbox.container_runtime or "local_process",
        "inline_execution": INLINE,
        "execution_mode": EXECUTION_MODE,
        "state_backend": getattr(state, "backend", "sqlite"),
    }


@app.get(f"{settings.api_prefix}/tools")
async def list_tools() -> dict[str, Any]:
    """The registered analysis catalogue: everything the agent is allowed to call."""
    from insightflow_analytics import REGISTRY

    return {
        "count": len(REGISTRY.names()),
        "tools": [
            {
                "name": spec.name,
                "version": spec.version,
                "analysis_type": spec.analysis_type,
                "summary": spec.summary,
                "parameters": spec.input_schema,
                "tags": spec.tags,
            }
            for spec in REGISTRY.catalogue()
        ],
    }


@app.post(
    f"{settings.api_prefix}/jobs",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload(
    owner: Owner,
    file: Annotated[UploadFile, File()],
    question: Annotated[str | None, Form()] = None,
    business_context: Annotated[str | None, Form()] = None,
    sheet_name: Annotated[str | None, Form()] = None,
    consent_third_party_llm: Annotated[bool, Form()] = False,
    require_review: Annotated[bool, Form()] = False,
    preprocessing: Annotated[str | None, Form()] = None,
    features: Annotated[str | None, Form()] = None,
) -> UploadResponse:
    """Register an upload and start a job.

    `preprocessing` is a JSON object of `PreprocessingOptions`; omitted fields keep
    their defaults, and unknown fields are refused.
    """
    try:
        options = (
            PreprocessingOptions.model_validate_json(preprocessing)
            if preprocessing and preprocessing.strip()
            else PreprocessingOptions()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid preprocessing options: {exc}") from exc
    try:
        feature_options = (
            FeatureOptions.model_validate_json(features)
            if features and features.strip()
            else FeatureOptions()
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid feature options: {exc}") from exc
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds the {MAX_UPLOAD_BYTES:,} byte limit",
        )
    if not payload:
        raise HTTPException(status_code=400, detail="the uploaded file is empty")

    suffix = Path(file.filename or "upload.csv").suffix.lower()
    if settings.upload.allowed_extensions and suffix not in settings.upload.allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=(
                f"unsupported file type '{suffix or '(none)'}'. "
                f"Supported: {', '.join(settings.upload.allowed_extensions)}"
            ),
        )

    staging = settings.artifact_path("staging") / f"{owner}-{_safe(file.filename or 'upload.csv')}"
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(payload)

    try:
        job, record = factory.create(
            staging,
            owner_id=owner,
            filename=file.filename or staging.name,
            objective=JobObjective(
                question=question, business_context=business_context, is_exploratory=not bool(question)
            ),
            sheet_name=sheet_name,
            consent_third_party_llm=consent_third_party_llm,
        )
    except UnsupportedInputError as exc:
        staging.unlink(missing_ok=True)
        raise HTTPException(status_code=415, detail={"message": exc.message, **exc.detail}) from exc

    job.require_review = require_review
    job.preprocessing = options
    job.features = feature_options
    state.save_job(job)

    job = _dispatch(job)

    return UploadResponse(
        job_id=job.job_id,
        status=job.status.value,
        filename=record.filename,
        file_hash=record.sha256,
        available_sheets=job.available_sheets,
        warnings=[],
    )


def _run_inline(job: Job, until: JobStage | None = None) -> Job:
    """Run (or resume) the pipeline synchronously in the calling thread."""
    record = factory.raw_record(job)
    context = build_context(
        job=job,
        raw_record=record,
        artifact_store=artifacts,
        state=state,
        ingestion=factory.ingestion,
    )
    return JobOrchestrator(context).run(until=until, resume=True)


def _run_tracked(job_id: str, until: JobStage | None) -> None:
    """Background entry point. Failures land on the job, never in the void."""
    try:
        job = state.load_job(job_id)
        if job is not None:
            _run_inline(job, until)
    except Exception as exc:  # pragma: no cover - defensive: stages record their own errors
        log.exception("background job %s crashed", job_id)
        job = state.load_job(job_id)
        if job is not None:
            job.status = JobStatus.FAILED
            job.error = f"[background_error] {type(exc).__name__}: {exc}"
            state.save_job(job)
    finally:
        with _in_flight_lock:
            _in_flight.discard(job_id)


def _dispatch(job: Job, until: JobStage | None = None) -> Job:
    """Run, queue or schedule a job according to the execution mode."""
    if EXECUTION_MODE == "inline":
        return _run_inline(job, until)
    if EXECUTION_MODE == "worker":
        job.status = JobStatus.PENDING
        return state.save_job(job)
    with _in_flight_lock:
        if job.job_id in _in_flight:
            return job
        _in_flight.add(job.job_id)
    job.status = JobStatus.RUNNING
    state.save_job(job)
    _executor.submit(_run_tracked, job.job_id, until)
    return job


@app.get(f"{settings.api_prefix}/jobs", response_model=list[JobListItem])
async def list_jobs(owner: Owner, limit: int = 50) -> list[JobListItem]:
    jobs = state.list_jobs(owner, limit=min(limit, 200))
    return [
        JobListItem(
            job_id=job.job_id,
            status=job.status.value,
            filename=job.source_filename,
            stage=job.current_stage.value,
            progress=job.progress,
            created_at=job.created_at.isoformat(),
        )
        for job in jobs
    ]


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}", response_model=ProgressResponse)
async def get_job(job_id: str, owner: Owner) -> ProgressResponse:
    job = _load(job_id, owner)
    return _progress(job)


def _progress(job: Job) -> ProgressResponse:
    return ProgressResponse(
        **job_progress(job),
        filename=job.source_filename,
        question=job.objective.question,
        require_review=job.require_review,
        created_at=job.created_at.isoformat(),
        updated_at=job.updated_at.isoformat(),
        stages=[record.model_dump(mode="json") for record in job.stages],
    )


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/profile", response_model=DatasetProfile)
async def get_profile(job_id: str, owner: Owner) -> DatasetProfile:
    job = _load(job_id, owner)
    snapshot = _snapshot(job)
    if snapshot.profile is None:
        raise HTTPException(status_code=409, detail="the dataset has not been profiled yet")
    return snapshot.profile

@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/cleaning")
async def get_cleaning(job_id: str, owner: Owner) -> dict[str, Any]:
    job = _load(job_id, owner)
    snapshot = _snapshot(job)
    plan = snapshot.cleaning_plan
    if plan is None:
        raise HTTPException(status_code=409, detail="no cleaning plan has been produced yet")
    # Show the user's recorded decisions on top of the stored proposal.
    for operation in plan.operations:
        if operation.operation_id in job.cleaning_decisions:
            operation.accepted = job.cleaning_decisions[operation.operation_id]
    execution = snapshot.cleaning
    return {
        "awaiting_review": job.status is JobStatus.AWAITING_REVIEW,
        "review_submitted": job.review_submitted,
        "summary": plan.summary().model_dump(mode="json"),
        "operations": [op.model_dump(mode="json") for op in plan.operations],
        "applied": [entry.model_dump(mode="json") for entry in (execution.applied if execution else [])],
        "rejected": [op.operation_id for op in (execution.rejected if execution else [])],
    }

@app.post(f"{settings.api_prefix}/jobs/{{job_id}}/cleaning/decision")
async def decide_cleaning(job_id: str, owner: Owner, decision: ReviewRequest) -> dict[str, Any]:
    """Record which proposed operations the user approved."""
    job = _load(job_id, owner)
    snapshot = _snapshot(job)
    plan = snapshot.cleaning_plan
    if plan is None:
        raise HTTPException(status_code=409, detail="no cleaning plan has been produced yet")

    known = {op.operation_id for op in plan.operations}
    unknown = (set(decision.accept) | set(decision.reject)) - known
    if unknown:
        raise HTTPException(status_code=400, detail=f"unknown operation id(s): {sorted(unknown)}")

    # Decisions are stored on the job itself, so they survive a restart and the
    # orchestrator applies exactly what the user chose when cleaning runs.
    decisions = dict(job.cleaning_decisions)
    decisions.update(dict.fromkeys(decision.accept, True))
    decisions.update(dict.fromkeys(decision.reject, False))

    approved: set[str] = set()
    for operation in plan.operations:
        if operation.operation_id in decisions:
            operation.accepted = decisions[operation.operation_id]
        if operation.effective_status.value == "automatic":
            approved.add(operation.operation_id)

    # Once cleaning has run, a decision is recorded but cannot change this job's data.
    applied = JobStage.CLEANING_APPLIED in job.completed_stages()
    job.cleaning_decisions = decisions
    job.review_submitted = True
    state.audit(
        job_id,
        owner,
        _audit_event(
            "cleaning.decision",
            {"accepted": len(decision.accept), "rejected": len(decision.reject),
             "after_cleaning_applied": applied},
        ),
    )
    if decision.sheet_name:
        job.source_sheet = decision.sheet_name
    if decision.question and JobStage.PLANNED not in job.completed_stages():
        job.objective = job.objective.model_copy(
            update={"question": decision.question, "is_exploratory": False}
        )
    state.save_job(job)

    if decision.resume and job.status is JobStatus.AWAITING_REVIEW:
        job = _dispatch(job)
    return {
        "approved": sorted(approved),
        "operation_count": len(plan.operations),
        "status": job.status.value,
        "takes_effect": not applied,
    }

@app.post(f"{settings.api_prefix}/jobs/{{job_id}}/resume")
async def resume_job(job_id: str, owner: Owner, until: JobStage | None = None) -> ProgressResponse:
    job = _load(job_id, owner)
    if job.status is JobStatus.COMPLETED and until is None:
        raise HTTPException(status_code=409, detail="this job has already completed")
    if job.status is JobStatus.CANCELLED:
        raise HTTPException(status_code=409, detail="a cancelled job cannot be resumed; upload again")
    if job.status is JobStatus.AWAITING_REVIEW and not job.review_submitted:
        raise HTTPException(
            status_code=409, detail="this job is waiting for a cleaning decision; submit one first"
        )
    if job.status is JobStatus.FAILED:
        job.error = None
    return _progress(_dispatch(job, until))


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/preprocessing", response_model=PreprocessingResult)
async def get_preprocessing(job_id: str, owner: Owner) -> PreprocessingResult:
    """What preprocessing did: analysed-data changes, the export, and the options used."""
    job = _load(job_id, owner)
    payload = load_json(artifacts, job.owner_id, job.job_id, "preprocessing")
    if payload is None:
        raise HTTPException(status_code=409, detail="preprocessing has not run yet")
    return PreprocessingResult.model_validate(payload)


class FeaturesResponse(BaseModel):
    engineering: FeatureEngineeringResult
    selection: FeatureSelectionResult


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/features", response_model=FeaturesResponse)
async def get_features(job_id: str, owner: Owner) -> FeaturesResponse:
    """What the feature engineering and feature selection agents did."""
    job = _load(job_id, owner)
    engineering = load_json(artifacts, job.owner_id, job.job_id, "feature_engineering")
    selection = load_json(artifacts, job.owner_id, job.job_id, "feature_selection")
    if engineering is None or selection is None:
        raise HTTPException(status_code=409, detail="feature engineering and selection have not run yet")
    return FeaturesResponse(
        engineering=FeatureEngineeringResult.model_validate(engineering),
        selection=FeatureSelectionResult.model_validate(selection),
    )


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/findings")
async def get_findings(job_id: str, owner: Owner) -> dict[str, Any]:
    job = _load(job_id, owner)
    snapshot = _snapshot(job)
    if not snapshot.findings:
        raise HTTPException(status_code=409, detail="verification has not run yet")
    return {
        "findings": [finding.model_dump(mode="json") for finding in snapshot.findings],
        "verification": snapshot.verification.model_dump(mode="json") if snapshot.verification else None,
        "anomalies": snapshot.anomalies,
        "warnings": snapshot.warnings,
    }

@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/audit")
async def get_audit(job_id: str, owner: Owner) -> dict[str, Any]:
    job = _load(job_id, owner)
    return {
        "job_id": job.job_id,
        "events": state.audit_trail(job_id),
        "stages": [record.model_dump(mode="json") for record in state.stage_history(job_id)],
    }


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/report", response_class=HTMLResponse)
async def get_report(
    job_id: str, owner: Owner, format: str = "html", embed: bool = False  # noqa: A002
) -> Response:
    """Serve the stored report.

    The report is rebuilt from persisted artifacts rather than re-running the
    analysis, so a reader always sees the numbers that were actually verified.

    `embed=true` inlines the charts, producing one self-contained document. A
    browser cannot attach the owner header to `<img>` requests, so this is the
    form a UI should display or download.
    """
    job = _load(job_id, owner)
    snapshot = _snapshot(job)
    if snapshot.document is None:
        raise HTTPException(status_code=409, detail="the report has not been generated yet")

    bundle = build_bundle(
        snapshot.document,
        findings=snapshot.findings,
        verification=snapshot.verification,
        charts=_artifact_refs(job, ArtifactKind.CHART_PNG),
        tables=_artifact_refs(job, ArtifactKind.TABLE_CSV),
    )
    base = f"{settings.api_prefix}/jobs/{job_id}/artifacts"
    if format == "markdown":
        return PlainTextResponse(bundle.to_markdown(), media_type="text/markdown")
    if format == "json":
        return JSONResponse(bundle.to_dict())
    if embed:
        charts = _artifact_refs(job, ArtifactKind.CHART_PNG)
        return HTMLResponse(bundle.to_html(embedded=embed_charts(charts)))
    return HTMLResponse(bundle.to_html(chart_base_url=base))


@app.get(f"{settings.api_prefix}/jobs/{{job_id}}/artifacts/{{artifact_id}}")
async def get_artifact(job_id: str, artifact_id: str, owner: Owner) -> Response:
    """Serve one artifact, after confirming it belongs to this job."""
    # Ownership is proven before any artifact path is built or read.
    _load(job_id, owner)
    kind_directory = artifacts.job_dir(owner, job_id)
    matches = [
        path for path in kind_directory.rglob(f"{artifact_id}.*") if path.is_file()
    ] if kind_directory.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail=f"artifact '{artifact_id}' does not exist")
    path = matches[0]
    return Response(
        content=path.read_bytes(),
        media_type=_media_for(path.suffix),
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@app.delete(f"{settings.api_prefix}/jobs/{{job_id}}")
async def delete_job(job_id: str, owner: Owner) -> dict[str, Any]:
    _load(job_id, owner)
    with _in_flight_lock:
        running = job_id in _in_flight
    if running:
        raise HTTPException(status_code=409, detail="the job is still running; cancel it first")
    return {"deleted": factory.delete(job_id)}


@app.post(f"{settings.api_prefix}/jobs/{{job_id}}/cancel")
async def cancel_job(job_id: str, owner: Owner) -> dict[str, Any]:
    _load(job_id, owner)
    return {"cancelled": factory.cancel(job_id)}


# ------------------------------------------------------------------- helpers
def _snapshot(job) -> JobSnapshot:
    """Load a job's stored outputs. Reading never re-runs the analysis."""
    return load_snapshot(artifacts, job)


def _artifact_refs(job, kind: ArtifactKind) -> list[ArtifactRef]:
    """Rebuild artifact references from the files on disk."""
    directory = _KIND_DIRS.get(kind)
    if directory is None:
        return []
    root = artifacts.job_dir(job.owner_id, job.job_id) / directory
    if not root.exists():
        return []
    return [
        ArtifactRef(
            artifact_id=path.stem,
            kind=kind,
            path=str(path),
            row_count=_row_count(path) if kind is ArtifactKind.TABLE_CSV else None,
            byte_size=path.stat().st_size,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]


def _row_count(path) -> int | None:
    if path.suffix.lower() != ".csv":
        return None
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _safe(name: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in name)[:120]


def _media_for(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".svg": "image/svg+xml",
        ".csv": "text/csv",
        ".json": "application/json",
        ".html": "text/html",
        ".md": "text/markdown",
        ".parquet": "application/vnd.apache.parquet",
    }.get(suffix.lower(), "application/octet-stream")


def _audit_event(action: str, detail: dict[str, Any]) -> AuditEvent:
    return AuditEvent(actor="user", action=action, detail=detail)


# The built frontend, mounted last so every API route above takes precedence.
if (WEB_DIST / "index.html").exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


__all__ = ["app"]