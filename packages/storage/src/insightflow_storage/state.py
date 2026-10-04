"""Job state persistence and the audit log.

Two backends behind one class, chosen by `database_url`:

* `sqlite:///path.db` (default) - stdlib `sqlite3`, zero setup, for local work and
  tests.
* `postgresql://...` - PostgreSQL through `psycopg`, which is how the platform runs
  on **Supabase**. Use the Supabase connection string (Project settings -> Database);
  the transaction pooler on port 6543 works because server-side prepared
  statements are disabled.

Both backends run the same SQL. Only the schema DDL, the parameter placeholder and
the decoding of JSON/timestamp columns differ, so the SQLite test suite exercises
every query the PostgreSQL path runs.

The audit log is append-only and JSON-per-line. It records what the agent *did*,
not only what it concluded, which is what makes a run reviewable.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from insightflow_contracts import Job, JobStatus, StageRecord, utc_now

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_stage   TEXT NOT NULL,
    dataset_id      TEXT,
    dataset_version INTEGER NOT NULL DEFAULT 0,
    retention_days  INTEGER NOT NULL DEFAULT 7,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    payload         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS job_stages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           TEXT NOT NULL,
    stage            TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempt          INTEGER NOT NULL DEFAULT 1,
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_seconds REAL,
    error            TEXT,
    error_code       TEXT,
    detail           TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_stages_job ON job_stages(job_id, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id   TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    at       TEXT NOT NULL,
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    stage    TEXT,
    outcome  TEXT NOT NULL DEFAULT 'ok',
    subject  TEXT,
    detail   TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_log(job_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_owner ON audit_log(owner_id, at DESC);
"""

#: PostgreSQL / Supabase schema. Mirrors `infra/supabase/migrations`; keep in step.
_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    owner_id        TEXT NOT NULL,
    status          TEXT NOT NULL,
    current_stage   TEXT NOT NULL,
    dataset_id      TEXT,
    dataset_version INTEGER NOT NULL DEFAULT 0,
    retention_days  INTEGER NOT NULL DEFAULT 7,
    created_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL,
    payload         JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_owner ON jobs (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status, created_at);

