import type { FeatureOptions } from "./types";

/** Feature engineering & selection defaults and helpers, shared by the upload form and the results tab. */

export const DEFAULT_FEATURES: FeatureOptions = {
  enabled: true,
  target_column: null,
  max_features: 15,
  max_engineered: 20,
};

export const MAX_FEATURES_RANGE = { min: 1, max: 100 } as const;
export const MAX_ENGINEERED_RANGE = { min: 0, max: 100 } as const;

export function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function isDefaultFeatures(o: FeatureOptions): boolean {
  return (Object.keys(DEFAULT_FEATURES) as (keyof FeatureOptions)[]).every((k) => o[k] === DEFAULT_FEATURES[k]);
}

/** The options as sent to the API: blank target becomes null, counts are clamped. */
export function normalizeFeatures(o: FeatureOptions): FeatureOptions {
  const target = o.target_column?.trim() ?? "";
  return {
    enabled: o.enabled,
    target_column: target ? target : null,
    max_features: clampInt(o.max_features, MAX_FEATURES_RANGE.min, MAX_FEATURES_RANGE.max),
    max_engineered: clampInt(o.max_engineered, MAX_ENGINEERED_RANGE.min, MAX_ENGINEERED_RANGE.max),
  };
}

/** One line describing the choices, for a collapsed panel. */
export function summarizeFeatures(o: FeatureOptions): string {
  if (!o.enabled) return "Off. The agent skips feature engineering and selection.";
  const target = o.target_column?.trim();
  const head = target ? `Target ${target}` : "Target detected automatically";
  const engineered =
    o.max_engineered === 0 ? "no engineered features" : `up to ${o.max_engineered} engineered features`;
  return `${head}, ${engineered}, keep the best ${o.max_features}.`;
}

/** Metrics where a larger cross-validation score means a better model. */
const HIGHER_IS_BETTER = new Set(["r2", "accuracy", "f1", "f1_macro", "f1_weighted", "balanced_accuracy", "roc_auc"]);

export function higherIsBetter(metric: string): boolean {
  return HIGHER_IS_BETTER.has(metric.toLowerCase());
}

/** Fixed three decimals; null and non-finite render as an en dash. */
export function fmt3(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "–";
  return value.toFixed(3);
}
