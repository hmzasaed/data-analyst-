# Architecture

## Pipeline stages

`JobStage` is a fixed, ordered sequence (`insightflow_contracts.job.STAGE_ORDER`).
A job records which stages it has completed, so a failure resumes from the next
incomplete stage rather than starting over, in the same process or a new one.

| # | Stage | Done by | Produces | Changes reported figures? |
| --- | --- | --- | --- | --- |
| 0 | `registered` | `JobFactory` | `RawFileRecord`, hashed original | – |
| 1 | `ingested` | `IngestionService` | `DatasetSnapshot` v1 | – |
| 2 | `profiled` | `profile_dataset` | `DatasetProfile` (types, roles, PII, quality) | – |
| 3 | `intent_captured` | orchestrator | Objective, exploratory flag | – |
| 4 | `cleaning_proposed` | `plan_cleaning` | `CleaningPlan` (automatic / recommended / needs review) | – |
| 5 | `cleaning_reviewed` | orchestrator, **pauses** when review is required | The user's decisions | – |
| 6 | `cleaning_applied` | `apply_cleaning` | `CleaningExecution`, snapshot v2 | Yes: approved repairs |
| 7 | `preprocessed` | `insightflow_preprocessing` | `PreprocessingResult`, model-ready CSV | Only opt-in imputation, row drops, capping |
| 8 | `features_engineered` | **FeatureEngineeringAgent** | `FeatureEngineeringResult` | No, works on a copy |
| 9 | `features_selected` | **FeatureSelectionAgent** | `FeatureSelectionResult`, feature-set CSV, ranking charts | No |
| 10 | `planned` | **AnalysisPlanner** | `AnalysisPlan` | – |
| 11 | `executed` | `execute_plan` + anomaly scans | `ExecutionResult[]`, evidence tables, charts | Computes them |
| 12 | `verified` | `FindingFactory`, **VerificationAgent** | `Finding[]`, `VerificationResult` | Excludes unverifiable ones |
| 13 | `reported` | EDA module, **ReportComposer**, renderers | HTML, Markdown and JSON report | – |

Statuses: `pending`, `running`, `awaiting_review`, `completed`, `failed`,
`cancelled`. Cancellation is checked between stages.

### Resuming in a new process

A worker or API process that picks up a job part-way through rebuilds what the
earlier stages held in memory: it re-reads the original (re-verifying its hash),
reloads the profile, cleaning plan and analysis plan, re-applies the recorded
cleaning decisions, preprocessing and feature specs, and recomputes execution and
verification rather than trusting stale in-memory results.

### Execution modes

`INSIGHTFLOW_EXECUTION_MODE` decides where stages run: `inline` (inside the
request; tests), `background` (a thread in the API process; the default for
`python -m insightflow_api`), or `worker` (queued; `python -m insightflow_worker`
claims jobs with a conditional `UPDATE ... WHERE status = 'pending'`).

## Packages

```text
contracts      strict Pydantic models for every payload
ingestion      safe CSV/XLSX reading, hashing, snapshots
profiling      type inference, semantic roles, PII, quality issues
cleaning       reviewable repairs with an audit trail
preprocessing  analysis preprocessing and the model-ready export (pandas, scikit-learn)
features       feature transforms, validation, ranking, cross-validation (scikit-learn)
analytics      19 registered analysis tools and the plan executor
anomaly        univariate, group-relative and multivariate detection
visualization  deterministic chart rendering and the EDA module (seaborn, matplotlib)
agents         planner, feature agents, verifier, composer, LLM client
reporting      HTML / Markdown / JSON renderers
storage        owner-scoped artifacts; SQLite or Postgres/Supabase state
orchestration  the resumable stage machine
sandbox        AST validation and resource-limited execution
```

See [agents.md](agents.md) for what each agent may decide and how its output is
validated.

## Contracts

`insightflow_contracts` holds every cross-package model. Nothing else defines a
wire shape. All models inherit `StrictModel`, which forbids extra fields — a
model call that invents a key is a validation error, not a silently dropped
field.

The contracts enforce the honesty rules structurally rather than by convention:

- `MetricValue` carries `source_step`, `table`, `n`, `unit` and `definition`, so
  a number cannot exist without provenance.
