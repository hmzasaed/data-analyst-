-- Schema for InsightFlow job state.
--
-- The runtime store creates this schema itself from `insightflow_storage.state`, so
-- this file exists as the reviewable, migration-managed definition and as the
-- starting point for a PostgreSQL deployment. Keep the two in step.
--
-- Jobs are stored with their full typed payload as JSON and indexed on the columns
-- the API actually filters by. Audit rows are append-only.

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
    status           TEXT NOT NULL CHECK (status IN ('succeeded', 'failed', 'skipped', 'running')),
    attempt          INTEGER NOT NULL DEFAULT 1,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    duration_seconds DOUBLE PRECISION,
    error            TEXT,
    error_code       TEXT,
    detail           JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_stages_job ON job_stages (job_id, id);

-- Append-only. Retention is enforced by deleting whole jobs, never individual rows,
-- so an audit trail is always complete for the period it covers.
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

CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);

-- Retention sweep. Run from cron; `purge_expired_artifact_jobs` is the application
-- side of the same policy for the artifact store.
-- DELETE FROM jobs
--  WHERE updated_at < now() - INTERVAL '7 days'
--    AND status IN ('completed', 'failed', 'rejected', 'cancelled');