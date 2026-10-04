"""Pre-parse safety gate.

Runs before any parser touches the file. Rejects the inputs that a naive reader
would happily execute, decompress or exhaust memory on.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from insightflow_contracts import UnsupportedInputError

from insightflow_ingestion.readers import list_sheets
from insightflow_ingestion.sniffing import detect_format

#: Refuse archives whose expansion ratio looks like a zip bomb.
MAX_EXPANSION_RATIO = 200
MAX_ZIP_ENTRIES = 20_000

_MACRO_MARKERS = (
    "vbaProject.bin",
    "xl/vbaproject.bin",
    "word/vbaproject.bin",
)

_ENCRYPTED_MARKERS = (
    b"EncryptedPackage",
    b"EncryptionInfo",
)


@dataclass
class SafetyReport:
    """Outcome of the safety gate, retained in the audit trail."""

    ok: bool
    detected_format: str
    reasons: list[str] = field(default_factory=list)
    is_encrypted: bool = False
    has_macros: bool = False
    sheet_names: list[str] = field(default_factory=list)
    zip_entries: int = 0
    uncompressed_bytes: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "detected_format": self.detected_format,
            "reasons": self.reasons,
            "is_encrypted": self.is_encrypted,
            "has_macros": self.has_macros,
            "sheet_names": self.sheet_names,
            "zip_entries": self.zip_entries,
        }


def _inspect_zip(path: Path) -> tuple[int, int, list[str]]:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        return len(infos), sum(info.file_size for info in infos), names


def assert_safe(
    path: str | Path,
    *,
    max_bytes: int,
    allowed_extensions: tuple[str, ...],
) -> SafetyReport:
    """Validate a file before parsing, raising `UnsupportedInputError` on refusal.

    Refusals are always explicit: the product promise is a clear failure for
    unsupported input rather than a fabricated result.
    """
    file_path = Path(path)
    reasons: list[str] = []

    if not file_path.exists():
        raise UnsupportedInputError("file does not exist", path=str(file_path))
    if not file_path.is_file():
        raise UnsupportedInputError("path is not a regular file", path=str(file_path))

    size = file_path.stat().st_size
    if size == 0:
        raise UnsupportedInputError(
            "file is empty",
            path=str(file_path),
            suggestions=["Provide a file with a header row and at least one data row."],
        )
    if size > max_bytes:
        raise UnsupportedInputError(
            "file exceeds the configured size limit",
            path=str(file_path),
            byte_size=size,
            max_bytes=max_bytes,
            suggestions=["Split the file, or aggregate it before uploading."],
        )

    suffix = file_path.suffix.lower()
    if allowed_extensions and suffix not in allowed_extensions:
        raise UnsupportedInputError(
            f"unsupported file extension '{suffix or '(none)'}'",
            allowed=list(allowed_extensions),
            suggestions=[f"Convert the file to {allowed_extensions[0]} and try again."],
        )

    detected = detect_format(file_path)
    if detected == "xls":
        raise UnsupportedInputError(
            "legacy .xls workbooks are not supported",
            path=str(file_path),
            suggestions=["Re-save the workbook as .xlsx."],
        )
    if detected == "zip":
        raise UnsupportedInputError(
            "archive files are not supported; provide a spreadsheet or delimited text file",
            path=str(file_path),
            suggestions=[
                "Extract the archive and upload the .csv or .xlsx file inside it.",
                "Combining several files is out of scope for this release.",
            ],
        )

    report = SafetyReport(ok=True, detected_format=detected)
    header = file_path.read_bytes()[:4096]
    report.is_encrypted = any(marker in header for marker in _ENCRYPTED_MARKERS)
    if report.is_encrypted:
        raise UnsupportedInputError(
            "password-protected file detected; InsightFlow cannot open it",
            path=str(file_path),
            suggestions=["Remove the password protection and re-upload."],
        )

    if detected == "xlsx":
        entries, uncompressed, names = _inspect_zip(file_path)
        report.zip_entries = entries
        report.uncompressed_bytes = uncompressed
        report.sheet_names = list_sheets(file_path)

        if any(name.lower().endswith(_MACRO_MARKERS) for name in names):
            report.has_macros = True
            raise UnsupportedInputError(
                "macro-enabled workbook detected; macros are never executed",
                path=str(file_path),
                suggestions=["Re-save as a macro-free .xlsx file."],
            )
        if entries > MAX_ZIP_ENTRIES:
            raise UnsupportedInputError(
                "workbook contains too many archive entries",
                entries=entries,
                limit=MAX_ZIP_ENTRIES,
            )
        if uncompressed / max(size, 1) > MAX_EXPANSION_RATIO:
            raise UnsupportedInputError(
                "workbook expansion ratio suggests a decompression bomb",
                expansion_ratio=round(uncompressed / max(size, 1), 2),
                limit=MAX_EXPANSION_RATIO,
            )
        if not report.sheet_names:
            raise UnsupportedInputError(
                "workbook has no readable worksheets", path=str(file_path)
            )

    if not reasons:
        report.ok = True
    return report
