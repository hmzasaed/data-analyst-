import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, errorMessage, notifyJobsChanged } from "../api";
import { fileLabel, isActive, relativeTime, STAGE_LABEL } from "../format";
import { navigate, useInterval, useNow } from "../hooks";
import type { JobDetail, JobStage } from "../types";
import { AuditTab } from "./AuditTab";
import { CleaningReview } from "./CleaningReview";
import { CleaningTab } from "./CleaningTab";
import { FeaturesTab } from "./FeaturesTab";
import { FindingsTab } from "./FindingsTab";
import { Lifecycle } from "./Lifecycle";
import { PreprocessingTab } from "./PreprocessingTab";
import { ProfileTab } from "./ProfileTab";
import { ReportTab } from "./ReportTab";
import { EmptyState, Icon, Notice, ProgressBar, Spinner, StatusPill } from "./ui";

type TabId = "report" | "findings" | "profile" | "cleaning" | "preprocessing" | "features" | "audit";

const TABS: { id: TabId; label: string; needs: JobStage | null }[] = [
  { id: "report", label: "Report", needs: "reported" },
  { id: "findings", label: "Findings", needs: "verified" },
  { id: "profile", label: "Data profile", needs: "profiled" },
  { id: "cleaning", label: "Cleaning", needs: "cleaning_proposed" },
  { id: "preprocessing", label: "Preprocessing", needs: "preprocessed" },
  { id: "features", label: "Features", needs: "features_selected" },
  { id: "audit", label: "Audit trail", needs: null },
];

const POLL_MS = 1500;

