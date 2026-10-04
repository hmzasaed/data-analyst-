import { useEffect, useMemo, useState } from "react";
import { api, ApiError, errorMessage } from "../api";
import { formatNumber, humanize } from "../format";
import type { CleaningOperation, CleaningResponse, ReviewLevel } from "../types";
import { Badge, EmptyState, Notice, Spinner, type Tone } from "./ui";

interface Props {
  jobId: string;
  question?: string | null;
  onSubmitted: () => void;
}

export const REVIEW_TONE: Record<ReviewLevel, Tone> = {
  automatic: "ok",
  recommended: "info",
  needs_review: "warn",
};

export const REVIEW_LABEL: Record<ReviewLevel, string> = {
  automatic: "Automatic",
  recommended: "Recommended",
  needs_review: "Needs review",
};

function initialDecision(op: CleaningOperation): boolean {
  if (op.accepted !== null && op.accepted !== undefined) return op.accepted;
  // Automatic and recommended fixes start approved; anything flagged for review starts rejected.
  return op.review_level !== "needs_review";
}

export function CleaningReview({ jobId, question, onSubmitted }: Props) {
  const [data, setData] = useState<CleaningResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Record<string, boolean>>({});
  const [refine, setRefine] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setLoadError(null);
    api
      .cleaning(jobId)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setDecisions(Object.fromEntries(res.operations.map((op) => [op.operation_id, initialDecision(op)])));
      })
      .catch((e) => {
        if (!cancelled) setLoadError(e instanceof ApiError && e.notReady ? "The cleaning plan isn't ready yet." : errorMessage(e));
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  const ops = data?.operations ?? [];
  const counts = useMemo(() => {
    const approved = ops.filter((op) => decisions[op.operation_id]).length;
    return { approved, rejected: ops.length - approved };
  }, [ops, decisions]);

  const setAll = (value: boolean) =>
    setDecisions((d) => {
      const next = { ...d };
      for (const op of ops) if (op.review_level !== "automatic") next[op.operation_id] = value;
      return next;
    });

  const submit = async () => {
    setSubmitting(true);
    setSubmitError(null);
    const accept = ops.filter((op) => decisions[op.operation_id]).map((op) => op.operation_id);
    const reject = ops.filter((op) => !decisions[op.operation_id]).map((op) => op.operation_id);
    try {
      const res = await api.decideCleaning(jobId, {
        accept,
        reject,
        question: refine.trim() || undefined,
        resume: true,
      });
      // Older backends record the decision without resuming; nudge them along.
      if (!res.status) {
        const job = await api.job(jobId);
        if (job.status === "awaiting_review") await api.resume(jobId);
      }
      onSubmitted();
    } catch (e) {
      setSubmitError(errorMessage(e));
      setSubmitting(false);
    }
  };

  return (
    <section className="review" aria-labelledby="review-title">
      <div className="review__head">
        <div>
          <h2 id="review-title" className="review__title">
            Review the proposed cleaning
          </h2>
          <p className="review__sub">
            The agent has paused. Nothing has been changed yet; the raw file stays untouched either way. Automatic
            fixes are low-risk type, formatting and exact-duplicate changes. Approve or reject the rest, then continue.
          </p>
        </div>
        {data && (
          <div className="review__stats">
            <span>
              <strong className="tnum">{data.summary.total_operations}</strong> proposed
            </span>
            <span>
              <strong className="tnum">{data.summary.needs_review}</strong> need a decision
            </span>
          </div>
        )}
      </div>

      {!data && !loadError && (
        <div className="review__loading">
          <Spinner label="Loading the cleaning plan" />
        </div>
      )}
      {loadError && <Notice tone="warn">{loadError}</Notice>}

      {data && ops.length === 0 && (
        <EmptyState title="No cleaning needed">The profile found nothing to fix. Continue to run the analysis.</EmptyState>
      )}

      {data && ops.length > 0 && (
        <>
          <div className="review__bulk">
            <span className="muted">
              {counts.approved} approved, {counts.rejected} rejected
            </span>
            <span className="review__bulkbtns">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAll(true)}>
                Approve all
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => setAll(false)}>
                Reject all optional
              </button>
            </span>
          </div>
          <ul className="ops">
            {ops.map((op) => {
              const approved = !!decisions[op.operation_id];
              const auto = op.review_level === "automatic";
              return (
                <li key={op.operation_id} className={`op${approved ? " is-approved" : " is-rejected"}`}>
                  <div className="op__main">
                    <div className="op__title">
                      <span className="op__kind">{humanize(op.kind)}</span>
                      {op.column && <code className="op__col">{op.column}</code>}
                    </div>
                    <p className="op__reason">{op.reason}</p>
                    <div className="op__meta">
                      <Badge tone={REVIEW_TONE[op.review_level]}>{REVIEW_LABEL[op.review_level]}</Badge>
                      <span className="tnum">{formatNumber(op.affected_rows)} rows affected</span>
                      <span>{op.reversible ? "Reversible" : "Not reversible"}</span>
                    </div>
                    {op.impact_note && <p className="op__impact">{op.impact_note}</p>}
                  </div>
                  <div className="op__decide" role="group" aria-label={`Decision for ${humanize(op.kind)}`}>
                    <button
                      type="button"
                      className={`seg${approved ? " is-on seg--ok" : ""}`}
                      aria-pressed={approved}
                      onClick={() => setDecisions((d) => ({ ...d, [op.operation_id]: true }))}
                    >
                      Approve
                    </button>
                    <button
                      type="button"
                      className={`seg${!approved ? " is-on seg--bad" : ""}`}
                      aria-pressed={!approved}
                      onClick={() => setDecisions((d) => ({ ...d, [op.operation_id]: false }))}
                    >
                      Reject
                    </button>
                    {auto && <span className="op__autonote">Pre-approved</span>}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}

      {data && (
        <div className="review__foot">
          <div className="field">
            <label className="field__label" htmlFor="refine">
              Refine the question <span className="field__optional">optional</span>
            </label>
            <input
              id="refine"
              className="input"
              placeholder={question || "Leave blank to keep the current question"}
              value={refine}
              onChange={(e) => setRefine(e.target.value)}
            />
          </div>
          {submitError && <Notice tone="bad" title="The decision wasn't saved">{submitError}</Notice>}
          <button type="button" className="btn btn--primary btn--lg" disabled={submitting} onClick={() => void submit()}>
            {submitting ? <Spinner label="Applying" /> : "Apply & continue"}
          </button>
        </div>
      )}
    </section>
  );
}
