# InsightFlow web

React 18 + TypeScript + Vite frontend for the InsightFlow API. No UI kit, no router, no chart library: charts arrive as server-rendered PNGs inside the report.

## Run

```bash
# 1. backend (from the repo root), on :8000
uv run python -m insightflow_api        # runs jobs in the background so the UI can watch them

# 2. frontend
cd apps/web
npm install
npm run dev          # http://localhost:5173
```

The dev server proxies `/api` and `/health` to `http://127.0.0.1:8000`, so no CORS setup is needed.

```bash
npm run build        # tsc -b && vite build, output in apps/web/dist
npm run preview      # serve the built bundle locally
```

When `apps/web/dist` exists the backend serves it at `/`, so a production build needs no separate web server.

## Workspaces

The API identifies the tenant with an `X-Owner-Id` header on every request. On first visit the app generates `user-<uuid>` and keeps it in `localStorage` (key `insightflow.owner_id`). Open the workspace popover (top right) to copy it, paste another id to switch workspaces, or generate a new one.

## Layout

```
src/
  api.ts            typed fetch client: owner header, error parsing (detail string or {message, available_sheets, suggestions})
  types.ts          response types mirroring packages/contracts
  format.ts         stage order, phases, labels, number/time formatting
  preprocessing.ts  preprocessing defaults, labels and summary line
  features.ts       feature-agent defaults, clamping, summary line, number formatting
  hooks.ts          hash routing (#/new, #/jobs/:id), polling, sub-resource fetching
  styles.css        design tokens (light + dark via prefers-color-scheme) and component styles
  components/
    Sidebar.tsx       analysis list, refreshed every 3 s while a job runs, otherwise every 8 s
    TopBar.tsx        backend health: language model on/off, storage backend, execution mode
    OwnerPopover.tsx  workspace id
    Landing.tsx       the lifecycle steps
    NewAnalysis.tsx   upload, question, review toggle, LLM consent, sheet picker on 415
    PreprocessingPanel.tsx  preprocessing options (analysed data vs model-ready export)
    FeaturesPanel.tsx       feature agents: enable, target column, max selected / engineered
    JobView.tsx       header, actions, polling (1.5 s while pending/running), tabs
    Lifecycle.tsx     phase rail + per-stage timeline (status, attempts, duration, detail, errors)
    CleaningReview.tsx approve/reject proposed operations when the job is awaiting review
    ReportTab.tsx     auto-sized iframe (report?format=html&embed=true, no scripts), open in new tab, print, HTML/Markdown/JSON downloads
    FindingsTab.tsx   findings with evidence, verification checks, warnings, flagged records
    ProfileTab.tsx    row/column counts, column table, quality issues
    CleaningTab.tsx   applied change log with before/after examples
    PreprocessingTab.tsx  what preprocessing changed, missing values before/after, model-ready CSV download
    FeaturesTab.tsx   target and task, engineered and rejected features, ranking bars and table, cross-validation, feature-set download
    AuditTab.tsx      chronological audit events
    ui.tsx            shared pill, badge, notice, empty state, spinner, icons
```

A `409` from any job sub-resource means that stage hasn't produced output yet, and the UI shows it as an empty state rather than an error.
