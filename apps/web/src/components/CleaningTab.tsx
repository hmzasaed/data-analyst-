import { api } from "../api";
import { formatNumber, humanize } from "../format";
import { useResource } from "../hooks";
import { REVIEW_LABEL, REVIEW_TONE } from "./CleaningReview";
import { Badge, EmptyState, ResourceView } from "./ui";

function Examples({ values }: { values: string[] }) {
  if (!values.length) return <span className="muted">–</span>;
  return (
    <span className="examples-inline">
      {values.slice(0, 3).map((v, i) => (
        <code key={`${v}-${i}`}>{v === "" ? "(empty)" : v}</code>
      ))}
    </span>
  );
}

export function CleaningTab({ jobId, refreshKey }: { jobId: string; refreshKey: string }) {
  const res = useResource(() => api.cleaning(jobId), `${jobId}:${refreshKey}`);

  return (
    <ResourceView
      resource={res}
      notReadyTitle="No cleaning plan yet"
      notReadyText="Proposed fixes appear here after profiling."
    >
      {(data) => {
        const rejected = new Set(data.rejected);
        const pending = data.operations.filter(
          (op) => !rejected.has(op.operation_id) && !data.applied.some((a) => a.operation_id === op.operation_id),
        );
        return (
          <div className="cleaning">
            <dl className="stats">
              <div className="stat">
                <dt>Proposed</dt>
                <dd className="tnum">{data.summary.total_operations}</dd>
              </div>
              <div className="stat">
                <dt>Applied</dt>
                <dd className="tnum">{data.applied.length}</dd>
              </div>
              <div className="stat">
                <dt>Rejected</dt>
                <dd className="tnum">{data.rejected.length}</dd>
              </div>
              <div className="stat">
                <dt>Rows touched</dt>
                <dd className="tnum">{formatNumber(data.summary.rows_touched)}</dd>
              </div>
            </dl>

            <section className="section">
              <h3 className="h3">Change log</h3>
              {data.applied.length === 0 ? (
                <EmptyState title="Nothing has been applied yet">
                  {pending.length > 0
                    ? `${pending.length} proposed ${pending.length === 1 ? "fix is" : "fixes are"} waiting to be applied.`
                    : "No changes were made to the data."}
                </EmptyState>
              ) : (
                <div className="tablewrap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th scope="col">Change</th>
                        <th scope="col">Level</th>
                        <th scope="col" className="num">
                          Rows
                        </th>
                        <th scope="col" className="num">
                          Missing
                        </th>
                        <th scope="col">Before</th>
                        <th scope="col">After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.applied.map((e) => (
                        <tr key={e.operation_id}>
                          <th scope="row">
                            <span className="colname">{humanize(e.kind)}</span>
                            {e.column && <code className="op__col">{e.column}</code>}
                            <span className="colname__orig">{e.reason}</span>
                          </th>
                          <td>
                            <Badge tone={REVIEW_TONE[e.review_level]}>{REVIEW_LABEL[e.review_level]}</Badge>
                          </td>
                          <td className="num tnum">
                            {formatNumber(e.rows_affected)}
                            {e.rows_before !== e.rows_after && (
                              <span className="colname__orig">
                                {formatNumber(e.rows_before)} to {formatNumber(e.rows_after)}
                              </span>
                            )}
                          </td>
                          <td className="num tnum">
                            {e.null_count_before != null && e.null_count_after != null
                              ? `${formatNumber(e.null_count_before)} to ${formatNumber(e.null_count_after)}`
                              : "–"}
                          </td>
                          <td>
                            <Examples values={e.examples_before} />
                          </td>
                          <td>
                            <Examples values={e.examples_after} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>

            {data.rejected.length > 0 && (
              <section className="section">
                <h3 className="h3">Rejected</h3>
                <ul className="ops ops--plain">
                  {data.operations
                    .filter((op) => rejected.has(op.operation_id))
                    .map((op) => (
                      <li key={op.operation_id} className="op is-rejected">
                        <div className="op__main">
                          <div className="op__title">
                            <span className="op__kind">{humanize(op.kind)}</span>
                            {op.column && <code className="op__col">{op.column}</code>}
                          </div>
                          <p className="op__reason">{op.reason}</p>
                        </div>
                      </li>
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
