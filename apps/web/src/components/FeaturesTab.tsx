import { useState } from "react";
import { api, errorMessage, saveBlob } from "../api";
import { fmt3, higherIsBetter } from "../features";
import { formatNumber, humanize } from "../format";
import { useResource } from "../hooks";
import type { CrossValidation, FeatureScore, FeaturesResponse } from "../types";
import { Badge, EmptyState, Icon, Notice, ResourceView, type Tone } from "./ui";

const TOP_N = 25;

const ORIGIN_TONE: Record<FeatureScore["origin"], Tone> = {
  original: "neutral",
  engineered: "accent",
  encoded: "info",
};

const ENGINEERING_SOURCE: Record<FeaturesResponse["engineering"]["source"], string> = {
  deterministic: "Rule-based",
  model: "Language model",
  deterministic_fallback: "Rule-based (model unavailable)",
  skipped: "Skipped",
};

/** Rank ascending, unranked last, then name for a stable order. */
function byRank(a: FeatureScore, b: FeatureScore): number {
  const ra = a.combined_rank ?? Number.POSITIVE_INFINITY;
  const rb = b.combined_rank ?? Number.POSITIVE_INFINITY;
  if (ra !== rb) return ra - rb;
  if (a.selected !== b.selected) return a.selected ? -1 : 1;
  return a.feature.localeCompare(b.feature);
}

function ColumnChips({ values }: { values: string[] }) {
  if (!values.length) return <span className="muted">–</span>;
  return (
    <span className="examples-inline">
      {values.map((v) => (
        <code key={v}>{v}</code>
      ))}
    </span>
  );
}

function meanStd(mean: number | null, std: number | null): string {
  if (mean == null) return "–";
  return std == null ? fmt3(mean) : `${fmt3(mean)} ± ${fmt3(std)}`;
}

function CvSummary({ cv }: { cv: CrossValidation }) {
  const better = higherIsBetter(cv.metric);
  const comparable = better && cv.selected_mean != null && cv.all_features_mean != null;
  const delta = comparable ? cv.selected_mean! - cv.all_features_mean! : 0;
  return (
    <div className="feat__cv">
      <p className="feat__cvline tnum">
        <span className="feat__cvmetric">{cv.metric}:</span> selected {meanStd(cv.selected_mean, cv.selected_std)} (
        {formatNumber(cv.selected_feature_count)} features) vs all {meanStd(cv.all_features_mean, cv.all_features_std)}{" "}
        ({formatNumber(cv.all_feature_count)} features)
        {comparable && (
          <>
            {" "}
            <Badge tone={delta >= 0 ? "ok" : "warn"}>
              {delta >= 0 ? "Selected set holds up" : "Selected set scores lower"}
            </Badge>
          </>
        )}
      </p>
      <p className="feat__cvnote">
        {cv.folds}-fold cross-validation with {cv.model}. Higher is better for r2, accuracy and f1.
      </p>
    </div>
  );
}

function RankingBars({ scores }: { scores: FeatureScore[] }) {
  const top = [...scores].sort(byRank).slice(0, TOP_N);
  const useImportance = top.some((s) => s.importance != null);
  const valueOf = (s: FeatureScore) => (useImportance ? s.importance : s.mutual_information);
  const max = Math.max(0, ...top.map((s) => valueOf(s) ?? 0));

  return (
    <div className="feat__ranking">
      <p className="prep__stepstitle">
        Top {Math.min(TOP_N, top.length)} by combined rank{" "}
        <span className="muted">bar shows {useImportance ? "model importance" : "mutual information"}</span>
      </p>
      <ol className="featbars">
        {top.map((s) => {
          const v = valueOf(s);
          const pct = max > 0 && v != null ? Math.max(0, (v / max) * 100) : 0;
          return (
            <li key={s.feature} className={`featbar${s.selected ? " is-selected" : ""}`}>
              <span className="featbar__rank tnum">{s.combined_rank ?? "–"}</span>
              <span className="featbar__label">
                <span className="featbar__name" title={s.feature}>
                  {s.feature}
                </span>
                {!s.selected && s.status && <span className="featbar__status">{s.status}</span>}
              </span>
              <span className="featbar__track" aria-hidden="true">
                <span className="featbar__fill" style={{ width: `${pct}%` }} />
              </span>
              <span className="featbar__value tnum">{fmt3(v)}</span>
            </li>
          );
        })}
      </ol>
      <p className="featbars__legend">
        <span className="featbars__key is-selected" aria-hidden="true" /> Selected
        <span className="featbars__key" aria-hidden="true" /> Dropped
      </p>
    </div>
  );
}

