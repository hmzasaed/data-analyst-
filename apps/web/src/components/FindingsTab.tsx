import { useState } from "react";
import { api } from "../api";
import { compactValue, humanize } from "../format";
import { useResource } from "../hooks";
import type { Finding, VerificationStatus } from "../types";
import { Badge, EmptyState, Notice, ResourceView, type Tone } from "./ui";

const FINDING_TONE: Record<Finding["status"], Tone> = { verified: "ok", needs_review: "warn", rejected: "bad" };
const CHECK_TONE: Record<VerificationStatus, Tone> = { pass: "ok", pass_with_warning: "warn", fail: "bad", skipped: "neutral" };
const CHECK_LABEL: Record<VerificationStatus, string> = {
  pass: "Passed",
  pass_with_warning: "Passed with warning",
  fail: "Failed",
  skipped: "Skipped",
};

function FindingCard({ finding }: { finding: Finding }) {
  return (
    <article className={`finding finding--${finding.status}`}>
      <header className="finding__head">
        <h3 className="finding__title">{finding.title}</h3>
        <span className="finding__badges">
          <Badge tone={FINDING_TONE[finding.status]}>{humanize(finding.status)}</Badge>
          <Badge tone="neutral" title="How strongly the evidence supports the statement">
            {humanize(finding.confidence)} confidence
          </Badge>
        </span>
      </header>
      <p className="finding__statement">{finding.statement}</p>

      {finding.evidence.length > 0 && (
        <table className="evidence">
          <caption className="visually-hidden">Evidence</caption>
          <thead>
            <tr>
              <th scope="col">Metric</th>
              <th scope="col" className="num">
                Value
              </th>
              <th scope="col">Compared with</th>
              <th scope="col">Artifact</th>
            </tr>
          </thead>
          <tbody>
            {finding.evidence.map((ev, i) => (
              <tr key={`${ev.artifact_id}-${ev.metric}-${i}`}>
                <td>{humanize(ev.metric)}</td>
                <td className="num tnum">
                  {compactValue(ev.value)}
                  {ev.unit ? ` ${ev.unit}` : ""}
                </td>
                <td>{ev.comparator ?? "–"}</td>
                <td>
                  <code className="evidence__id" title={ev.artifact_id}>
                    {ev.artifact_id.length > 14 ? `${ev.artifact_id.slice(0, 12)}…` : ev.artifact_id}
                  </code>
                  {ev.dataset_version != null && <span className="muted"> v{ev.dataset_version}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {finding.limitations.length > 0 && (
        <div className="finding__limits">
          <p className="finding__label">Limitations</p>
          <ul className="bullets">
            {finding.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </div>
      )}
      {finding.unsupported_claims.length > 0 && (
        <Notice tone="warn" title="Unsupported wording was removed">
          <ul className="bullets">
            {finding.unsupported_claims.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </Notice>
      )}
      {finding.tags.length > 0 && (
        <div className="finding__tags">
          {finding.tags.map((t) => (
            <span className="tag" key={t}>
              {t}
            </span>
          ))}
        </div>
      )}
    </article>
  );
}

export function FindingsTab({ jobId, refreshKey }: { jobId: string; refreshKey: string }) {
  const res = useResource(() => api.findings(jobId), `${jobId}:${refreshKey}`);
  const [allChecks, setAllChecks] = useState(false);

  return (
    <ResourceView
      resource={res}
      notReadyTitle="No findings yet"
      notReadyText="Findings appear after the analysis runs and each one is checked against its evidence."
    >
      {(data) => {
        const checks = data.verification?.checks ?? [];
        const tally = checks.reduce<Record<string, number>>((acc, c) => {
          acc[c.status] = (acc[c.status] ?? 0) + 1;
          return acc;
        }, {});
        const problems = checks
          .filter((c) => c.status === "fail" || c.status === "pass_with_warning")
          .sort((a, b) => (a.status === b.status ? 0 : a.status === "fail" ? -1 : 1));
        return (
          <div className="findings">
            {data.verification && (
              <section className="verify">
                <div className="verify__head">
                  <h3 className="h3">Verification</h3>
                  <div className="verify__tally">
                    {(["pass", "pass_with_warning", "fail", "skipped"] as VerificationStatus[]).map((s) =>
                      tally[s] ? (
                        <Badge key={s} tone={CHECK_TONE[s]}>
                          {tally[s]} {CHECK_LABEL[s].toLowerCase()}
                        </Badge>
                      ) : null,
                    )}
                  </div>
                </div>
                {data.verification.summary && <p className="verify__summary">{data.verification.summary}</p>}
                {problems.length > 0 && (
                  <ul className="checks">
                    {(allChecks ? problems : problems.slice(0, 4)).map((c, i) => (
                      <li key={`${c.check_id}-${i}`} className={`check check--${c.status}`}>
                        <span className="check__name">{humanize(c.name)}</span>
                        <span className="check__detail">{c.detail}</span>
                        {(c.expected || c.actual) && (
                          <span className="check__vals tnum">
                            {c.expected && <>Expected {c.expected}</>}
                            {c.expected && c.actual && ", "}
                            {c.actual && <>got {c.actual}</>}
                          </span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
                {problems.length > 4 && (
                  <button type="button" className="linkbtn verify__more" onClick={() => setAllChecks((v) => !v)}>
                    {allChecks ? "Show fewer checks" : `Show all ${problems.length} checks that need attention`}
                  </button>
                )}
                {data.verification.contradictions.length > 0 && (
                  <Notice tone="warn" title="Contradictions between findings">
                    <ul className="bullets">
                      {data.verification.contradictions.map((c) => (
                        <li key={c}>{c}</li>
                      ))}
                    </ul>
                  </Notice>
                )}
              </section>
            )}

            {data.warnings.length > 0 && (
              <Notice tone="warn" title="Warnings from the run">
                <ul className="bullets">
                  {data.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </Notice>
            )}

            {data.findings.length === 0 ? (
              <EmptyState title="The analysis produced no findings" />
            ) : (
              <div className="findings__list">
                {data.findings.map((f) => (
                  <FindingCard key={f.finding_id} finding={f} />
                ))}
              </div>
            )}

            {data.anomalies.length > 0 && (
              <section className="anomalies">
                <h3 className="h3">Flagged for review</h3>
                <p className="muted">
                  These records look unusual for the stated baseline. That warrants a look, not a conclusion that they
                  are errors.
                </p>
                <ul className="anomalies__list">
                  {data.anomalies.slice(0, 50).map((a, i) => (
                    <li key={a.anomaly_id ?? i} className="anomaly">
                      <span className="anomaly__label">{a.entity_label ?? a.anomaly_id ?? `Record ${i + 1}`}</span>
                      {a.observed_value != null && <span className="tnum">{compactValue(a.observed_value)}</span>}
                      {a.method && <span className="muted">{humanize(String(a.method))}</span>}
                      {a.reason && <span className="anomaly__reason">{a.reason}</span>}
                    </li>
                  ))}
                </ul>
                {data.anomalies.length > 50 && (
                  <p className="muted">{data.anomalies.length - 50} more are listed in the report exports.</p>
                )}
              </section>
            )}
          </div>
        );
      }}
    </ResourceView>
  );
}
