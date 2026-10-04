import { api } from "../api";
import { formatBytes, formatNumber, formatPercent, humanize } from "../format";
import { useResource } from "../hooks";
import type { PiiRisk, Severity } from "../types";
import { Badge, EmptyState, ResourceView, type Tone } from "./ui";

const PII_TONE: Record<PiiRisk, Tone> = { none: "neutral", low: "info", medium: "warn", high: "bad" };
export const SEVERITY_TONE: Record<Severity, Tone> = { info: "neutral", low: "info", medium: "warn", high: "bad" };

export function ProfileTab({ jobId, refreshKey }: { jobId: string; refreshKey: string }) {
  const res = useResource(() => api.profile(jobId), `${jobId}:${refreshKey}`);

  return (
    <ResourceView
      resource={res}
      notReadyTitle="The dataset hasn't been profiled yet"
      notReadyText="Column types, roles and quality issues appear here a few seconds after upload."
    >
      {(p) => (
        <div className="profile">
          <dl className="stats">
            <div className="stat">
              <dt>Rows</dt>
              <dd className="tnum">{formatNumber(p.row_count)}</dd>
            </div>
            <div className="stat">
              <dt>Columns</dt>
              <dd className="tnum">{formatNumber(p.column_count)}</dd>
            </div>
            <div className="stat">
              <dt>Duplicate rows</dt>
              <dd className="tnum">{formatNumber(p.duplicate_row_count)}</dd>
            </div>
            <div className="stat">
              <dt>In memory</dt>
              <dd className="tnum">{formatBytes(p.estimated_memory_bytes)}</dd>
            </div>
            {p.time_coverage?.min && (
              <div className="stat stat--wide">
                <dt>Time coverage</dt>
                <dd className="tnum">
                  {p.time_coverage.min.slice(0, 10)} to {p.time_coverage.max?.slice(0, 10)}
                </dd>
              </div>
            )}
            {p.sheet_name && (
              <div className="stat">
                <dt>Sheet</dt>
                <dd>{p.sheet_name}</dd>
              </div>
            )}
          </dl>

          <section className="section">
            <h3 className="h3">Columns</h3>
            <div className="tablewrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Column</th>
                    <th scope="col">Type</th>
                    <th scope="col">Role</th>
                    <th scope="col" className="num">
                      Missing
                    </th>
                    <th scope="col" className="num">
                      Unique
                    </th>
                    <th scope="col">Personal data</th>
                    <th scope="col">Sample</th>
                  </tr>
                </thead>
                <tbody>
                  {p.columns.map((c) => (
                    <tr key={c.name}>
                      <th scope="row">
                        <span className="colname">{c.name}</span>
                        {c.original_name && c.original_name !== c.name && (
                          <span className="colname__orig">was “{c.original_name}”</span>
                        )}
                      </th>
                      <td>{humanize(c.inferred_type)}</td>
                      <td>{humanize(c.semantic_role)}</td>
                      <td className="num tnum">
                        <span className="nullbar" aria-hidden="true">
                          <span style={{ width: `${Math.min(100, c.null_fraction * 100)}%` }} />
                        </span>
                        {formatPercent(c.null_fraction)}
                      </td>
                      <td className="num tnum">{formatNumber(c.unique_count)}</td>
                      <td>
                        {c.pii_risk === "none" ? (
                          <span className="muted">None</span>
                        ) : (
                          <Badge tone={PII_TONE[c.pii_risk]} title={c.pii_signals.join(", ")}>
                            {humanize(c.pii_risk)} risk
                          </Badge>
                        )}
                      </td>
                      <td className="sample">{c.sample_values.slice(0, 3).join(", ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="section">
            <h3 className="h3">Quality issues</h3>
            {p.quality_issues.length === 0 ? (
              <EmptyState title="No quality issues detected" />
            ) : (
              <ul className="issues">
                {p.quality_issues.map((q, i) => (
                  <li key={`${q.kind}-${q.column}-${i}`} className="issue">
                    <div className="issue__head">
                      <Badge tone={SEVERITY_TONE[q.severity]}>{humanize(q.severity)}</Badge>
                      <span className="issue__kind">{humanize(q.kind)}</span>
                      {q.column && <code className="op__col">{q.column}</code>}
                      {q.affected_rows > 0 && (
                        <span className="muted tnum">
                          {formatNumber(q.affected_rows)} rows ({formatPercent(q.affected_fraction)})
                        </span>
                      )}
                    </div>
                    <p className="issue__desc">{q.description}</p>
                    {q.recommended_action && <p className="issue__action">{q.recommended_action}</p>}
                  </li>
                ))}
              </ul>
            )}
          </section>

          {p.warnings.length > 0 && (
            <section className="section">
              <h3 className="h3">Profiler warnings</h3>
              <ul className="bullets">
                {p.warnings.map((w) => (
                  <li key={w.code + w.message}>{w.message}</li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </ResourceView>
  );
}
