"""Job, stage and objective contracts.

The job state machine is explicit rather than an autonomous loop: stages are a
fixed, ordered list with typed statuses, so a failed job can be resumed at the
failed stage and the UI can always say what the agent is doing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field

from insightflow_contracts.features import FeatureOptions
from insightflow_contracts.preprocessing import PreprocessingOptions
from insightflow_contracts.profile import StrictModel


def utc_now() -> datetime:
    return datetime.now(UTC)


class JobStage(StrEnum):
    REGISTERED = "registered"
    INGESTED = "ingested"
    PROFILED = "profiled"
    INTENT_CAPTURED = "intent_captured"
    CLEANING_PROPOSED = "cleaning_proposed"
    CLEANING_REVIEWED = "cleaning_reviewed"
    CLEANING_APPLIED = "cleaning_applied"
    PREPROCESSED = "preprocessed"
    FEATURES_ENGINEERED = "features_engineered"
    FEATURES_SELECTED = "features_selected"
    PLANNED = "planned"
    EXECUTED = "executed"
    VERIFIED = "verified"
    REPORTED = "reported"
    FAILED = "failed"
    REJECTED = "rejected"


#: Linear happy path. Used for progress display and for resume ordering.
STAGE_ORDER: tuple[JobStage, ...] = (
    JobStage.REGISTERED,
    JobStage.INGESTED,
    JobStage.PROFILED,
    JobStage.INTENT_CAPTURED,
    JobStage.CLEANING_PROPOSED,
    JobStage.CLEANING_REVIEWED,
    JobStage.CLEANING_APPLIED,
    JobStage.PREPROCESSED,
    JobStage.FEATURES_ENGINEERED,
    JobStage.FEATURES_SELECTED,
    JobStage.PLANNED,
    JobStage.EXECUTED,
    JobStage.VERIFIED,
    JobStage.REPORTED,
)

TERMINAL_STAGES: frozenset[JobStage] = frozenset(
    {JobStage.REPORTED, JobStage.FAILED, JobStage.REJECTED}
)


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_REVIEW = "awaiting_review"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class JobObjective(StrictModel):
    """The user's business question plus the context needed to interpret it."""

    question: str | None = None
    business_context: str | None = None
    time_grain: str | None = None
    dimension_preferences: list[str] = Field(default_factory=list)
    measure_preferences: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    is_exploratory: bool = True


class StageRecord(StrictModel):
    """Append-only audit row for one stage attempt."""

    stage: JobStage
    status: str = Field(pattern="^(succeeded|failed|skipped|running)$")
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    attempt: int = Field(default=1, ge=1)
    detail: dict[str, object] = Field(default_factory=dict)
    error: str | None = None
    error_code: str | None = None
    duration_seconds: float | None = Field(default=None, ge=0)

    @property
    def duration(self) -> float:
        if self.finished_at is None:
            return 0.0
        return (self.finished_at - self.started_at).total_seconds()


class Job(StrictModel):
    job_id: str
    owner_id: str
    status: JobStatus = JobStatus.PENDING
    current_stage: JobStage = JobStage.REGISTERED
    source_artifact_id: str | None = None
    source_filename: str | None = None
    source_file_hash: str | None = None
    source_sheet: str | None = None
    available_sheets: list[str] = Field(default_factory=list)
    dataset_id: str | None = None
    dataset_version: int = Field(default=0, ge=0)
    objective: JobObjective = Field(default_factory=JobObjective)
    stages: list[StageRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    retention_days: int = Field(default=7, ge=1, le=365)
    consent_processing: bool = True
    consent_third_party_llm: bool = False
    #: Pause after proposing cleaning until the user approves or rejects operations.
    require_review: bool = False
    #: The user's cleaning decisions, operation id -> accepted. Survives restarts.
    cleaning_decisions: dict[str, bool] = Field(default_factory=dict)
    review_submitted: bool = False
    preprocessing: PreprocessingOptions = Field(default_factory=PreprocessingOptions)
    features: FeatureOptions = Field(default_factory=FeatureOptions)
    profile_digest: str | None = None
    plan_digest: str | None = None
    versions: dict[str, str] = Field(default_factory=dict)
    error: str | None = None

    def stage_record(self, stage: JobStage) -> StageRecord | None:
        for record in reversed(self.stages):
            if record.stage is stage and record.status == "succeeded":
                return record
        return None

    def completed_stages(self) -> set[JobStage]:
        """Stages that have succeeded, plus the implicit initial registration.

        `REGISTERED` is the state a job is created in rather than a stage that
        runs, so counting it keeps progress and `next_stage()` honest.
        """
        done = {
            record.stage for record in self.stages if record.status in {"succeeded", "skipped"}
        }
        return done | {JobStage.REGISTERED}

    def next_stage(self) -> JobStage | None:
        for stage in STAGE_ORDER:
            if stage not in self.completed_stages():
                return stage
        return None

    @property
    def progress(self) -> float:
        done = len(self.completed_stages() & set(STAGE_ORDER))
        return round(done / len(STAGE_ORDER), 3)