function ScoresTable({ scores }: { scores: FeatureScore[] }) {
  const rows = [...scores].sort(byRank);
  return (
    <div className="tablewrap">
      <table className="table">
        <thead>
          <tr>
            <th scope="col">Feature</th>
            <th scope="col">Origin</th>
            <th scope="col" className="num">
              MI
            </th>
            <th scope="col" className="num">
              |corr|
            </th>
            <th scope="col" className="num">
              Importance
            </th>
            <th scope="col" className="num">
              Rank
            </th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.feature}>
              <th scope="row">
                <code className="op__col">{s.feature}</code>
              </th>
              <td>
                <Badge tone={ORIGIN_TONE[s.origin] ?? "neutral"}>{humanize(s.origin)}</Badge>
              </td>
              <td className="num tnum">{fmt3(s.mutual_information)}</td>
              <td className="num tnum">{fmt3(s.correlation == null ? null : Math.abs(s.correlation))}</td>
              <td className="num tnum">{fmt3(s.importance)}</td>
              <td className="num tnum">{s.combined_rank ?? "–"}</td>
              <td>
                {s.selected ? (
                  <Badge tone="ok">Selected</Badge>
                ) : (
                  <span className="muted">{s.status || "Dropped"}</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function FeaturesTab({
  jobId,
  refreshKey,
  filename,
}: {
  jobId: string;
  refreshKey: string;
  filename?: string | null;
}) {
  const res = useResource(() => api.features(jobId), `${jobId}:${refreshKey}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showRejected, setShowRejected] = useState(false);

  const stem = (filename || "dataset").replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_");

  const download = async (artifactId: string) => {
    setBusy(true);
    setError(null);
    try {
      const blob = await api.artifactBlob(jobId, artifactId);
      saveBlob(blob, `${stem}_feature_set.csv`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResourceView
      resource={res}
      notReadyTitle="Features haven't been selected yet"
      notReadyText="Engineered features and the ranking appear here after preprocessing, once the agent has selected a feature set."
    >
      {(data) => {
        const eng = data.engineering;
        const sel = data.selection;
        const created = eng.created ?? [];
        const rejected = eng.rejected ?? [];
        const scores = sel.scores ?? [];
        const selected = sel.selected ?? [];
        const methods = sel.methods ?? [];
        const warnings = [...(eng.warnings ?? []), ...(sel.warnings ?? [])];
        const target = sel.target_column ?? eng.target_column;
        const task = sel.task ?? eng.task;

        if (eng.skipped_reason && sel.skipped_reason) {
          return (
            <EmptyState title="Feature engineering and selection were skipped">
              <p>{eng.skipped_reason}</p>
              {sel.skipped_reason !== eng.skipped_reason && <p>{sel.skipped_reason}</p>}
            </EmptyState>
          );
        }

        return (
          <div className="cleaning">
            {eng.skipped_reason && (
              <section className="section">
                <Notice tone="info" title="Feature engineering was skipped">
                  {eng.skipped_reason}
                </Notice>
              </section>
            )}
            {sel.skipped_reason && (
              <section className="section">
                <Notice tone="info" title="Feature selection was skipped">
                  {sel.skipped_reason}
                </Notice>
              </section>
            )}

            <dl className="stats">
              <div className="stat stat--wide">
                <dt>Target</dt>
                <dd className="feat__target-dd">
                  {target ? <code className="op__col">{target}</code> : <span className="muted">None</span>}
                  {task && <Badge tone="accent">{humanize(task)}</Badge>}
                </dd>
              </div>
              <div className="stat">
                <dt>Candidates → selected</dt>
                <dd className="tnum">
                  {formatNumber(sel.candidate_count)} → {formatNumber(selected.length)}
                </dd>
              </div>
              <div className="stat">
                <dt>Engineered</dt>
                <dd className="tnum">{formatNumber(created.length)}</dd>
              </div>
              <div className="stat">
                <dt>Rows used</dt>
                <dd className="tnum">{formatNumber(sel.rows_used)}</dd>
              </div>
            </dl>

            {(eng.target_reason || sel.cross_validation) && (
              <div className="feat__summary">
                {eng.target_reason && (
                  <p className="feat__why">
                    <span className="feat__whylabel">Why this target</span> {eng.target_reason}
                  </p>
                )}
                {sel.cross_validation && <CvSummary cv={sel.cross_validation} />}
              </div>
            )}

            {sel.artifact_id && (
              <div className="prep__download">
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy}
                  onClick={() => void download(sel.artifact_id!)}
                >
                  <Icon name="download" size={14} /> {busy ? "Preparing" : "Download feature set (CSV)"}
                </button>
                <span className="muted">
                  {formatNumber(selected.length)} selected features, {formatNumber(sel.rows_used)} rows
                </span>
              </div>
            )}
            {error && <Notice tone="bad" title="Download failed">{error}</Notice>}

            {warnings.length > 0 && (
              <section className="section">
                <Notice tone="warn" title="Warnings">
                  <ul className="bullets">
                    {warnings.map((w, i) => (
                      <li key={`${w}-${i}`}>{w}</li>
                    ))}
                  </ul>
                </Notice>
              </section>
            )}

            {!eng.skipped_reason && (
              <section className="section">
                <h3 className="h3">
                  Engineered features{" "}
                  <span className="feat__h3meta">
                    {ENGINEERING_SOURCE[eng.source] ?? humanize(eng.source)}
                    {eng.model_used ? `, ${eng.model_used}` : ""}
                  </span>
                </h3>
                {created.length === 0 ? (
                  <p className="muted prep__none">No new features were kept.</p>
                ) : (
                  <div className="tablewrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th scope="col">Name</th>
                          <th scope="col">Kind</th>
                          <th scope="col">Inputs</th>
                          <th scope="col">Source</th>
                          <th scope="col">Rationale</th>
                        </tr>
                      </thead>
                      <tbody>
                        {created.map((f) => (
                          <tr key={f.spec.name}>
                            <th scope="row">
                              <code className="op__col">{f.spec.name}</code>
                            </th>
                            <td>
                              <span className="feat__chip">{humanize(f.spec.kind)}</span>
                            </td>
                            <td>
                              <ColumnChips values={f.spec.inputs ?? []} />
                            </td>
                            <td>
                              <Badge tone={f.source === "model" ? "accent" : "neutral"}>
                                {f.source === "model" ? "Model" : "Rule"}
                              </Badge>
                            </td>
                            <td className="feat__rationale">{f.spec.rationale || <span className="muted">–</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {rejected.length > 0 && (
                  <div className="feat__rejected">
                    <button
                      type="button"
                      className="linkbtn"
                      aria-expanded={showRejected}
                      onClick={() => setShowRejected((v) => !v)}
                    >
                      {showRejected ? "Hide" : "Show"} rejected proposals ({rejected.length})
                    </button>
                    {showRejected && (
                      <ul className="issues">
                        {rejected.map((r, i) => (
                          <li key={`${r.name}-${i}`} className="issue">
                            <div className="issue__head">
                              <code className="op__col">{r.name}</code>
                              <span className="feat__chip">{humanize(r.kind)}</span>
                            </div>
                            <p className="issue__desc">{r.reason}</p>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </section>
            )}

            {scores.length > 0 && (
              <section className="section">
                <h3 className="h3">Feature ranking</h3>
                <RankingBars scores={scores} />
                <div className="prep__steps">
                  <p className="prep__stepstitle">
                    All candidates <span className="muted tnum">{scores.length}</span>
                  </p>
                  <ScoresTable scores={scores} />
                </div>
              </section>
            )}

            {methods.length > 0 && (
              <section className="section">
                <h3 className="h3">Methods</h3>
                <ul className="bullets feat__methods">
                  {methods.map((m, i) => (
                    <li key={`${m}-${i}`}>{m}</li>
                  ))}
                </ul>
              </section>
            )}
          </div>
        );
      }}
    </ResourceView>
  );
}
