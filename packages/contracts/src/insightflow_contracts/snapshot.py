"""Raw-file registration record."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from insightflow_contracts.job import utc_now
from insightflow_contracts.profile import StrictModel


class RawFileRecord(StrictModel):
    """An uploaded file, after validation but before any parsing.

    `sha256` is the dataset identity: re-uploading identical bytes produces the
    same hash, which lets the platform reuse cached profiles and detect tampering
    with the stored original.
    """

    artifact_id: str
    owner_id: str
    filename: str
    declared_media_type: str | None = None
    detected_format: str = "unknown"
    byte_size: int = Field(ge=0)
    sha256: str
    sha256_verified: bool = False
    uploaded_at: datetime = Field(default_factory=utc_now)
    retention_until: datetime | None = None
    encrypted: bool = False
    quarantined: bool = False
    scan_status: str = "pending"
    notes: list[str] = Field(default_factory=list)
