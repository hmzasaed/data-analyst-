"""Shared fixtures.

Fixtures are deterministic: fixed seeds, fixed timestamps, temporary directories
and per-test settings, so a failure is always reproducible.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import polars as pl
import pytest
from insightflow_contracts import DatasetSnapshot, get_settings, reset_settings


@pytest.fixture(scope="session", autouse=True)
def _isolated_environment() -> Iterator[None]:
    """Point every path at a temp directory and keep the LLM off for tests."""
    root = Path(tempfile.mkdtemp(prefix="insightflow-tests-"))
    os.environ.setdefault("INSIGHTFLOW_ARTIFACT_ROOT", str(root / "artifacts"))
    os.environ.setdefault(
        "INSIGHTFLOW_STATE_DATABASE_URL", f"sqlite:///{(root / 'state.db').as_posix()}"
    )
    os.environ["INSIGHTFLOW_LLM_ENABLED"] = "false"
    os.environ["INSIGHTFLOW_ENVIRONMENT"] = "test"
    reset_settings()
    yield
    reset_settings()


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def state_store(tmp_path: Path):
    from insightflow_storage import StateStore

    return StateStore(f"sqlite:///{(tmp_path / 'state.db').as_posix()}")


@pytest.fixture
def artifact_store(tmp_path: Path):
    from insightflow_storage import ArtifactStore

    return ArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def sales_csv(tmp_path: Path) -> Path:
    """240 rows, 12 months, four regions, one planted outlier, mixed formatting."""
    path = tmp_path / "sales.csv"
    lines = ["order_id,order_date,region,channel,revenue,units,customer_email"]
    for index in range(240):
        month = (index % 12) + 1
        day = (index % 28) + 1
        region = ["North", "South", "East", "West"][index % 4]
        channel = ["web", "retail"][index % 2]
        revenue = 100 + (index % 9) * 23.5
        if index == 17:
            revenue = 12000.0
        lines.append(
            f"{index + 1},2024-{month:02d}-{day:02d},{region},{channel},"
            f"{revenue},{1 + index % 6},user{index}@example.com"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def messy_csv(tmp_path: Path) -> Path:
    """Deliberately awkward: duplicates, blanks, mixed dates, inconsistent labels."""
    path = tmp_path / "messy.csv"
    path.write_text(
        "Order ID;Order Date;Region ;Revenue;Notes\n"
        "1;2024-01-05;West;1,200.50;ok\n"
        "2;2024-01-06;west;$980; padded \n"
        "3;06/02/2024; WEST ;n/a;\n"
        "4;2024-02-10;East;1200.50;ok\n"
        "5;2024-02-11;east;1450;ok\n"
        "6;03/01/2024;East;990.25;ok\n"
        "7;2024-03-05;North;(300);ok\n"
        "8;2024-03-06;north;;ok\n"
        "9;2024-03-07;North;1120;ok\n"
        "10;2024-03-08;north;1180;ok\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def unicode_csv(tmp_path: Path) -> Path:
    """Semicolon-delimited so the accent-bearing header is a separate column."""
    path = tmp_path / "unicode.csv"
    path.write_text(
        "café;prix;date\nZoé;10,50;2024-01-01\nÉmile;20,00;2024-01-02\nÁvila;5;2024-01-03\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sales_frame() -> pl.DataFrame:
    """A typed frame matching the shape the analysis tools expect."""
    import numpy as np

    rng = np.random.default_rng(20240101)
    rows = 240
    return pl.DataFrame(
        {
            "order_id": [str(index) for index in range(1, rows + 1)],
            "order_date": [f"2024-{(index % 12) + 1:02d}-{(index % 28) + 1:02d}" for index in range(rows)],
            "region": [["North", "South", "East", "West"][index % 4] for index in range(rows)],
            "channel": [["web", "retail"][index % 2] for index in range(rows)],
            "revenue": (rng.normal(400, 90, rows)).tolist(),
            "units": rng.normal(10, 3, rows).tolist(),
        }
    ).with_columns(pl.col("order_date").str.to_datetime(format="%Y-%m-%d"))


@pytest.fixture
def sales_snapshot(sales_frame: pl.DataFrame) -> DatasetSnapshot:
    return DatasetSnapshot(
        dataset_id="ds_test",
        artifact_id="art_test_v1",
        version=1,
        row_count=sales_frame.height,
        column_count=sales_frame.width,
        columns=sales_frame.columns,
        source_file_hash="fixture-hash",
        sheet_name=None,
    )


@pytest.fixture
def sales_profile(sales_frame: pl.DataFrame, sales_snapshot: DatasetSnapshot):
    from insightflow_profiling import profile_dataset

    return profile_dataset(sales_frame, sales_snapshot)