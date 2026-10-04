# Deployment

This guide takes InsightFlow from a laptop to a shared server. Pick the shape
that fits, then work through the production checklist at the end.

| Shape | Use it for | Processes | Job state | Artifacts |
| --- | --- | --- | --- | --- |
| **A. Local** | Development, a single analyst | API (background mode) + Vite dev server, or API serving the built UI | SQLite file | Local folder |
| **B. Single server, Docker Compose** | A team on one VM | API + worker(s) in containers | SQLite on a volume, or Supabase | Docker volume |
| **C. Supabase-backed** | Durable, backed-up state; several hosts | As B | Supabase Postgres | Volume on the host that runs jobs |

## What runs where

```text
                  ┌──────────────────────────── host / VM ─────────────────────────────┐
 browser ─TLS──▶  │ reverse proxy (Caddy/nginx) ──▶ API :8000  ──┐                     │
                  │   · TLS, auth, upload limit     · REST + UI   │  job table (queue)  │
                  │                                 · serves      ▼                     │
                  │                                   apps/web  ┌────────────┐          │
                  │                                   /dist     │  worker(s) │          │
                  │                                             └─────┬──────┘          │
                  │  artifacts volume  ◀──────── reads/writes ────────┘                 │
                  └──────────────────────────────────────┬─────────────────────────────┘
                                                         │ state (jobs, stages, audit)
                                                         ▼
                                        SQLite file on the volume  or  Supabase Postgres
```

- **API** (`python -m insightflow_api`): REST API and the built web UI.
- **Worker** (`python -m insightflow_worker`): claims queued jobs with an atomic
  conditional update and runs them. Run as many as the CPU allows.
- **Job state**: jobs, stage history and the audit log. SQLite by default;
  Supabase Postgres when `INSIGHTFLOW_STATE_DATABASE_URL` is a `postgresql://` URL.
- **Artifacts**: uploads, cleaned data, charts, tables, reports, the model-ready
  dataset and the feature set. Files on disk under `INSIGHTFLOW_ARTIFACT_ROOT`;
  **the API and every worker must see the same directory.**

---

## A. Local

