"""Persisting and reloading stage outputs.

Read endpoints must never re-run an analysis: doing so would be slow, would cost
money, and could produce different numbers than the ones a user already read. So
each stage writes its typed output as a JSON artifact, and readers load it back.

This also means a completed job survives a restart of the API process, which is the
whole point of storing the work rather than recomputing it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from insightflow_contracts import (
    ArtifactKind,
    CleaningExecution,
    CleaningPlan,
    DatasetProfile,
    Finding,
    Job,
    ReportDocument,
    VerificationResult,
)
from insightflow_storage import ArtifactStore

#: Logical name -> the contract it deserialises into.
_PROFILE = "profile"
_CLEANING_PLAN = "cleaning_plan"
_CLEANING_EXECUTION = "cleaning_execution"
_PLAN = "analysis_plan"
_FINDINGS = "findings"
_VERIFICATION = "verification"
_REPORT = "report_document"
_ANOMALIES = "anomaly_results"
_RESULTS = "execution_results"
_WARNINGS = "warnings"


def _artifact_id(name: str, job_id: str) -> str:
    return f"{name}_{job_id}"[:64]


def _serialise(value: Any) -> str:
    """Serialise a model, or a list of models, to JSON text."""
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple):
        return json.dumps(
            [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value],
            indent=2,
            default=str,
        )
    return value.model_dump_json(indent=2)


def save_stage_outputs(
    store: ArtifactStore, owner_id: str, job_id: str, payload: dict[str, Any]
) -> None:
    """Write the typed outputs a stage produced."""
    for name, value in payload.items():
        if value is None:
            continue
        store.write_text(
            owner_id,
            job_id,
            ArtifactKind.REPORT_JSON,
            _artifact_id(name, job_id),
            _serialise(value),
        )


def load_json(
    store: ArtifactStore, owner_id: str, job_id: str, name: str
) -> Any | None:
    """Load one stored stage output, or None when it has not been produced."""
    path = store.path_for(
        owner_id, job_id, ArtifactKind.REPORT_JSON, _artifact_id(name, job_id)
    )
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class JobSnapshot:
    """Everything a reader needs about a job, loaded from stored artifacts."""

    job: Job
    profile: DatasetProfile | None = None
    cleaning_plan: CleaningPlan | None = None
    cleaning: CleaningExecution | None = None
    findings: list[Finding] = field(default_factory=list)
    verification: VerificationResult | None = None
    document: ReportDocument | None = None
    anomalies: list[dict] = field(default_factory=list)
    results: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def has_report(self) -> bool:
        return self.document is not None


def load_snapshot(store: ArtifactStore, job: Job) -> JobSnapshot:
    """Rebuild a read-only view of a job from its stored artifacts."""
    owner, job_id = job.owner_id, job.job_id

    def _load(name: str) -> Any | None:
        return load_json(store, owner, job_id, name)

    def _model(name: str, contract: type):
        payload = _load(name)
        return contract.model_validate(payload) if payload else None

    findings_payload = _load(_FINDINGS) or []
    return JobSnapshot(
        job=job,
        profile=_model(_PROFILE, DatasetProfile),
        cleaning_plan=_model(_CLEANING_PLAN, CleaningPlan),
        cleaning=_model(_CLEANING_EXECUTION, CleaningExecution),
        findings=[Finding.model_validate(item) for item in findings_payload],
        verification=_model(_VERIFICATION, VerificationResult),
        document=_model(_REPORT, ReportDocument),
        anomalies=_load(_ANOMALIES) or [],
        results=_load(_RESULTS) or [],
        warnings=_load(_WARNINGS) or [],
    )