export function JobView({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<JobDetail | null>(null);
  const [loadError, setLoadError] = useState<{ message: string; missing: boolean } | null>(null);
  const [tab, setTab] = useState<TabId | null>(null);
  const [action, setAction] = useState<null | "cancel" | "resume" | "delete">(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [forcePoll, setForcePoll] = useState(false);
  const lastStatus = useRef<string | null>(null);
  const now = useNow(15000);

  const load = useCallback(async () => {
    try {
      const next = await api.job(jobId);
      setJob(next);
      setLoadError(null);
      if (lastStatus.current !== null && lastStatus.current !== next.status) notifyJobsChanged();
      lastStatus.current = next.status;
      if (forcePoll && next.status !== "awaiting_review") setForcePoll(false);
    } catch (e) {
      const missing = e instanceof ApiError && e.status === 404;
      setLoadError({ message: errorMessage(e), missing });
    }
  }, [jobId, forcePoll]);

  useEffect(() => {
    setJob(null);
    setLoadError(null);
    setTab(null);
    setConfirmDelete(false);
    setActionError(null);
    lastStatus.current = null;
    void load();
  }, [jobId]);

  const polling = !loadError?.missing && (job === null || isActive(job.status) || forcePoll);
  useInterval(() => void load(), POLL_MS, polling);

  if (loadError?.missing) {
    return (
      <EmptyState title="This analysis doesn't exist in your workspace">
        <p>It may have been deleted, or it was created under a different workspace id.</p>
        <button type="button" className="btn btn--primary" onClick={() => navigate("/new")}>
          Start a new analysis
        </button>
      </EmptyState>
    );
  }

  if (!job) {
    return loadError ? (
      <Notice
        tone="bad"
        title="The analysis couldn't be loaded"
        action={
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => void load()}>
            Retry
          </button>
        }
      >
        {loadError.message}
      </Notice>
    ) : (
      <div className="loading-block">
        <Spinner label="Loading analysis" />
      </div>
    );
  }

  const done = new Set<string>(job.completed_stages);
  const available = TABS.filter((t) => t.needs === null || done.has(t.needs));
  const preferred = available[0]?.id ?? "audit";
  const current: TabId = tab && available.some((t) => t.id === tab) ? tab : preferred;
  const refreshKey = `${job.completed_stages.length}:${job.status}:${job.dataset_version}`;
  const active = isActive(job.status);

  const run = async (kind: "cancel" | "resume" | "delete") => {
    setAction(kind);
    setActionError(null);
    try {
      if (kind === "cancel") await api.cancel(jobId);
      if (kind === "resume") {
        setForcePoll(true);
        await api.resume(jobId);
      }
      if (kind === "delete") {
        await api.remove(jobId);
        notifyJobsChanged();
        navigate("/");
        return;
      }
      await load();
      notifyJobsChanged();
    } catch (e) {
      setActionError(errorMessage(e));
      setForcePoll(false);
    } finally {
      setAction(null);
      setConfirmDelete(false);
    }
  };

  const onReviewed = () => {
    setForcePoll(true);
    setJob((j) => (j ? { ...j, status: "running" } : j));
    notifyJobsChanged();
    void load();
  };

  const stageText =
    job.status === "awaiting_review"
      ? "Waiting for your cleaning review"
      : active && job.next_stage
        ? `Working on: ${STAGE_LABEL[job.next_stage] ?? job.next_stage}`
        : job.status === "completed"
          ? "All stages finished"
          : job.status === "failed"
            ? `Stopped at ${STAGE_LABEL[job.next_stage ?? job.stage] ?? job.stage}`
            : `Last stage: ${STAGE_LABEL[job.stage] ?? job.stage}`;

  return (
    <div className="jobview">
      <header className="jobhead">
        <div className="jobhead__top">
          <div className="jobhead__titles">
            <h1 className="page-title jobhead__file">{fileLabel(job.filename)}</h1>
            {job.question ? (
              <p className="jobhead__question">{job.question}</p>
            ) : (
              <p className="jobhead__question is-muted">Exploratory analysis, no question given</p>
            )}
          </div>
          <div className="jobhead__actions">
            {active && (
              <button
                type="button"
                className="btn btn--ghost"
                disabled={action !== null}
                onClick={() => void run("cancel")}
              >
                <Icon name="stop" size={14} /> {action === "cancel" ? "Cancelling" : "Cancel"}
              </button>
            )}
            {(job.status === "failed" || job.status === "cancelled") && (
              <button
                type="button"
                className="btn btn--primary"
                disabled={action !== null}
                onClick={() => void run("resume")}
              >
                <Icon name="play" size={14} /> {action === "resume" ? "Resuming" : "Resume"}
              </button>
            )}
            {!confirmDelete ? (
              <button
                type="button"
                className="btn btn--ghost btn--danger-text"
                disabled={action !== null}
                onClick={() => setConfirmDelete(true)}
              >
                <Icon name="trash" size={14} /> Delete
              </button>
            ) : (
              <span className="confirm" role="group" aria-label="Confirm delete">
                <span className="confirm__text">Delete this analysis and its files?</span>
                <button
                  type="button"
                  className="btn btn--danger btn--sm"
                  disabled={action !== null}
                  onClick={() => void run("delete")}
                >
                  {action === "delete" ? "Deleting" : "Delete"}
                </button>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setConfirmDelete(false)}>
                  Keep
                </button>
              </span>
            )}
          </div>
        </div>

        <div className="jobhead__status">
          <StatusPill status={job.status} />
          <span className="jobhead__stage">{stageText}</span>
          <span className="jobhead__meta">
            {job.created_at && <span>Started {relativeTime(job.created_at, now)}</span>}
            {job.dataset_version > 0 && <span>Dataset v{job.dataset_version}</span>}
            {job.require_review && <span>Review on</span>}
          </span>
          <span className="jobhead__pct tnum">{Math.round(job.progress * 100)}%</span>
        </div>
        <ProgressBar value={job.progress} status={job.status} />
      </header>

      {actionError && <Notice tone="bad" title="That didn't work">{actionError}</Notice>}
      {job.status === "failed" && job.error && (
        <Notice tone="bad" title="The analysis stopped">
          {job.error} Fix the cause if it's in the file, or resume to retry from the failed stage.
        </Notice>
      )}
      {job.status === "cancelled" && (
        <Notice tone="warn" title="This analysis was cancelled">
          Resume to continue from where it stopped.
        </Notice>
      )}
      {loadError && !loadError.missing && (
        <Notice tone="warn" title="Lost contact with the API">
          {loadError.message} Retrying automatically.
        </Notice>
      )}

      {job.status === "awaiting_review" && (
        <CleaningReview jobId={jobId} question={job.question} onSubmitted={onReviewed} />
      )}

      <Lifecycle job={job} />

      <section className="results">
        <div className="tabs" role="tablist" aria-label="Results">
          {available.map((t) => (
            <button
              key={t.id}
              type="button"
              role="tab"
              id={`tab-${t.id}`}
              aria-selected={current === t.id}
              aria-controls={`panel-${t.id}`}
              className={`tab${current === t.id ? " is-current" : ""}`}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
          {available.length < TABS.length && (
            <span className="tabs__pending">More appears as stages finish</span>
          )}
        </div>
        <div className="tabpanel" role="tabpanel" id={`panel-${current}`} aria-labelledby={`tab-${current}`}>
          {current === "report" && <ReportTab jobId={jobId} refreshKey={refreshKey} filename={job.filename} />}
          {current === "findings" && <FindingsTab jobId={jobId} refreshKey={refreshKey} />}
          {current === "profile" && <ProfileTab jobId={jobId} refreshKey={refreshKey} />}
          {current === "cleaning" && <CleaningTab jobId={jobId} refreshKey={refreshKey} />}
          {current === "preprocessing" && (
            <PreprocessingTab jobId={jobId} refreshKey={refreshKey} filename={job.filename} />
          )}
          {current === "features" && <FeaturesTab jobId={jobId} refreshKey={refreshKey} filename={job.filename} />}
          {current === "audit" && <AuditTab jobId={jobId} refreshKey={refreshKey} />}
        </div>
      </section>
    </div>
  );
}