See [Running the project](../README.md#running-the-project) in the README. In
short:

```bash
uv sync
uv run python -m insightflow_api           # API on :8000, jobs run in the background
cd apps/web && npm install && npm run dev  # UI on :5173
```

Or build the UI once (`npm run build` in `apps/web`) and open
<http://127.0.0.1:8000/>; the API serves it.

---

## B. Single server with Docker Compose

Requirements: a Linux VM (2+ vCPU, 4 GB+ RAM; feature selection and charts are
CPU-bound), Docker Engine 24+ with the Compose plugin, and a DNS name pointing at
the VM if you want TLS.

### 1. Get the code and configure

```bash
git clone <your-repo-url> insightflow && cd insightflow
cp .env.example .env        # then edit .env
```

The values Compose reads from `.env` (all optional):

```dotenv
# Leave unset for SQLite on the shared volume, or point at Supabase (section C).
INSIGHTFLOW_STATE_DATABASE_URL=
# Optional language model (planning, feature proposals and prose only).
INSIGHTFLOW_LLM_ENABLED=false
INSIGHTFLOW_LLM_API_KEY=
INSIGHTFLOW_LLM_BASE_URL=https://api.openai.com/v1
```

### 2. Build and start

```bash
docker compose -f infra/docker/docker-compose.yml --env-file .env up -d --build
docker compose -f infra/docker/docker-compose.yml ps
curl -fsS http://127.0.0.1:8000/health
```

The image (`infra/docker/app.Dockerfile`) is multi-stage: Node builds the web UI,
then the Python stage installs the locked dependencies (`uv sync --frozen`) and
copies the built UI to `/app/apps/web/dist`. It runs as a non-root user with a
health check.

The Compose stack runs:

- `api` in `worker` execution mode: it only queues jobs;
- `worker`, which claims and runs them, sharing the `artifacts` volume;
- optionally the `sandbox` image, for generated-code execution (see
  [operations](operations.md#sandbox)).

Scale the workers when jobs queue up:

```bash
docker compose -f infra/docker/docker-compose.yml up -d --scale worker=3
```

### 3. Put a reverse proxy in front

The API listens on port 8000 without TLS. Terminate TLS in a proxy and keep 8000
private to the host. The simplest option is **Caddy**, which obtains and renews
certificates automatically:

```caddyfile
# /etc/caddy/Caddyfile
insightflow.example.com {
    request_body {
        max_size 210MB          # uploads are capped at 200 MB by the API
    }
    reverse_proxy 127.0.0.1:8000 {
        transport http {
            read_timeout 300s   # synchronous endpoints can take a while on big files
        }
    }
}
```

With **nginx**:

```nginx
server {
    listen 443 ssl http2;
    server_name insightflow.example.com;
    ssl_certificate     /etc/letsencrypt/live/insightflow.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/insightflow.example.com/privkey.pem;

    client_max_body_size 210m;
    proxy_read_timeout   300s;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

### 4. Add authentication (required before exposing it)

The API identifies a workspace by the **`X-Owner-Id` header and nothing else**.
That isolates tenants from each other, but it is **not authentication**: anyone
who can reach the API can send any owner id. Before the service is reachable
beyond a trusted network, put an identity-aware proxy in front that:

1. authenticates the user (company SSO, Google Workspace, Microsoft Entra ID…);
2. **removes any `X-Owner-Id` sent by the client**;
3. sets `X-Owner-Id` itself from the verified identity (for example the email).

Good options: [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/),
Cloudflare Access, Google IAP, or Caddy/nginx `forward_auth` to your IdP. With
nginx and oauth2-proxy:

```nginx
location / {
    auth_request /oauth2/auth;
    auth_request_set $email $upstream_http_x_auth_request_email;
    proxy_set_header X-Owner-Id $email;     # overrides anything the client sent
    proxy_pass http://127.0.0.1:8000;
}
```

The web UI keeps working unchanged: the proxy's header replaces the one the
browser sends.

### 5. Updating

```bash
git pull
docker compose -f infra/docker/docker-compose.yml up -d --build
```

Stage outputs are versioned JSON, so completed jobs stay readable across
upgrades. Running jobs resume from their last completed stage after a restart.

---

## C. Supabase for job state

Supabase gives you a managed, backed-up Postgres database for jobs, stage
history and the audit log.

1. Create a project at <https://supabase.com>.
2. **Project settings → Database → Connection string → URI.** Copy the
   *transaction pooler* string (port 6543). The direct connection (5432) also works.
3. Set it in `.env` (Compose) or the environment (local):

   ```dotenv
   INSIGHTFLOW_STATE_DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
   ```

4. Start the API and workers. The schema is created on first start. To manage it
   as a migration instead, apply
   `infra/supabase/migrations/20261001000000_insightflow_state.sql` with
   `supabase db push` or the SQL editor.
5. Check `/health`: it should report `"state_backend": "postgres"`.

Notes:

- **Row-level security** is enabled on every table with no policies, so
  Supabase's public REST and anon roles cannot read tenant data. The service
  connects with the database role from the connection string.
- Server-side prepared statements are disabled, which is what makes the
  transaction pooler work.
- Supabase stores **state only**. Artifacts stay on the host's volume, so the
  API and workers that share a volume must still run on the same host (or share
  a network file system).

---

## Production checklist

| | Item | How |
| --- | --- | --- |
| ☐ | TLS | Reverse proxy (step B.3) |
| ☐ | Authentication | Identity-aware proxy that sets `X-Owner-Id` (step B.4) |
| ☐ | Durable state | Supabase (section C), or back up the SQLite file on the volume |
| ☐ | Artifact backups | Snapshot the `artifacts` volume; retention is `INSIGHTFLOW_RETENTION_DAYS` (default 7) |
| ☐ | Execution mode | `worker` with at least one worker; `background` only for single-process setups |
| ☐ | Upload limits | `INSIGHTFLOW_UPLOAD_MAX_BYTES` matches the proxy's body limit |
| ☐ | Language model | Off by default; if on, set the key as a secret and keep consent per upload |
| ☐ | Sandbox | `container` runtime for generated code, or `INSIGHTFLOW_SANDBOX_ALLOW_GENERATED_CODE=false` |
| ☐ | Monitoring | Poll `/health`; worker logs are structured key=value lines (`structlog`) |
| ☐ | CORS | Only needed if the UI is served from a different origin: `INSIGHTFLOW_CORS_ORIGINS` |

## Troubleshooting deployments

| Symptom | Fix |
| --- | --- |
| Jobs stay `pending` | No worker is running, or it cannot reach the same state database. Check `docker compose logs worker`. |
| Worker logs `the stored original for this job is missing` | The worker does not share the API's artifacts volume. Mount the same volume in both. |
| `413` on upload | Raise the proxy body limit (`client_max_body_size` / `max_size`) to match `INSIGHTFLOW_UPLOAD_MAX_BYTES`. |
| `/health` shows `state_backend: sqlite` although Supabase is configured | The URL must start with `postgresql://` or `postgres://`; check the variable reached the container (`docker compose config`). |
| UI shows a blank page at `/` | The image was built without the UI stage, or `INSIGHTFLOW_WEB_DIST` points elsewhere. Rebuild with `--build`. |
