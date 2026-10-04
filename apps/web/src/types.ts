// Mirrors packages/contracts/src/insightflow_contracts/*. Fields the backend may add
// later are optional so the UI degrades rather than crashes.

export type JobStatus =
  | "pending"
  | "running"
  | "awaiting_review"
  | "completed"
  | "failed"
  | "rejected"
  | "cancelled";

export type JobStage =
  | "registered"
  | "ingested"
  | "profiled"
  | "intent_captured"
  | "cleaning_proposed"
  | "cleaning_reviewed"
  | "cleaning_applied"
  | "preprocessed"
  | "features_engineered"
  | "features_selected"
  | "planned"
  | "executed"
  | "verified"
  | "reported"
  | "failed"
  | "rejected";

export interface Health {
  status: string;
  environment?: string;
  llm_enabled?: boolean;
  sandbox_runtime?: string;
  inline_execution?: boolean;
  execution_mode?: string;
  state_backend?: string;
}

export interface Tool {
  name: string;
  version: string;
  analysis_type: string;
  summary: string;
  parameters: Record<string, unknown>;
  tags: string[];
}

export interface UploadResponse {
  job_id: string;
  status: JobStatus;
  filename: string;
  file_hash: string;
  available_sheets: string[];
  warnings: string[];
}

export interface JobListItem {
  job_id: string;
  status: JobStatus;
  filename: string | null;
  stage: JobStage;
  progress: number;
  created_at: string;
}

export type StageRecordStatus = "succeeded" | "failed" | "skipped" | "running";

export interface StageRecord {
  stage: JobStage;
  status: StageRecordStatus;
  started_at: string;
  finished_at: string | null;
  attempt: number;
  detail: Record<string, unknown>;
  error: string | null;
  error_code: string | null;
  duration_seconds: number | null;
}

export interface JobDetail {
  job_id: string;
  status: JobStatus;
  stage: JobStage;
  next_stage: JobStage | null;
  progress: number;
  dataset_version: number;
  completed_stages: JobStage[];
  error: string | null;
  filename?: string | null;
  question?: string | null;
  require_review?: boolean;
  created_at?: string;
  updated_at?: string;
  stages?: StageRecord[];
}

// ---------------------------------------------------------------- profile
export type PiiRisk = "none" | "low" | "medium" | "high";
export type Severity = "info" | "low" | "medium" | "high";

export interface QualityIssue {
  kind: string;
  severity: Severity;
  column: string | null;
  description: string;
  affected_rows: number;
  affected_fraction: number;
  evidence: Record<string, unknown>;
  recommended_action: string | null;
}

export interface ColumnProfile {
  name: string;
  original_name: string | null;
  inferred_type: string;
  semantic_role: string;
  null_count: number;
  null_fraction: number;
  unique_count: number;
  distinct_fraction: number;
  sample_values: string[];
  pii_risk: PiiRisk;
  pii_signals: string[];
}

export interface DateSummary {
  min: string | null;
  max: string | null;
  span_days: number | null;
  granularity_guess: string | null;
}

export interface DatasetProfile {
  dataset_id: string;
  source_file_hash: string;
  sheet_name: string | null;
  row_count: number;
  column_count: number;
  estimated_memory_bytes: number;
  columns: ColumnProfile[];
  quality_issues: QualityIssue[];
  duplicate_row_count: number;
  time_coverage: DateSummary | null;
  warnings: { code: string; message: string; stage: string | null }[];
  is_exploratory: boolean;
}

// ---------------------------------------------------------------- cleaning
export type ReviewLevel = "automatic" | "recommended" | "needs_review";

export interface CleaningOperation {
  operation_id: string;
  kind: string;
  column: string | null;
  parameters: Record<string, unknown>;
  reason: string;
  affected_rows: number;
  review_level: ReviewLevel;
  reversible: boolean;
  inverse_note: string | null;
  issue_kind: string | null;
  impact_note: string | null;
  accepted: boolean | null;
}

export interface ChangeLogEntry {
  operation_id: string;
  kind: string;
  column: string | null;
  rows_before: number;
  rows_after: number;
  rows_affected: number;
  null_count_before: number | null;
  null_count_after: number | null;
  reason: string;
  reversible: boolean;
  review_level: ReviewLevel;
  severity: Severity;
  examples_before: string[];
  examples_after: string[];
  inverse_note: string | null;
}

export interface CleaningSummary {
  total_operations: number;
  automatic: number;
  needs_review: number;
  accepted: number;
  rows_touched: number;
}

export interface CleaningResponse {
  summary: CleaningSummary;
  operations: CleaningOperation[];
  applied: ChangeLogEntry[];
  rejected: string[];
}

export interface CleaningDecision {
  accept: string[];
  reject: string[];
  question?: string;
  resume?: boolean;
}

// ---------------------------------------------------------------- findings
export interface Evidence {
  artifact_id: string;
  metric: string;
  value: number | string | boolean | null;
  unit: string | null;
  comparator: string | null;
  source_rows_or_groups: string[];
  dataset_version: number | null;
}

