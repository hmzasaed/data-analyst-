-- InsightFlow job state on Supabase (PostgreSQL).
--
-- The API and worker create this schema on startup when INSIGHTFLOW_STATE_DATABASE_URL
-- points at Postgres, so applying this file is optional. It exists so the schema is
-- reviewable and can be applied with `supabase db push` or the SQL editor.
-- Mirrors _PG_SCHEMA in packages/storage/src/insightflow_storage/state.py.

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
