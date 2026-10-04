"""Ingestion: format detection, parsing and the safety gate."""

from __future__ import annotations

import pytest
from insightflow_contracts import UnsupportedInputError
from insightflow_ingestion import (
    assert_safe,
    detect_delimiter,
    detect_encoding,
    detect_format,
    read_table,
    slugify,
)
from insightflow_ingestion.readers import ReadOptions
from openpyxl import Workbook


class TestFormatDetection:
    def test_detects_csv(self, sales_csv):
        assert detect_format(sales_csv) == "csv"

    def test_detects_xlsx_regardless_of_extension(self, tmp_path, sales_csv):
        disguised = tmp_path / "not-really.txt"
        disguised.write_bytes(sales_csv.read_bytes())
        assert detect_format(disguised) == "csv"

    def test_detects_xlsx(self, tmp_path):
        workbook = Workbook()
        workbook.active.title = "Data"
        workbook.active.append(["a", "b"])
        path = tmp_path / "book.xlsx"
        workbook.save(path)
        assert detect_format(path) == "xlsx"

    def test_legacy_xls_is_detected(self, tmp_path):
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        assert detect_format(path) == "xls"


class TestEncodingAndDelimiter:
    def test_utf8_is_detected(self, sales_csv):
        assert detect_encoding(sales_csv).is_utf8

    def test_utf8_bom_is_detected(self, tmp_path):
        path = tmp_path / "bom.csv"
        path.write_bytes("﻿a,b\n1,2\n".encode())
        sniff = detect_encoding(path)
        assert sniff.encoding == "utf-8-sig"

    def test_non_utf8_is_reported_honestly(self, tmp_path):
        path = tmp_path / "latin.csv"
        path.write_bytes("name,city\nJosé,Köln\n".encode("latin-1"))
        sniff = detect_encoding(path)
        assert not sniff.is_utf8

    @pytest.mark.parametrize(
        ("delimiter", "name"),
        [(",", "a,b,c"), (";", "a;b;c"), ("\t", "a\tb\tc"), ("|", "a|b|c")],
    )
    def test_delimiter_detection(self, tmp_path, delimiter, name):
        path = tmp_path / "delim.csv"
        path.write_text("\n".join([name] * 5), encoding="utf-8")
        sniff = detect_delimiter(path, encoding="utf-8")
        assert sniff.delimiter == delimiter

    def test_single_column_has_no_delimiter(self, tmp_path):
        path = tmp_path / "one.csv"
        path.write_text("name\nalpha\nbeta\n", encoding="utf-8")
        assert detect_delimiter(path, encoding="utf-8").delimiter is None


