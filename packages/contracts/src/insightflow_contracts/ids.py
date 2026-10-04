"""Deterministic identifier helpers.

All identifiers are content-derived (`sha256` over stable, sorted JSON) rather
than random. Re-running the same stage on the same input therefore produces the
same id, which makes jobs idempotent, caches safe to reuse and audit diffs
reviewable.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_CHUNK = 1024 * 1024


def canonical_json(payload: Any) -> str:
    """Serialise `payload` to a stable string: sorted keys, no insignificant space."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def content_digest(payload: Any, *, length: int = 32) -> str:
    """Return a stable hex digest of any JSON-serialisable payload."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:length]


def derive_id(prefix: str, payload: Any, *, length: int = 16) -> str:
    """Return `<prefix>_<digest>` for the given payload."""
    return f"{prefix}_{content_digest(payload, length=length)}"


def file_sha256(path: str | Path, *, chunk_size: int = _CHUNK) -> str:
    """Stream a file through sha256 without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def stable_row_keys(rows: list[dict[str, Any]], *, columns: list[str]) -> list[str]:
    """Deterministic per-row identifiers usable as evidence references.

    Rows are keyed by their position in the snapshot plus a digest of the
    projected values, so a key survives re-ordering and points back at a concrete
    record without leaking the record contents into logs.
    """
    keys: list[str] = []
    for position, row in enumerate(rows):
        projection = [row.get(column) for column in columns]
        keys.append(f"row:{position}:{content_digest(projection, length=10)}")
    return keys
