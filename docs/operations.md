# Operations

## Local development

See **Running the project** in the [README](../README.md#running-the-project) for
the full step-by-step guide. In short:

```bash
uv sync
uv run python -m insightflow_api            # API on :8000, jobs run in the background
cd apps/web && npm install && npm run dev   # UI on :5173
```

The API module is `insightflow_api.service`, not `insightflow_api.app` — the
latter name shadows the `app` package that FastAPI tooling expects. Running
`uvicorn insightflow_api.service:app` directly works too, but runs each job
inside the upload request (`inline` mode).

## Worker mode

For an asynchronous flow, run the API and the worker as separate processes:

```bash
INSIGHTFLOW_EXECUTION_MODE=worker uv run python -m insightflow_api
uv run python -m insightflow_worker
```

Jobs are created as `pending`. The worker claims one, runs it to completion, and
records the outcome. A failed job keeps its completed stages and resumes from
the next incomplete stage.

## Docker Compose

```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d --build
```

Brings up the API (which queues jobs and serves the UI) and a worker, sharing an
artifacts volume. Job state is SQLite on that volume unless
`INSIGHTFLOW_STATE_DATABASE_URL` points at Supabase. See
[deployment.md](deployment.md) for TLS, authentication and scaling.

## Configuration

Environment variables, prefixed `INSIGHTFLOW_`. The most important ones:

### Core
| Variable | Default | Notes |
| --- | --- | --- |
| `INSIGHTFLOW_ENVIRONMENT` | `local` | `production` tightens defaults |
| `INSIGHTFLOW_ARTIFACT_ROOT` | `artifacts` | Owner-scoped artifact tree |
| `INSIGHTFLOW_STATE_DATABASE_URL` | `sqlite:///./artifacts/insightflow.db` | `postgresql://...` for Postgres / Supabase |
| `INSIGHTFLOW_EXECUTION_MODE` | `inline` (`background` via `python -m insightflow_api`) | `inline`, `background`, `worker` |
| `INSIGHTFLOW_WEB_DIST` | `apps/web/dist` | Built UI served at `/` when present |
| `INSIGHTFLOW_CORS_ORIGINS` | `http://localhost:5173,...` | Only for a UI on another origin |
| `INSIGHTFLOW_LOG_LEVEL` | `INFO` | |
| `INSIGHTFLOW_RETENTION_DAYS` | `7` | |

### Uploads
| Variable | Default |
| --- | --- |
| `INSIGHTFLOW_UPLOAD_MAX_BYTES` | `209715200` (200 MB) |
| `INSIGHTFLOW_UPLOAD_MAX_ROWS` | `5000000` |
| `INSIGHTFLOW_UPLOAD_MAX_COLUMNS` | `1000` |
| `INSIGHTFLOW_UPLOAD_ALLOWED_EXTENSIONS` | `.csv .tsv .txt .xlsx` |

### Sandbox
| Variable | Default | Notes |
| --- | --- | --- |
| `INSIGHTFLOW_SANDBOX_ENABLED` | `true` | |
| `INSIGHTFLOW_SANDBOX_ALLOW_GENERATED_CODE` | `true` | `false` disables code execution |
| `INSIGHTFLOW_SANDBOX_ALLOW_NETWORK` | `false` | Leave false |
| `INSIGHTFLOW_SANDBOX_MEMORY_LIMIT_BYTES` | `2147483648` | |
| `INSIGHTFLOW_SANDBOX_CPU_SECONDS` | `120` | |
| `INSIGHTFLOW_SANDBOX_TIMEOUT_SECONDS` | `120` | |
| `INSIGHTFLOW_SANDBOX_MAX_PROCESSES` | `64` | |
| `INSIGHTFLOW_SANDBOX_MAX_FILE_DESCRIPTORS` | `64` | |
| `INSIGHTFLOW_SANDBOX_CONTAINER_RUNTIME` | auto | `docker` or `podman` |

### Model access
| Variable | Default | Notes |
| --- | --- | --- |
| `INSIGHTFLOW_LLM_ENABLED` | `false` | |
| `INSIGHTFLOW_LLM_API_KEY` | unset | Never logged |
| `INSIGHTFLOW_LLM_BASE_URL` | `https://api.openai.com/v1` | Any OpenAI-compatible endpoint |
| `INSIGHTFLOW_LLM_FAST_MODEL` | `gpt-4o-mini` | Planning and prose |
| `INSIGHTFLOW_LLM_REASONING_MODEL` | `gpt-4o` | Harder planning steps |
| `INSIGHTFLOW_LLM_TEMPERATURE` | `0.0` | |
| `INSIGHTFLOW_LLM_MAX_DATASET_ROWS_TO_MODEL` | `50` | Profiles, never raw data |

With the model disabled, the planner uses intent-driven rules over the profile's
semantic roles and the composer uses templates. Reports are still produced and
still verified — only the prose is less fluent.

## Security posture

- Uploads are untrusted input. Size, type and shape are checked before parsing.
- Headers and cell contents are never treated as instructions.
- Model context is redacted before it can reach a provider, and the redaction is
  audited.
- Only registered tools run against data; parameters are schema-validated first.
- Generated code is AST-validated and sandboxed. `local_process` mode has **no
  isolation** and is development-only.
- Artifacts and jobs are owner-scoped on every read.
- Reports are standalone HTML with no remote assets.
- The audit trail is append-only.

## Known limitations

These are real constraints, not oversights.

**Windows Application Control / Smart App Control.** On hosts with this policy,
uv's generated `.exe` launchers (`insightflow-api.exe`) can be blocked; use
`python -m insightflow_api` and `python -m insightflow_worker` instead. A native
extension (for example a pandas DLL) can be blocked the first time it loads and
allowed afterwards; re-running the command clears it. `pyright`'s runtime binary
may be blocked too, so type-check in CI.

**Authentication.** `X-Owner-Id` isolates tenants but does not authenticate
anyone. Put an identity-aware proxy in front before exposing the service
([deployment.md](deployment.md#4-add-authentication-required-before-exposing-it)).

**Artifacts are local files.** Job state can live in Supabase, but uploads,
charts and reports are files on `INSIGHTFLOW_ARTIFACT_ROOT`, which the API and
every worker must share.

**Feature selection is a screening step.** Rankings come from one seeded random
forest, mutual information and correlation on at most 3,000 rows. They describe
association with the target in this dataset; they are not a substitute for a
properly validated model.

**Large-file performance.** Files at the 200 MB ceiling are parsed into memory.
Row and column ceilings are enforced, but there is no streaming path yet.

## Health checks

```
GET /health
```

Reports process status, configured sandbox mode and whether model access is
enabled. It does not disclose paths or keys.

## Retention

Artifacts older than `INSIGHTFLOW_RETENTION_DAYS` are eligible for cleanup. The
audit trail and state records should be retained independently of artifacts if
you need the history without the files.