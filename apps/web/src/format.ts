import type { JobStage, JobStatus } from "./types";

export const STAGE_ORDER: JobStage[] = [
  "registered",
  "ingested",
  "profiled",
  "intent_captured",
  "cleaning_proposed",
  "cleaning_reviewed",
  "cleaning_applied",
  "preprocessed",
  "features_engineered",
  "features_selected",
  "planned",
  "executed",
  "verified",
  "reported",
];

export const STAGE_LABEL: Record<string, string> = {
  registered: "Registered",
  ingested: "Ingested",
  profiled: "Profiled",
  intent_captured: "Question captured",
  cleaning_proposed: "Cleaning proposed",
  cleaning_reviewed: "Cleaning reviewed",
  cleaning_applied: "Cleaning applied",
  preprocessed: "Data preprocessed",
  features_engineered: "Features engineered",
  features_selected: "Features selected",
  planned: "Analysis planned",
  executed: "Tools executed",
  verified: "Findings verified",
  reported: "Report written",
  failed: "Failed",
  rejected: "Rejected",
};

export const STAGE_HINT: Record<string, string> = {
  registered: "File received and hashed",
  ingested: "Encoding, delimiter and headers normalised",
  profiled: "Types, roles, PII and quality issues detected",
  intent_captured: "Question mapped to an objective",
  cleaning_proposed: "Transforms proposed with review levels",
  cleaning_reviewed: "Approved operations recorded",
  cleaning_applied: "New dataset version written",
  preprocessed: "Prepare analysis-ready and model-ready data",
  features_engineered: "Agent proposes and validates new features",
  features_selected: "Agent ranks features and keeps the strongest",
  planned: "Question mapped onto registered tools",
  executed: "Tools run with validated parameters",
  verified: "Evidence, versions and wording checked",
  reported: "HTML, Markdown and JSON rendered",
};

export interface Phase {
  id: string;
  label: string;
  stages: JobStage[];
}

export const PHASES: Phase[] = [
  { id: "ingest", label: "Ingest & profile", stages: ["registered", "ingested", "profiled", "intent_captured"] },
  { id: "clean", label: "Clean", stages: ["cleaning_proposed", "cleaning_reviewed", "cleaning_applied"] },
  { id: "preprocess", label: "Preprocess", stages: ["preprocessed"] },
  { id: "features", label: "Features", stages: ["features_engineered", "features_selected"] },
  { id: "plan", label: "Plan", stages: ["planned"] },
  { id: "execute", label: "Execute", stages: ["executed"] },
  { id: "verify", label: "Verify", stages: ["verified"] },
  { id: "report", label: "Report", stages: ["reported"] },
];

export const STATUS_LABEL: Record<JobStatus, string> = {
  pending: "Queued",
  running: "Running",
  awaiting_review: "Needs review",
  completed: "Completed",
  failed: "Failed",
  rejected: "Rejected",
  cancelled: "Cancelled",
};

export const isActive = (s: JobStatus | undefined): boolean => s === "pending" || s === "running";
export const isTerminal = (s: JobStatus | undefined): boolean =>
  s === "completed" || s === "failed" || s === "rejected" || s === "cancelled";

export function humanize(value: string): string {
  const text = value.replace(/[_-]+/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  return `${value >= 10 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

export function formatPercent(fraction: number, digits = 1): string {
  if (!Number.isFinite(fraction)) return "–";
  const pct = fraction * 100;
  if (pct > 0 && pct < 0.1) return "<0.1%";
  if (pct === 0 || pct === 100) return `${pct}%`;
  return `${pct.toFixed(digits)}%`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "";
  if (seconds < 1) return `${Math.max(1, Math.round(seconds * 1000))} ms`;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}m ${s}s`;
}

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null;
  // Naive timestamps from the backend are UTC.
  const hasZone = /[zZ]$|[+-]\d\d:?\d\d$/.test(value);
  const date = new Date(hasZone ? value : `${value}Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function relativeTime(value: string | null | undefined, now = Date.now()): string {
  const date = parseDate(value);
  if (!date) return "";
  const diff = Math.round((now - date.getTime()) / 1000);
  if (diff < 45) return "just now";
  const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  if (diff < 3600) return rtf.format(-Math.round(diff / 60), "minute");
  if (diff < 86400) return rtf.format(-Math.round(diff / 3600), "hour");
  if (diff < 86400 * 30) return rtf.format(-Math.round(diff / 86400), "day");
  return date.toLocaleDateString();
}

export function formatTimestamp(value: string | null | undefined): string {
  const date = parseDate(value);
  if (!date) return value ?? "";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/** Render an arbitrary detail value in a few characters. */
export function compactValue(value: unknown): string {
  if (value == null) return "–";
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "number")
    return Number.isInteger(value) ? formatNumber(value) : String(Math.round(value * 1000) / 1000);
  if (typeof value === "string") return value.length > 60 ? `${value.slice(0, 57)}…` : value;
  if (Array.isArray(value)) {
    if (value.length === 0) return "none";
    if (value.length <= 3 && value.every((v) => typeof v === "string" || typeof v === "number"))
      return value.join(", ");
    return `${value.length} items`;
  }
  if (typeof value === "object") return `${Object.keys(value as object).length} fields`;
  return String(value);
}

export function fileLabel(name: string | null | undefined): string {
  return name || "Untitled dataset";
}
