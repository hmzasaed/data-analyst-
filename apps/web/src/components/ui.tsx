import type { ReactNode } from "react";
import { STATUS_LABEL } from "../format";
import type { Resource } from "../hooks";
import type { JobStatus } from "../types";

/** Small shared primitives: status pill, badges, notices, empty states, icons. */

export function StatusPill({ status, compact = false }: { status: JobStatus; compact?: boolean }) {
  return (
    <span className={`pill pill--${status}${compact ? " pill--compact" : ""}`}>
      <span className="pill__dot" aria-hidden="true" />
      {STATUS_LABEL[status] ?? status}
    </span>
  );
}

export type Tone = "neutral" | "ok" | "warn" | "bad" | "info" | "accent";

export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function Notice({
  tone = "info",
  title,
  children,
  action,
}: {
  tone?: "info" | "warn" | "bad" | "ok";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`notice notice--${tone}`} role={tone === "bad" ? "alert" : "status"}>
      <div className="notice__body">
        {title && <p className="notice__title">{title}</p>}
        {children && <div className="notice__text">{children}</div>}
      </div>
      {action && <div className="notice__action">{action}</div>}
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <p className="empty__title">{title}</p>
      {children && <div className="empty__text">{children}</div>}
    </div>
  );
}

/** Loading / error / not-yet states for a job sub-resource; renders children once data exists. */
export function ResourceView<T>({
  resource,
  notReadyTitle,
  notReadyText,
  children,
}: {
  resource: Resource<T>;
  notReadyTitle: string;
  notReadyText?: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (resource.data) return <>{children(resource.data)}</>;
  if (resource.error)
    return (
      <Notice
        tone="bad"
        title="This couldn't be loaded"
        action={
          <button type="button" className="btn btn--ghost btn--sm" onClick={resource.reload}>
            Retry
          </button>
        }
      >
        {resource.error}
      </Notice>
    );
  if (resource.notReady) return <EmptyState title={notReadyTitle}>{notReadyText}</EmptyState>;
  return (
    <div className="loading-block">
      <Spinner label="Loading" />
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner" role="status">
      <span className="spinner__ring" aria-hidden="true" />
      {label && <span className="spinner__label">{label}</span>}
    </span>
  );
}

export function ProgressBar({ value, status }: { value: number; status?: JobStatus }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div
      className={`progress${status ? ` progress--${status}` : ""}`}
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className="progress__fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

type IconName =
  | "plus"
  | "upload"
  | "menu"
  | "close"
  | "check"
  | "x"
  | "alert"
  | "download"
  | "user"
  | "refresh"
  | "trash"
  | "stop"
  | "play"
  | "file"
  | "clock";

const PATHS: Record<IconName, ReactNode> = {
  plus: <path d="M12 5v14M5 12h14" />,
  upload: (
    <>
      <path d="M12 16V4M7 9l5-5 5 5" />
      <path d="M4 16v3a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-3" />
    </>
  ),
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  x: <path d="M7 7l10 10M17 7L7 17" />,
  alert: (
    <>
      <path d="M12 8v5M12 16.5v.5" />
      <circle cx="12" cy="12" r="9" />
    </>
  ),
  download: (
    <>
      <path d="M12 4v12M7 11l5 5 5-5" />
      <path d="M4 20h16" />
    </>
  ),
  user: (
    <>
      <circle cx="12" cy="8.5" r="3.5" />
      <path d="M5 20c1.2-3.6 3.9-5.5 7-5.5s5.8 1.9 7 5.5" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.3-5.6" />
      <path d="M20 4v4.5h-4.5" />
    </>
  ),
  trash: (
    <>
      <path d="M4 7h16M10 7V4.5h4V7" />
      <path d="M6.5 7l1 13h9l1-13" />
    </>
  ),
  stop: <rect x="6.5" y="6.5" width="11" height="11" rx="1.5" />,
  play: <path d="M8 5.5v13l10-6.5z" />,
  file: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v4h4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
};

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {PATHS[name]}
    </svg>
  );
}
