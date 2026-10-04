"""Format, encoding and delimiter detection.

Detection is deterministic and evidence-returning: every guess carries the
confidence and the candidates considered so the profile can show *why* the reader
chose what it chose.
"""

from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import charset_normalizer

#: Bytes that reliably identify OOXML / legacy Excel containers.
_ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

#: Sniff only the head of the file: enough signal, bounded memory and time.
SNIFF_BYTES = 256 * 1024

_CANDIDATE_DELIMITERS = (",", ";", "\t", "|", ":")

_PREFERRED_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


@dataclass(frozen=True)
class DelimiterSniff:
    delimiter: str | None
    confidence: float
    candidates: dict[str, int] = field(default_factory=dict)
    rationale: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "delimiter": self.delimiter,
            "confidence": round(self.confidence, 4),
            "candidates": self.candidates,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class EncodingSniff:
    encoding: str
    confidence: float
    is_utf8: bool
    rationale: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "encoding": self.encoding,
            "confidence": round(self.confidence, 4),
            "is_utf8": self.is_utf8,
            "rationale": self.rationale,
        }


def detect_format(path: str | Path) -> str:
    """Return `xlsx`, `xls`, `csv`, or `unknown` from content, not extension.

    Extension lies are common; content does not. Unknown formats raise
    `UnsupportedInputError` later rather than being parsed on a guess.
    """
    file_path = Path(path)
    with file_path.open("rb") as handle:
        head = handle.read(8)

    if head.startswith(_OLE_MAGIC):
        return "xls"
    if head[:4] in _ZIP_MAGICS and zipfile.is_zipfile(file_path):
        names = set(zipfile.ZipFile(file_path).namelist())
        if any(name.startswith("xl/") for name in names):
            return "xlsx"
        return "zip"
    return "csv" if file_path.suffix.lower() != ".zip" else "zip"


def _decoded_head(raw: bytes) -> tuple[str, str, float, str]:
    """Decode the head bytes, returning (text, encoding, confidence, rationale)."""
    match = charset_normalizer.from_bytes(raw).best()
    if match is None:
        return (
            raw.decode("utf-8", errors="replace"),
            "utf-8",
            0.0,
            "charset-normalizer found no match; decoded as utf-8 with replacement",
        )
    encoding = (match.encoding or "utf-8").lower()
    # charset-normalizer reports e.g. cp1250 for windows-1252 text; keep its answer.
    return str(match), encoding, float(match.percent_coherence or 0.0) / 100.0, "charset-normalizer"


def detect_encoding(path: str | Path) -> EncodingSniff:
    """Detect the text encoding of a delimited text file."""
    file_path = Path(path)
    raw = file_path.read_bytes()[:SNIFF_BYTES]
    _, encoding, confidence, rationale = _decoded_head(raw)

    if raw.startswith(b"\xef\xbb\xbf"):
        return EncodingSniff("utf-8-sig", 1.0, True, "UTF-8 byte order mark present")

    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Byte-level truth beats the detector's guess when they disagree.
        return EncodingSniff(
            encoding,
            confidence,
            False,
            f"not valid utf-8 ({exc.reason} at byte {exc.start}); using {encoding}",
        )
    return EncodingSniff("utf-8", max(confidence, 0.9), True, f"{rationale}; utf-8 decode succeeded")


def _candidate_counts(text: str, delimiter: str) -> int:
    """Count delimiter occurrences inside quoted fields only at line granularity."""
    total = 0
    try:
        dialect = csv.reader(io.StringIO(text), delimiter=delimiter)
        for index, row in enumerate(dialect):
            if index >= 20:
                break
            if row:
                total += max(len(row) - 1, 0)
    except csv.Error:
        return 0
    return total


def detect_delimiter(path: str | Path, *, encoding: str | None = None) -> DelimiterSniff:
    """Detect the delimiter using consistent multi-column rows across the head."""
    file_path = Path(path)
    raw = file_path.read_bytes()[:SNIFF_BYTES]
    text, detected_encoding, _, _ = _decoded_head(raw)
    encoding = encoding or detected_encoding

    if raw.startswith(b"\xef\xbb\xbf"):
        text = text.lstrip("\ufeff")

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return DelimiterSniff(None, 0.0, {}, "no non-empty lines in head")

    counts = {candidate: _candidate_counts(text, candidate) for candidate in _CANDIDATE_DELIMITERS}

    scored: dict[str, float] = {}
    for candidate, total in counts.items():
        if total == 0:
            continue
        per_line = [line.count(candidate) for line in lines[:20]]
        consistent = sum(1 for value in per_line if value == per_line[0])
        consistency = consistent / len(per_line)
        scored[candidate] = total * consistency

    if not scored:
        # Single-column file: let polars treat it as one column.
        return DelimiterSniff(None, 0.5, counts, "no delimiter produced multiple fields")

    best = max(scored, key=lambda key: scored[key])
    return DelimiterSniff(
        best,
        min(1.0, scored[best] / max(1.0, sum(scored.values()))),
        counts,
        f"'{best!r}' produced the most consistent multi-field rows",
    )


def read_text_head(path: str | Path, *, encoding: str, max_bytes: int = SNIFF_BYTES) -> str:
    file_path = Path(path)
    raw = file_path.read_bytes()[:max_bytes]
    return raw.decode(encoding, errors="replace")
