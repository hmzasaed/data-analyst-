import { useState } from "react";
import { api, errorMessage, saveBlob } from "../api";
import { formatNumber, humanize } from "../format";
import { useResource } from "../hooks";
import {
  choiceLabel,
  MISSING_CATEGORICAL,
  MISSING_NUMERIC,
  OUTLIERS,
  SCALING,
  thresholdLabel,
} from "../preprocessing";
import type { PreprocessingResponse, PreprocessingStep } from "../types";
import { Badge, Icon, Notice, ResourceView } from "./ui";

function ColumnChips({ values }: { values: string[] }) {
  return (
    <span className="examples-inline">
      {values.map((v) => (
        <code key={v}>{v}</code>
      ))}
    </span>
  );
}

function StepsTable({ title, steps }: { title: string; steps: PreprocessingStep[] }) {
  return (
    <div className="prep__steps">
      <p className="prep__stepstitle">
        {title} <span className="muted tnum">{steps.length}</span>
      </p>
      {steps.length === 0 ? (
        <p className="muted prep__none">No changes</p>
      ) : (
        <div className="tablewrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Step</th>
                <th scope="col">Detail</th>
                <th scope="col" className="num">
                  Rows
                </th>
                <th scope="col">Columns</th>
              </tr>
            </thead>
            <tbody>
              {steps.map((s) => (
                <tr key={s.step_id}>
                  <th scope="row">
                    <span className="colname">{humanize(s.action)}</span>
                    {s.column && <code className="op__col">{s.column}</code>}
                  </th>
                  <td>{s.detail || <span className="muted">–</span>}</td>
                  <td className="num tnum">{formatNumber(s.rows_affected)}</td>
                  <td>
                    {s.columns_added.length === 0 && s.columns_removed.length === 0 ? (
                      <span className="muted">–</span>
                    ) : (
                      <>
                        {s.columns_added.length > 0 && (
                          <span className="colname__orig">
                            Added {s.columns_added.length}: {s.columns_added.slice(0, 4).join(", ")}
                            {s.columns_added.length > 4 && "…"}
                          </span>
                        )}
                        {s.columns_removed.length > 0 && (
                          <span className="colname__orig">
                            Removed {s.columns_removed.length}: {s.columns_removed.slice(0, 4).join(", ")}
                            {s.columns_removed.length > 4 && "…"}
                          </span>
                        )}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function OptionsUsed({ data }: { data: PreprocessingResponse }) {
  const o = data.options;
  const rows: [string, string][] = [
    ["Missing numbers", choiceLabel(MISSING_NUMERIC, o.missing_numeric)],
    ["Missing categories", choiceLabel(MISSING_CATEGORICAL, o.missing_categorical)],
    ["Outliers", choiceLabel(OUTLIERS, o.outliers)],
  ];
  const exportRows: [string, string][] = [
    ["Drop columns missing over", thresholdLabel(o.drop_columns_missing_above)],
    ["Scaling", choiceLabel(SCALING, o.scaling)],
    ["Encode categoricals", o.encode_categoricals ? "yes" : "no"],
    ["Date features", o.date_features ? "yes" : "no"],
  ];
  return (
    <div className="prep__options">
      {[
        { title: "Analysed data", items: rows },
        { title: "Model-ready export", items: exportRows },
      ].map((g) => (
        <div key={g.title}>
          <p className="prep__stepstitle">{g.title}</p>
          <dl className="detail">
            {g.items.map(([k, v]) => (
              <div className="detail__item" key={k}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

export function PreprocessingTab({
  jobId,
  refreshKey,
  filename,
}: {
  jobId: string;
  refreshKey: string;
  filename?: string | null;
}) {
  const res = useResource(() => api.preprocessing(jobId), `${jobId}:${refreshKey}`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const stem = (filename || "dataset").replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_");

  const download = async (artifactId: string) => {
    setBusy(true);
    setError(null);
    try {
      const blob = await api.artifactBlob(jobId, artifactId);
      saveBlob(blob, `${stem}_model_ready.csv`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ResourceView
      resource={res}
      notReadyTitle="Preprocessing hasn't run yet"
      notReadyText="Analysis-ready and model-ready data appear here after cleaning is applied."
    >
      {(data) => {
        const steps = data.steps ?? [];
        const warnings = data.warnings ?? [];
        const analysisSteps = steps.filter((s) => s.scope === "analysis");
        const exportSteps = steps.filter((s) => s.scope !== "analysis");
        const missingCols = Array.from(
          new Set([...Object.keys(data.missing_before ?? {}), ...Object.keys(data.missing_after ?? {})]),
        )
          .map((col) => {
            const before = data.missing_before?.[col] ?? 0;
            const after = data.missing_after?.[col] ?? 0;
            return { col, before, after, delta: after - before };
          })
          .filter((m) => m.before > 0 || m.after > 0);
        const exp = data.export
          ? {
              ...data.export,
              encoded_columns: data.export.encoded_columns ?? [],
              date_feature_columns: data.export.date_feature_columns ?? [],
              dropped_columns: data.export.dropped_columns ?? [],
            }
          : null;

        return (
          <div className="cleaning">
            <dl className="stats">
              <div className="stat">
                <dt>Analysed rows</dt>
                <dd className="tnum">
                  {data.rows_before === data.rows_after
                    ? formatNumber(data.rows_after)
                    : `${formatNumber(data.rows_before)} → ${formatNumber(data.rows_after)}`}
                </dd>
              </div>
              <div className="stat stat--wide">
                <dt>Analysed data</dt>
                <dd>
                  {data.analysis_changed ? (
                    <Badge tone="warn">Changed</Badge>
                  ) : (
                    <Badge tone="ok">Unchanged</Badge>
                  )}
                </dd>
              </div>
              <div className="stat">
                <dt>Columns</dt>
                <dd className="tnum">
                  {data.columns_before === data.columns_after
                    ? formatNumber(data.columns_after)
                    : `${formatNumber(data.columns_before)} → ${formatNumber(data.columns_after)}`}
                </dd>
              </div>
              <div className="stat">
                <dt>Model-ready export</dt>
                <dd className="tnum">{exp ? `${formatNumber(exp.rows)} × ${formatNumber(exp.columns)}` : "–"}</dd>
              </div>
            </dl>

            {exp && (
              <div className="prep__download">
                <button
                  type="button"
                  className="btn btn--primary"
                  disabled={busy}
                  onClick={() => void download(exp.artifact_id)}
                >
                  <Icon name="download" size={14} /> {busy ? "Preparing" : "Download model-ready dataset (CSV)"}
                </button>
                <span className="muted">
                  {formatNumber(exp.rows)} rows, {formatNumber(exp.columns)} columns
                  {exp.scaling && exp.scaling !== "none" ? `, ${choiceLabel(SCALING, exp.scaling).toLowerCase()} scaling` : ""}
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

            <section className="section">
              <h3 className="h3">Options used</h3>
              <OptionsUsed data={data} />
            </section>

            <section className="section">
              <h3 className="h3">Steps</h3>
              <StepsTable title="Applied to analysed data" steps={analysisSteps} />
              <StepsTable title="Model-ready export" steps={exportSteps} />
            </section>

            <section className="section">
              <h3 className="h3">Missing values</h3>
              {missingCols.length === 0 ? (
                <p className="muted prep__none">No missing values before or after preprocessing.</p>
              ) : (
                <div className="tablewrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">Column</th>
                        <th scope="col" className="num">
                          Before
                        </th>
                        <th scope="col" className="num">
                          After
                        </th>
                        <th scope="col" className="num">
                          Change
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {missingCols.map((m) => (
                        <tr key={m.col}>
                          <th scope="row">
                            <code className="op__col">{m.col}</code>
                          </th>
                          <td className="num tnum">{formatNumber(m.before)}</td>
                          <td className="num tnum">{formatNumber(m.after)}</td>
                          <td
                            className={`num tnum${m.delta < 0 ? " prep__delta--down" : m.delta > 0 ? " prep__delta--up" : ""}`}
                          >
                            {m.delta === 0 ? "–" : `${m.delta > 0 ? "+" : "−"}${formatNumber(Math.abs(m.delta))}`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {exp && (
              <section className="section">
                <h3 className="h3">Export columns</h3>
                {exp.dropped_columns.length > 0 && (
                  <div className="prep__steps">
                    <p className="prep__stepstitle">
                      Dropped <span className="muted tnum">{exp.dropped_columns.length}</span>
                    </p>
                    <div className="tablewrap">
                      <table className="table">
                        <thead>
                          <tr>
                            <th scope="col">Column</th>
                            <th scope="col">Reason</th>
                          </tr>
                        </thead>
                        <tbody>
                          {exp.dropped_columns.map((d) => (
                            <tr key={d.column}>
                              <th scope="row">
                                <code className="op__col">{d.column}</code>
                              </th>
                              <td>{d.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                )}
                <div className="prep__steps">
                  <p className="prep__stepstitle">
                    Encoded <span className="muted tnum">{exp.encoded_columns.length}</span>
                  </p>
                  {exp.encoded_columns.length ? (
                    <ColumnChips values={exp.encoded_columns} />
                  ) : (
                    <p className="muted prep__none">None</p>
                  )}
                </div>
                <div className="prep__steps">
                  <p className="prep__stepstitle">
                    Date features <span className="muted tnum">{exp.date_feature_columns.length}</span>
                  </p>
                  {exp.date_feature_columns.length ? (
                    <ColumnChips values={exp.date_feature_columns} />
                  ) : (
                    <p className="muted prep__none">None</p>
                  )}
                </div>
              </section>
            )}
          </div>
        );
      }}
    </ResourceView>
  );
}