export interface Finding {
  finding_id: string;
  title: string;
  statement: string;
  evidence: Evidence[];
  severity: Severity;
  confidence: "low" | "medium" | "high";
  limitations: string[];
  status: "verified" | "needs_review" | "rejected";
  tags: string[];
  flagged_for_review: boolean;
  unsupported_claims: string[];
}

export type VerificationStatus = "pass" | "pass_with_warning" | "fail" | "skipped";

export interface VerificationCheck {
  check_id: string;
  name: string;
  status: VerificationStatus;
  detail: string;
  finding_id: string | null;
  expected: string | null;
  actual: string | null;
}

export interface VerificationResult {
  verified_finding_ids: string[];
  rejected_finding_ids: string[];
  checks: VerificationCheck[];
  unsupported_claims: string[];
  contradictions: string[];
  summary: string | null;
}

export interface Anomaly {
  anomaly_id?: string;
  method?: string;
  entity_label?: string;
  observed_value?: number | string | null;
  severity?: Severity;
  reason?: string;
  [key: string]: unknown;
}

export interface FindingsResponse {
  findings: Finding[];
  verification: VerificationResult | null;
  anomalies: Anomaly[];
  warnings: string[];
}

// ---------------------------------------------------------------- audit
export interface AuditEvent {
  at: string;
  actor: string;
  action: string;
  stage: string | null;
  outcome: string;
  subject: string | null;
  detail: Record<string, unknown>;
}

export interface AuditResponse {
  job_id: string;
  events: AuditEvent[];
  stages: StageRecord[];
}

// ---------------------------------------------------------------- preprocessing
export type MissingNumeric = "none" | "median" | "mean" | "zero" | "drop_rows";
export type MissingCategorical = "none" | "mode" | "constant";
export type OutlierHandling = "none" | "cap_iqr";
export type Scaling = "none" | "standard" | "minmax" | "robust";

export interface PreprocessingOptions {
  missing_numeric: MissingNumeric;
  missing_categorical: MissingCategorical;
  outliers: OutlierHandling;
  /** Fraction 0..1; null turns the drop off. */
  drop_columns_missing_above: number | null;
  scaling: Scaling;
  encode_categoricals: boolean;
  date_features: boolean;
}

export interface PreprocessingStep {
  step_id: string;
  scope: "analysis" | "export";
  action: string;
  column: string | null;
  detail: string;
  rows_affected: number;
  columns_added: string[];
  columns_removed: string[];
}

export interface PreprocessingExport {
  artifact_id: string;
  rows: number;
  columns: number;
  column_names: string[];
  scaling: string;
  encoded_columns: string[];
  date_feature_columns: string[];
  dropped_columns: { column: string; reason: string }[];
}

export interface PreprocessingResponse {
  options: PreprocessingOptions;
  analysis_changed: boolean;
  rows_before: number;
  rows_after: number;
  columns_before: number;
  columns_after: number;
  steps: PreprocessingStep[];
  missing_before: Record<string, number>;
  missing_after: Record<string, number>;
  export: PreprocessingExport | null;
  warnings: string[];
}

// ---------------------------------------------------------------- features
export interface FeatureOptions {
  enabled: boolean;
  /** null lets the agent choose the target from the question or the data. */
  target_column: string | null;
  /** 1..100 */
  max_features: number;
  /** 0..100 */
  max_engineered: number;
}

export type FeatureTask = "regression" | "classification";

export interface FeatureSpec {
  name: string;
  kind: string;
  inputs: string[];
  rationale: string;
}

export interface EngineeredFeature {
  spec: FeatureSpec;
  source: "rule" | "model";
  non_missing: number;
  mean: number | null;
  std: number | null;
}

export interface RejectedFeature {
  name: string;
  kind: string;
  reason: string;
}

export interface FeatureEngineering {
  skipped_reason: string | null;
  target_column: string | null;
  target_reason: string;
  task: FeatureTask | null;
  source: "deterministic" | "model" | "deterministic_fallback" | "skipped";
  model_used: string | null;
  created: EngineeredFeature[];
  rejected: RejectedFeature[];
  warnings: string[];
}

export interface FeatureScore {
  feature: string;
  origin: "original" | "engineered" | "encoded";
  mutual_information: number | null;
  correlation: number | null;
  importance: number | null;
  combined_rank: number | null;
  selected: boolean;
  status: string;
}

export interface CrossValidation {
  metric: string;
  folds: number;
  all_features_mean: number | null;
  all_features_std: number | null;
  selected_mean: number | null;
  selected_std: number | null;
  all_feature_count: number;
  selected_feature_count: number;
  model: string;
}

export interface FeatureSelection {
  skipped_reason: string | null;
  target_column: string | null;
  task: FeatureTask | null;
  rows_used: number;
  candidate_count: number;
  selected: string[];
  scores: FeatureScore[];
  cross_validation: CrossValidation | null;
  /** The downloadable feature set CSV. */
  artifact_id: string | null;
  methods: string[];
  warnings: string[];
}

export interface FeaturesResponse {
  engineering: FeatureEngineering;
  selection: FeatureSelection;
}
