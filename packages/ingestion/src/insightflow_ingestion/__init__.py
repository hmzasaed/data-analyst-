"""Safe ingestion of CSV and XLSX into one normalised table representation.

Threat model summary (see docs/threat-model.md for the full version):

* spreadsheets are opened read-only with formula *results* never evaluated, so a
  malicious `=HYPERLINK(...)` or `=cmd|'...'` cell is inert;
* macro-enabled workbooks, password-protected workbooks and zip bombs are rejected
  before parsing;
* files are hashed at registration so the stored original can be verified later;
* nothing here executes code found in the data.
"""

from insightflow_ingestion.normalize import (
    NULL_TOKENS,
    ColumnNameCollision,
    NormalizedTable,
    clean_number_literal,
    is_null_token,
    normalise_column_names,
    normalise_whitespace,
    slugify,
)
from insightflow_ingestion.readers import (
    ReadOptions,
    ReadResult,
    list_sheets,
    preview_rows,
    read_table,
)
from insightflow_ingestion.safety import SafetyReport, assert_safe
from insightflow_ingestion.service import IngestionOutcome, IngestionService
from insightflow_ingestion.sniffing import (
    DelimiterSniff,
    EncodingSniff,
    detect_delimiter,
    detect_encoding,
    detect_format,
)
from insightflow_ingestion.writer import write_json, write_parquet, write_table_csv

__all__ = [
    "NULL_TOKENS",
    "ColumnNameCollision",
    "DelimiterSniff",
    "EncodingSniff",
    "IngestionOutcome",
    "IngestionService",
    "NormalizedTable",
    "ReadOptions",
    "ReadResult",
    "SafetyReport",
    "assert_safe",
    "clean_number_literal",
    "detect_delimiter",
    "detect_encoding",
    "detect_format",
    "is_null_token",
    "list_sheets",
    "normalise_column_names",
    "normalise_whitespace",
    "preview_rows",
    "read_table",
    "slugify",
    "write_json",
    "write_parquet",
    "write_table_csv",
]
