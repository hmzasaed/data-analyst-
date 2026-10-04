import { api } from "../api";
import { compactValue, formatTimestamp, humanize, STAGE_LABEL } from "../format";
import { useResource } from "../hooks";
import { EmptyState, ResourceView } from "./ui";

export function AuditTab({ jobId, refreshKey }: { jobId: string; refreshKey: string }) {
  const res = useResource(() => api.audit(jobId), `${jobId}:${refreshKey}`);

  return (
    <ResourceView resource={res} notReadyTitle="No audit events yet">
      {(data) =>
        data.events.length === 0 ? (
          <EmptyState title="No audit events yet" />
        ) : (
          <ol className="audit">
            {data.events.map((e, i) => {
              const ok = !e.outcome || e.outcome === "ok" || e.outcome === "succeeded";
              const detail = Object.entries(e.detail ?? {});
              return (
                <li key={`${e.at}-${i}`} className={`audit__event${ok ? "" : " is-bad"}`}>
                  <time className="audit__time tnum" dateTime={e.at}>
                    {formatTimestamp(e.at)}
                  </time>
                  <div className="audit__body">
                    <p className="audit__line">
                      <span className="audit__action">{e.action}</span>
                      <span className="audit__actor">by {e.actor}</span>
                      {e.stage && <span className="audit__stage">{STAGE_LABEL[e.stage] ?? humanize(e.stage)}</span>}
                      {!ok && <span className="audit__outcome">{e.outcome}</span>}
                    </p>
                    {e.subject && <p className="audit__subject">{e.subject}</p>}
                    {detail.length > 0 && (
                      <p className="audit__detail">
                        {detail
                          .slice(0, 6)
                          .map(([k, v]) => `${humanize(k)}: ${compactValue(v)}`)
                          .join("; ")}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        )
      }
    </ResourceView>
  );
}
