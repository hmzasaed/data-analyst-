import type {
  AuditResponse,
  CleaningDecision,
  CleaningResponse,
  DatasetProfile,
  FeatureOptions,
  FeaturesResponse,
  FindingsResponse,
  Health,
  JobDetail,
  JobListItem,
  JobStage,
  PreprocessingOptions,
  PreprocessingResponse,
  Tool,
  UploadResponse,
} from "./types";

const PREFIX = "/api/v1";
const OWNER_KEY = "insightflow.owner_id";
export const OWNER_EVENT = "insightflow:owner-changed";
export const JOBS_EVENT = "insightflow:jobs-changed";

// ------------------------------------------------------------------ owner id
let memoryOwner: string | null = null;

function randomId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}

export function newOwnerId(): string {
  return `user-${randomId()}`;
}

export function getOwnerId(): string {
  if (memoryOwner) return memoryOwner;
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(OWNER_KEY);
  } catch {
    stored = null;
  }
  if (!stored) {
    stored = newOwnerId();
    try {
      window.localStorage.setItem(OWNER_KEY, stored);
    } catch {
      /* storage unavailable: keep it in memory for this tab */
    }
  }
  memoryOwner = stored;
  return stored;
}

export function setOwnerId(value: string): void {
  const next = value.trim();
  if (!next) return;
  memoryOwner = next;
  try {
    window.localStorage.setItem(OWNER_KEY, next);
  } catch {
    /* ignore */
  }
  window.dispatchEvent(new CustomEvent(OWNER_EVENT, { detail: next }));
}

export function notifyJobsChanged(): void {
  window.dispatchEvent(new CustomEvent(JOBS_EVENT));
}

// ------------------------------------------------------------------ errors
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.map((v) => String(v)) : [];
}

export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly availableSheets: string[];
  readonly suggestions: string[];

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    const obj = isRecord(detail) ? detail : {};
    this.availableSheets = stringList(obj.available_sheets);
    this.suggestions = stringList(obj.suggestions);
  }

  /** The job exists but this part of the pipeline has not produced output yet. */
  get notReady(): boolean {
    return this.status === 409;
  }
}

function messageFromDetail(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail) return detail;
  if (isRecord(detail) && typeof detail.message === "string") return detail.message;
  if (Array.isArray(detail) && detail.length) {
    // FastAPI validation errors: [{loc, msg, type}]
    return detail
      .map((d) => {
        if (!isRecord(d)) return String(d);
        const loc = Array.isArray(d.loc) ? d.loc.slice(1).join(".") : "";
        return `${loc ? `${loc}: ` : ""}${String(d.msg ?? "")}`;
      })
      .join("; ");
  }
  return fallback;
}

// ------------------------------------------------------------------ core
type Parse = "json" | "text" | "blob";

async function request<T>(path: string, init: RequestInit = {}, parse: Parse = "json"): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Owner-Id", getOwnerId());
  if (parse === "json" && !headers.has("Accept")) headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "Can't reach the InsightFlow API. Check that the backend is running on port 8000.");
  }

  if (!response.ok) {
    let detail: unknown = undefined;
    const text = await response.text().catch(() => "");
    try {
      const body: unknown = text ? JSON.parse(text) : undefined;
      detail = isRecord(body) && "detail" in body ? body.detail : body;
    } catch {
      detail = text || undefined;
    }
    const fallback = `${response.status} ${response.statusText || "Request failed"}`.trim();
    throw new ApiError(response.status, messageFromDetail(detail, fallback), detail);
  }

  if (parse === "text") return (await response.text()) as T;
  if (parse === "blob") return (await response.blob()) as T;
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const jobPath = (id: string) => `${PREFIX}/jobs/${encodeURIComponent(id)}`;

// ------------------------------------------------------------------ endpoints
export interface UploadInput {
  file: File;
  question?: string;
  businessContext?: string;
  sheetName?: string;
  consentThirdPartyLlm: boolean;
  requireReview: boolean;
  preprocessing?: PreprocessingOptions;
  features?: FeatureOptions;
}

export type ReportFormat = "html" | "markdown" | "json";

export const api = {
  health: (signal?: AbortSignal) => request<Health>("/health", { signal }),

  tools: () => request<{ count: number; tools: Tool[] }>(`${PREFIX}/tools`),

  upload(input: UploadInput): Promise<UploadResponse> {
    const form = new FormData();
    form.append("file", input.file);
    if (input.question?.trim()) form.append("question", input.question.trim());
    if (input.businessContext?.trim()) form.append("business_context", input.businessContext.trim());
    if (input.sheetName) form.append("sheet_name", input.sheetName);
    form.append("consent_third_party_llm", String(input.consentThirdPartyLlm));
    form.append("require_review", String(input.requireReview));
    if (input.preprocessing) form.append("preprocessing", JSON.stringify(input.preprocessing));
    if (input.features) form.append("features", JSON.stringify(input.features));
    return request<UploadResponse>(`${PREFIX}/jobs`, { method: "POST", body: form });
  },

  listJobs: (signal?: AbortSignal) => request<JobListItem[]>(`${PREFIX}/jobs?limit=100`, { signal }),

  job: (id: string, signal?: AbortSignal) => request<JobDetail>(jobPath(id), { signal }),

  profile: (id: string) => request<DatasetProfile>(`${jobPath(id)}/profile`),

  cleaning: (id: string) => request<CleaningResponse>(`${jobPath(id)}/cleaning`),

  decideCleaning: (id: string, decision: CleaningDecision) =>
    request<{ approved: string[]; operation_count: number; status?: string }>(
      `${jobPath(id)}/cleaning/decision`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume: true, ...decision }),
      },
    ),

  resume: (id: string, until?: JobStage) =>
    request<JobDetail>(`${jobPath(id)}/resume${until ? `?until=${encodeURIComponent(until)}` : ""}`, {
      method: "POST",
    }),

  preprocessing: (id: string) => request<PreprocessingResponse>(`${jobPath(id)}/preprocessing`),

  features: (id: string) => request<FeaturesResponse>(`${jobPath(id)}/features`),

  findings: (id: string) => request<FindingsResponse>(`${jobPath(id)}/findings`),

  audit: (id: string) => request<AuditResponse>(`${jobPath(id)}/audit`),

  reportHtml: (id: string) =>
    request<string>(`${jobPath(id)}/report?format=html&embed=true`, { headers: { Accept: "text/html" } }, "text"),

  reportBlob: (id: string, format: ReportFormat) =>
    request<Blob>(`${jobPath(id)}/report?format=${format}${format === "html" ? "&embed=true" : ""}`, {}, "blob"),

  /** Artifacts are owner-checked, so they can't be linked directly: fetch and wrap. */
  async artifactUrl(id: string, artifactId: string): Promise<string> {
    const blob = await request<Blob>(`${jobPath(id)}/artifacts/${encodeURIComponent(artifactId)}`, {}, "blob");
    return URL.createObjectURL(blob);
  },

  /** Owner-checked artifact as a blob (sent with X-Owner-Id). */
  artifactBlob: (id: string, artifactId: string) =>
    request<Blob>(`${jobPath(id)}/artifacts/${encodeURIComponent(artifactId)}`, {}, "blob"),

  cancel: (id: string) => request<{ cancelled: boolean }>(`${jobPath(id)}/cancel`, { method: "POST" }),

  remove: (id: string) => request<{ deleted: boolean }>(jobPath(id), { method: "DELETE" }),
};

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

/** Save a blob through a temporary object URL. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
