import { useCallback, useEffect, useState } from "react";
import { api, errorMessage, JOBS_EVENT, OWNER_EVENT } from "../api";
import { fileLabel, isActive, relativeTime } from "../format";
import { navigate, useInterval, useNow, useWindowEvent, type Route } from "../hooks";
import type { JobListItem } from "../types";
import { Icon, StatusPill } from "./ui";

interface Props {
  route: Route;
  onNavigate: () => void;
}

export function Sidebar({ route, onNavigate }: Props) {
  const [jobs, setJobs] = useState<JobListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const now = useNow(20000);

  const load = useCallback(async () => {
    try {
      const list = await api.listJobs();
      setJobs(list);
      setError(null);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const anyActive = jobs?.some((j) => isActive(j.status) ) ?? false;
  useInterval(() => void load(), anyActive ? 3000 : 8000);
  useWindowEvent(JOBS_EVENT, () => void load());
  useWindowEvent(OWNER_EVENT, () => {
    setJobs(null);
    void load();
  });

  const activeId = route.name === "job" ? route.id : null;

  const go = (path: string) => {
    navigate(path);
    onNavigate();
  };

  return (
    <nav className="sidebar" aria-label="Analyses">
      <a className="brand" href="#/" onClick={() => onNavigate()}>
        <span className="brand__mark" aria-hidden="true">
          <svg viewBox="0 0 32 32" width="22" height="22">
            <path
              d="M5 21h5l3.5-10 5 14 3.5-8H27"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </span>
        <span className="brand__name">InsightFlow</span>
      </a>

      <button
        type="button"
        className={`btn btn--primary btn--block${route.name === "new" ? " is-current" : ""}`}
        onClick={() => go("/new")}
      >
        <Icon name="plus" /> New analysis
      </button>

      <div className="sidebar__heading">
        <span>Analyses</span>
        {jobs && jobs.length > 0 && <span className="sidebar__count">{jobs.length}</span>}
      </div>

      <div className="joblist">
        {jobs === null && !error && <div className="joblist__skeleton" aria-hidden="true" />}
        {error && (
          <div className="joblist__error">
            <p>{error}</p>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => void load()}>
              <Icon name="refresh" size={14} /> Retry
            </button>
          </div>
        )}
        {jobs && jobs.length === 0 && (
          <p className="joblist__empty">Nothing here yet. Upload a CSV or Excel file to start your first analysis.</p>
        )}
        {jobs?.map((job) => (
          <a
            key={job.job_id}
            href={`#/jobs/${encodeURIComponent(job.job_id)}`}
            className={`jobitem${job.job_id === activeId ? " is-current" : ""}`}
            aria-current={job.job_id === activeId ? "page" : undefined}
            onClick={() => onNavigate()}
          >
            <span className="jobitem__row">
              <span className="jobitem__name" title={fileLabel(job.filename)}>
                {fileLabel(job.filename)}
              </span>
              <StatusPill status={job.status} compact />
            </span>
            <span className="jobitem__row jobitem__meta">
              <span>{relativeTime(job.created_at, now)}</span>
              <span className="jobitem__track" aria-hidden="true">
                <span
                  className={`jobitem__fill jobitem__fill--${job.status}`}
                  style={{ width: `${Math.round(job.progress * 100)}%` }}
                />
              </span>
              <span className="tnum">{Math.round(job.progress * 100)}%</span>
            </span>
          </a>
        ))}
      </div>
    </nav>
  );
}