class TestReading:
    def test_semicolon_csv_is_normalised(self, tmp_path):
        path = tmp_path / "semi.csv"
        path.write_text("Order ID;Region;Revenue\n1;West;100\n2;East;200\n", encoding="utf-8")
        result = read_table(path)
        assert result.table.columns == ["order_id", "region", "revenue"]
        assert result.table.frame.height == 2

    def test_original_headers_are_retained(self, tmp_path):
        path = tmp_path / "semi.csv"
        path.write_text("Order ID;Region\n1;West\n", encoding="utf-8")
        result = read_table(path)
        assert result.table.original_columns == ["Order ID", "Region"]

    def test_duplicate_headers_are_renamed_and_reported(self, tmp_path):
        path = tmp_path / "dup.csv"
        path.write_text("a,a,b\n1,2,3\n4,5,6\n", encoding="utf-8")
        result = read_table(path)
        assert len(set(result.table.columns)) == 3
        assert any("duplicate header" in note for note in result.notes)

    def test_null_tokens_become_null(self, tmp_path):
        path = tmp_path / "nulls.csv"
        path.write_text("a,b,c\n1,N/A,\n2,3,NULL\n", encoding="utf-8")
        result = read_table(path)
        # Row 1 has an empty cell in `b` and an empty cell in `c`.
        assert result.table.frame["b"].null_count() == 1
        # Row 1 is empty and row 2 is the literal token NULL.
        assert result.table.frame["c"].null_count() == 2

    def test_unicode_headers_are_preserved(self, unicode_csv):
        result = read_table(unicode_csv)
        assert "café" in result.table.columns

    def test_multiple_sheets(self, tmp_path):
        workbook = Workbook()
        first = workbook.active
        first.title = "Sales"
        first.append(["order_id", "revenue"])
        first.append([1, 100])
        second = workbook.create_sheet("Notes")
        second.append(["text"])
        second.append(["hello"])
        path = tmp_path / "multi.xlsx"
        workbook.save(path)

        result = read_table(path, ReadOptions(sheet_name="Notes"))
        assert result.table.columns == ["text"]
        assert result.available_sheets == ["Sales", "Notes"]

    def test_unknown_sheet_is_refused(self, tmp_path):
        workbook = Workbook()
        workbook.active.title = "Sales"
        workbook.active.append(["a"])
        path = tmp_path / "book.xlsx"
        workbook.save(path)
        with pytest.raises(UnsupportedInputError, match="not found"):
            read_table(path, ReadOptions(sheet_name="Nope"))

    def test_empty_sheet_is_refused(self, tmp_path):
        workbook = Workbook()
        workbook.active.title = "Empty"
        path = tmp_path / "empty_sheet.xlsx"
        workbook.save(path)
        with pytest.raises(UnsupportedInputError, match="empty"):
            read_table(path)

    def test_header_only_csv_has_no_rows(self, tmp_path):
        path = tmp_path / "header.csv"
        path.write_text("a,b,c\n", encoding="utf-8")
        result = read_table(path)
        assert result.table.frame.height == 0

    def test_ragged_rows_are_truncated_not_dropped(self, tmp_path):
        path = tmp_path / "ragged.csv"
        path.write_text("a,b,c\n1,2,3\n4,5\n", encoding="utf-8")
        result = read_table(path)
        assert result.table.frame.height == 2


class TestSafetyGate:
    def test_empty_file_is_refused(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_bytes(b"")
        with pytest.raises(UnsupportedInputError, match="empty"):
            assert_safe(path, max_bytes=10**6, allowed_extensions=(".csv",))

    def test_oversized_file_is_refused(self, sales_csv):
        with pytest.raises(UnsupportedInputError, match="size limit"):
            assert_safe(sales_csv, max_bytes=10, allowed_extensions=(".csv",))

    def test_disallowed_extension_is_refused(self, sales_csv):
        with pytest.raises(UnsupportedInputError, match="unsupported file extension"):
            assert_safe(sales_csv, max_bytes=10**6, allowed_extensions=(".xlsx",))

    def test_legacy_xls_is_refused(self, tmp_path):
        path = tmp_path / "old.xls"
        path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
        with pytest.raises(UnsupportedInputError, match="legacy"):
            assert_safe(path, max_bytes=10**6, allowed_extensions=(".xls", ".csv"))

    def test_macro_workbook_is_refused(self, tmp_path):
        workbook = Workbook()
        workbook.active.title = "Data"
        workbook.active.append(["a"])
        path = tmp_path / "macro.xlsx"
        workbook.save(path)
        # Simulate a macro-enabled workbook by injecting the VBA part.
        import zipfile

        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("xl/vbaProject.bin", b"\x00" * 32)
        with pytest.raises(UnsupportedInputError, match="macro"):
            assert_safe(path, max_bytes=10**6, allowed_extensions=(".xlsx",))

    def test_encrypted_marker_is_refused(self, tmp_path):
        path = tmp_path / "encrypted.xlsx"
        path.write_bytes(b"PK\x03\x04" + b"EncryptedPackage" + b"\x00" * 32)
        with pytest.raises(UnsupportedInputError, match="password"):
            assert_safe(path, max_bytes=10**6, allowed_extensions=(".xlsx",))


class TestColumnNames:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Order ID", "order_id"),
            ("order-id", "order_id"),
            ("  Padded  ", "padded"),
            ("orderID", "order_id"),
            ("Total$Amount", "total_amount"),
            ("", "column"),
            ("Café", "café"),
        ],
    )
    def test_slugify(self, raw, expected):
        assert slugify(raw) == expected

    def test_slugify_is_idempotent(self):
        once = slugify("Order ID")
        assert slugify(once) == once