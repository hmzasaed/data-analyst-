import { useState } from "react";
import { compactValue, formatDuration, humanize, parseDate, PHASES, STAGE_HINT, STAGE_LABEL } from "../format";
import type { JobDetail, JobStage, StageRecord } from "../types";
import { Icon } from "./ui";

export type StageState = "pending" | "running" | "waiting" | "succeeded" | "skipped" | "failed";

export interface StageView {
  stage: JobStage;
  state: StageState;
  attempts: number;
  latest: StageRecord | null;
  duration: number | null;
}

function durationOf(record: StageRecord | null, now: number): number | null {
  if (!record) return null;
  if (record.duration_seconds != null) return record.duration_seconds;
  const start = parseDate(record.started_at)?.getTime();
  if (start == null) return null;
  const end = parseDate(record.finished_at)?.getTime() ?? (record.status === "running" ? now : null);
  return end == null ? null : Math.max(0, (end - start) / 1000);
}

export function deriveStages(job: JobDetail, now = Date.now()): StageView[] {
  const records = job.stages ?? [];
  const completed = new Set<string>(job.completed_stages ?? []);
  const active = job.status === "pending" || job.status === "running";

  return PHASES.flatMap((p) => p.stages).map((stage) => {
    const mine = records.filter((r) => r.stage === stage);
    const latest = mine.length ? mine[mine.length - 1] : null;
    const attempts = mine.length ? Math.max(...mine.map((r) => r.attempt ?? 1), mine.length) : 0;

    let state: StageState = "pending";
    if (latest?.status === "running") state = "running";
    else if (latest?.status === "failed" && !completed.has(stage)) state = "failed";
    else if (latest?.status === "skipped") state = "skipped";
    else if (completed.has(stage) || latest?.status === "succeeded") state = "succeeded";
    else if (job.next_stage === stage) {
      if (job.status === "awaiting_review") state = "waiting";
      else if (active) state = "running";
      else if (job.status === "failed") state = "failed";
    }
    return { stage, state, attempts, latest, duration: durationOf(latest, now) };
  });
}

const STATE_LABEL: Record<StageState, string> = {
  pending: "Not started",
  running: "Running",
  waiting: "Waiting for you",
  succeeded: "Done",
  skipped: "Skipped",
  failed: "Failed",
};

function DetailSummary({ detail }: { detail: Record<string, unknown> }) {
  const entries = Object.entries(detail).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (!entries.length) return null;
  return (
    <dl className="detail">
      {entries.slice(0, 6).map(([k, v]) => (
        <div className="detail__item" key={k}>
          <dt>{humanize(k)}</dt>
          <dd className="tnum">{compactValue(v)}</dd>
        </div>
      ))}
      {entries.length > 6 && <div className="detail__more">+{entries.length - 6} more</div>}
    </dl>
  );
}

function StateMark({ state }: { state: StageState }) {
  return (
    <span className={`mark mark--${state}`} aria-hidden="true">
      {state === "succeeded" && <Icon name="check" size={12} />}
      {state === "failed" && <Icon name="x" size={12} />}
      {state === "waiting" && <Icon name="clock" size={12} />}
    </span>
  );
}

export function Lifecycle({ job }: { job: JobDetail }) {
  const views = deriveStages(job);
  const byStage = new Map(views.map((v) => [v.stage, v]));
  const [open, setOpen] = useState<string | null>(null);
  const halted = job.status === "cancelled" || job.status === "rejected";

  const phaseState = (stages: JobStage[]): StageState => {
    const s = stages.map((st) => byStage.get(st)?.state ?? "pending");
    if (s.includes("failed")) return "failed";
    if (s.includes("waiting")) return "waiting";
    if (s.includes("running")) return "running";
    if (s.every((x) => x === "succeeded" || x === "skipped")) return "succeeded";
    if (s.some((x) => x === "succeeded") && !halted && (job.status === "pending" || job.status === "running"))
      return "running";
    return "pending";
  };

  return (
    <section className={`lifecycle${halted ? " is-halted" : ""}`} aria-label="Agent lifecycle">
      <ol className="rail">
        {PHASES.map((phase, i) => {
          const ps = phaseState(phase.stages);
          return (
            <li key={phase.id} className={`rail__phase rail__phase--${ps}`}>
              <div className="rail__ticks" aria-hidden="true">
                {phase.stages.map((st) => (
                  <span key={st} className={`rail__tick rail__tick--${byStage.get(st)?.state ?? "pending"}`} />
                ))}
              </div>
              <div className="rail__label">
                <span className="rail__num tnum">{i + 1}</span>
                <span className="rail__name">{phase.label}</span>
              </div>
              <span className="rail__state">{STATE_LABEL[ps]}</span>
            </li>
          );
        })}
      </ol>

      <div className="timeline">
        {PHASES.map((phase) => (
          <div className="timeline__phase" key={phase.id}>
            <p className="timeline__phasename">{phase.label}</p>
            <ul className="timeline__stages">
              {phase.stages.map((st) => {
                const v = byStage.get(st)!;
                const rec = v.latest;
                const hasDetail = !!rec && Object.keys(rec.detail ?? {}).length > 0;
                const expanded = open === st || v.state === "failed";
                return (
                  <li key={st} className={`stage stage--${v.state}`}>
                    <StateMark state={v.state} />
                    <div className="stage__body">
                      <div className="stage__head">
                        <span className="stage__name">{STAGE_LABEL[st]}</span>
                        <span className="stage__meta">
                          {v.attempts > 1 && <span className="stage__attempt">Attempt {v.attempts}</span>}
                          {v.duration != null && v.state !== "pending" && (
                            <span className="tnum">{formatDuration(v.duration)}</span>
                          )}
                          <span className="stage__state">{STATE_LABEL[v.state]}</span>
                        </span>
                      </div>
                      {v.state === "pending" || !rec ? (
                        <p className="stage__hint">{STAGE_HINT[st]}</p>
                      ) : null}
                      {v.state === "failed" && (rec?.error || job.error) && (
                        <div className="stage__error">
                          {rec?.error_code && <code>{rec.error_code}</code>}
                          <span>{rec?.error ?? job.error}</span>
                        </div>
                      )}
                      {hasDetail && (
                        <>
                          {expanded ? (
                            <DetailSummary detail={rec!.detail} />
                          ) : (
                            <button type="button" className="linkbtn linkbtn--sm" onClick={() => setOpen(st)}>
                              Show details
                            </button>
                          )}
                          {expanded && v.state !== "failed" && (
                            <button type="button" className="linkbtn linkbtn--sm" onClick={() => setOpen(null)}>
                              Hide details
                            </button>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