- `Finding` requires at least one `Evidence`, and each evidence item must name an
  `artifact_id`, the `metric` it came from and its `value`.
- `Finding` validators reject causal, absolute and accusatory language.
- `VerificationStatus` distinguishes `pass`, `pass_with_warning`, `fail` and
  `skipped` — "skipped" exists so an unrun check is never reported as a pass.

## Evidence and verification

Every tool writes its own output to a CSV artifact and returns metric values that
name the table they came from:

```python
outcome.table(frame, f"ranking_{group}")     # -> tbl_<hash>.csv
outcome.metric(name="top_share", value=..., table="ranking_region")
```

`FindingFactory` resolves each metric to the exact artifact that contains it.
The verifier then re-reads that artifact and confirms the published figure is
actually present. This is what catches a report claiming a figure its own
evidence table does not contain — a class of bug that is easy to introduce and
easy to miss by eye.

Because the artifact ID is a content hash of the step, table name and rows, the
same input produces the same artifact ID. The evaluation suite relies on that
for byte-level reproducibility checks.

## Determinism

- Tool results are plain Python/polars with no randomness unless a seed is given.
- Isolation Forest, when available, is seeded.
- Group iteration is always sorted, so ranking and chart output cannot depend on
  dictionary or filesystem order.
- The report renderer emits no host paths into the artifact body.
- Exploratory charts sample at most 20,000 rows with a fixed seed; feature
  ranking samples at most 3,000 rows with a fixed seed; random forests are
  seeded and single-threaded.

A model is used for three things only: choosing which tools to run, proposing
candidate features (each validated by code), and writing prose. All are optional. With `INSIGHTFLOW_LLM_ENABLED=false` the planner falls
back to intent-driven rules over the profile's semantic roles and the composer
writes templated text. Numbers never come from a model.

## Trust boundaries

```
upload ──▶ ingestion ──▶ profiling ──▶ cleaning ──▶ analytics ──▶ storage
             │                                                  │
             │                                        artifacts (owner-scoped)
```

Three boundaries matter:

1. **Upload → ingestion.** File size, extension, media type and row/column
   ceilings are checked before parsing. Header bytes are treated as data.
2. **Dataset → analytics.** Tools receive an `AnalysisContext`, not the raw
   frame, and cannot reach the filesystem or network. Parameters are validated
   against a declared schema and column types before the tool body runs.
3. **Analysis → report.** Only findings that pass verification reach the
   renderer. The renderer writes a standalone HTML file with no remote assets.

## Generated code

The sandbox exists for the case where a user supplies an expression rather than
picking a tool. It is the highest-risk surface, so it is deliberately narrow:

- The AST validator rejects imports outside the allowlist, `eval`/`exec`, `open`,
  attribute escapes (`__class__`, `__globals__`, `__subclasses__`), bare
  `except BaseException`, and dynamic `with` targets.
- Execution is limited to a safe API object, not builtins.
- `container` mode additionally gets a read-only root, no network, dropped
  capabilities, a non-root user, memory/CPU/file-descriptor limits and a timeout.

## Storage

`ArtifactStore` writes content-addressed files under `artifacts/<owner>/<job>/`,
with kind subdirectories. Every read re-checks owner scoping, so a guessed job ID
cannot cross a tenant boundary.

`StateStore` persists jobs, stage history and the audit trail. It defaults to
SQLite through the standard library; a `postgresql://` URL switches it to
PostgreSQL through `psycopg`, which is how it runs on Supabase. Both backends run
the same SQL, so the SQLite test suite exercises every query. The Supabase schema
is `infra/supabase/migrations/`.

Stage outputs are persisted rather than recomputed. A `GET` on a profile or
finding does not re-run analysis, which keeps reads cheap and keeps the report
consistent with what was actually executed.

## Prompt layers

`prompts/` holds one file per role. Each role builds a layered prompt: role
definition, policy, style, and a JSON context block. Dataset content is always
serialised into the context block and explicitly fenced as untrusted, so a cell
containing "ignore previous instructions" is inert text.

Model context is redacted first: anything matching the redaction rules is
replaced with a placeholder before it can reach a provider, and the redaction is
recorded in the audit trail.