CREATE TABLE IF NOT EXISTS job_stages (
    id               BIGSERIAL PRIMARY KEY,
    job_id           TEXT NOT NULL REFERENCES jobs (job_id) ON DELETE CASCADE,
    stage            TEXT NOT NULL,
    status           TEXT NOT NULL,
    attempt          INTEGER NOT NULL DEFAULT 1,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION,
    error            TEXT,
    error_code       TEXT,
    detail           JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_stages_job ON job_stages (job_id, id);

CREATE TABLE IF NOT EXISTS audit_log (
    id       BIGSERIAL PRIMARY KEY,
    job_id   TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor    TEXT NOT NULL,
    action   TEXT NOT NULL,
    stage    TEXT,
    outcome  TEXT NOT NULL DEFAULT 'ok',
    subject  TEXT,
    detail   JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_log (job_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_owner ON audit_log (owner_id, at DESC);

-- The service connects as a privileged role; RLS with no policies keeps the
-- Supabase anon/authenticated REST roles from reading tenant data directly.
ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE job_stages ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_meta ENABLE ROW LEVEL SECURITY;
"""


@dataclass
class AuditEvent:
    """One append-only audit row. `detail` must never contain dataset cell values."""

    actor: str
    action: str
    outcome: str = "ok"
    stage: str | None = None
    subject: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class _PgConnection:
    """Adapts a psycopg connection to the sqlite3-style calls this module makes."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    def execute(self, sql: str, parameters: tuple = ()) -> Any:
        if not parameters:
            return self._connection.execute(sql)
        return self._connection.execute(sql.replace("?", "%s"), parameters)


class StateStore:
    """Job, stage and audit persistence on SQLite or PostgreSQL (Supabase)."""

    def __init__(self, database_url: str = "sqlite:///./insightflow.db") -> None:
        self.database_url = database_url
        self.backend = "postgres" if _is_postgres(database_url) else "sqlite"
        self._lock = threading.Lock()
        self._pg: Any = None
        self._path = ""
        if self.backend == "sqlite":
            self._path = _sqlite_path(database_url)
            if self._path != ":memory:":
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            if self.backend == "sqlite":
                connection.executescript(_SCHEMA)
            else:
                connection.execute(_PG_SCHEMA)
            # A literal, not a parameter: PostgreSQL cannot type a bare `SELECT $1`.
            connection.execute(
                f"INSERT INTO schema_meta (version) SELECT {int(SCHEMA_VERSION)} "  # noqa: S608
                "WHERE NOT EXISTS (SELECT 1 FROM schema_meta)"
            )

    @contextmanager
    def _connect(self) -> Iterator[Any]:
        if self.backend == "postgres":
            with self._connect_postgres() as connection:
                yield connection
            return
        connection = sqlite3.connect(self._path, isolation_level=None, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _connect_postgres(self) -> Iterator[_PgConnection]:
        """One long-lived autocommit connection, reopened if it drops.

        A remote database makes a connection per call expensive, and the lock
        keeps the API's background threads from interleaving on it.
        """
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise RuntimeError(
                "a PostgreSQL database_url needs psycopg: uv pip install 'psycopg[binary]'"
            ) from exc

        with self._lock:
            if self._pg is None or self._pg.closed:
                # prepare_threshold=None: Supabase's transaction pooler (pgbouncer)
                # cannot hold server-side prepared statements across transactions.
                self._pg = psycopg.connect(
                    _postgres_dsn(self.database_url), autocommit=True, row_factory=dict_row,
                    prepare_threshold=None, connect_timeout=15,
                )
            try:
                yield _PgConnection(self._pg)
            except psycopg.OperationalError:
                self._pg.close()
                self._pg = None
                raise

    # ------------------------------------------------------------------- jobs
    def save_job(self, job: Job) -> Job:
        job.updated_at = utc_now()
        payload = json.dumps(job.model_dump(mode="json"), default=str)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_id, owner_id, status, current_stage, dataset_id,
                                  dataset_version, retention_days, created_at, updated_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    status = excluded.status,
                    current_stage = excluded.current_stage,
                    dataset_id = excluded.dataset_id,
                    dataset_version = excluded.dataset_version,
                    retention_days = excluded.retention_days,
                    updated_at = excluded.updated_at,
                    payload = excluded.payload
                """,
                (
                    job.job_id,
                    job.owner_id,
                    job.status.value,
                    job.current_stage.value,
                    job.dataset_id,
                    job.dataset_version,
                    job.retention_days,
                    _iso(job.created_at),
                    _iso(job.updated_at),
                    payload,
                ),
            )
        return job

    def load_job(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return Job.model_validate(_json(row["payload"])) if row else None

    def list_jobs(self, owner_id: str, *, limit: int = 50) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM jobs WHERE owner_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (owner_id, limit),
            ).fetchall()
        return [Job.model_validate(_json(row["payload"])) for row in rows]

    def jobs_pending(self, *, limit: int = 100) -> list[Job]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM jobs WHERE status IN (?, ?) "
                "ORDER BY created_at LIMIT ?",
                (JobStatus.PENDING.value, JobStatus.RUNNING.value, limit),
            ).fetchall()
        return [Job.model_validate(_json(row["payload"])) for row in rows]

    def claim_job(self, job_id: str) -> Job | None:
        """Move one job from pending to running, atomically.

        The conditional UPDATE is the lock: when two workers race, the database
        lets exactly one of them match `status = 'pending'`.
        """
        job = self.load_job(job_id)
        if job is None or job.status is not JobStatus.PENDING:
            return None
        job.status = JobStatus.RUNNING
        job.updated_at = utc_now()
        payload = json.dumps(job.model_dump(mode="json"), default=str)
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ?, payload = ? "
                "WHERE job_id = ? AND status = ?",
                (job.status.value, _iso(job.updated_at), payload, job_id,
                 JobStatus.PENDING.value),
            )
            claimed = cursor.rowcount == 1
        return job if claimed else None

    # ----------------------------------------------------------------- stages
    def append_stage(self, job_id: str, record: StageRecord) -> StageRecord:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO job_stages (job_id, stage, status, attempt, started_at,
                                        finished_at, duration_seconds, error, error_code, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    record.stage.value,
                    record.status,
                    record.attempt,
                    _iso(record.started_at),
                    _iso(record.finished_at),
                    record.duration_seconds,
                    record.error,
                    record.error_code,
                    json.dumps(record.detail, default=str),
                ),
            )
        return record

    def stage_history(self, job_id: str) -> list[StageRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_stages WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        return [
            StageRecord(
                stage=row["stage"],
                status=row["status"],
                attempt=row["attempt"],
                started_at=_parse(row["started_at"]),
                finished_at=_parse(row["finished_at"]),
                duration_seconds=row["duration_seconds"],
                error=row["error"],
                error_code=row["error_code"],
                detail=_json(row["detail"]),
            )
            for row in rows
        ]

    # ------------------------------------------------------------------ audit
    def audit(self, job_id: str, owner_id: str, event: AuditEvent) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_log (job_id, owner_id, at, actor, action, stage, outcome, subject, detail)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    owner_id,
                    _iso(utc_now()),
                    event.actor,
                    event.action,
                    event.stage,
                    event.outcome,
                    event.subject,
                    json.dumps(event.detail, default=str),
                ),
            )

    def audit_trail(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_log WHERE job_id = ? ORDER BY id", (job_id,)
            ).fetchall()
        return [
            {
                "at": _iso(row["at"]) if isinstance(row["at"], datetime) else row["at"],
                "actor": row["actor"],
                "action": row["action"],
                "stage": row["stage"],
                "outcome": row["outcome"],
                "subject": row["subject"],
                "detail": _json(row["detail"]),
            }
            for row in rows
        ]

    def export_audit_trail(self, job_id: str, destination: str | Path) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        events = self.audit_trail(job_id)
        path.write_text(
            "\n".join(json.dumps(event, default=str) for event in events) + ("\n" if events else ""),
            encoding="utf-8",
        )
        return path

    def delete_job(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM audit_log WHERE job_id = ?", (job_id,))
            connection.execute("DELETE FROM job_stages WHERE job_id = ?", (job_id,))
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (job_id,))


def _is_postgres(database_url: str) -> bool:
    return database_url.startswith(("postgres://", "postgresql://", "postgresql+psycopg://"))


def _postgres_dsn(database_url: str) -> str:
    """Accept the SQLAlchemy-style `postgresql+psycopg://` spelling as well."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _json(value: Any) -> Any:
    """Decode a JSON column: text on SQLite, already decoded from JSONB on PostgreSQL."""
    if value is None:
        return {}
    if isinstance(value, str | bytes):
        return json.loads(value or "{}")
    return value


def _sqlite_path(database_url: str) -> str:
    """Turn a SQLAlchemy-style URL into a filesystem path."""
    if database_url in {":memory:", "sqlite://", "sqlite:///:memory:"}:
        return ":memory:"
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        remainder = database_url[len(prefix) :]
        # sqlite:///relative.db and sqlite:////absolute.db are both common forms.
        return remainder.lstrip("/") if not database_url.startswith("sqlite:////") else f"/{remainder.lstrip('/')}"
    if "://" not in database_url:
        return database_url
    raise ValueError(
        f"unsupported database_url '{database_url}'; use a sqlite path or a PostgreSQL URL"
    )


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _parse(value: str | datetime | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value) if value else None


def default_state_store(database_url: str | None = None) -> StateStore:
    from insightflow_contracts import get_settings

    return StateStore(database_url or get_settings().state_database_url)