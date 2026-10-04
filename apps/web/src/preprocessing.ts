import type { MissingCategorical, MissingNumeric, OutlierHandling, PreprocessingOptions, Scaling } from "./types";

/** Preprocessing option labels and defaults, shared by the upload form and the results tab. */

export const DEFAULT_PREPROCESSING: PreprocessingOptions = {
  missing_numeric: "none",
  missing_categorical: "none",
  outliers: "none",
  drop_columns_missing_above: 0.5,
  scaling: "standard",
  encode_categoricals: true,
  date_features: true,
};

export interface Choice<T extends string> {
  value: T;
  label: string;
}

export const MISSING_NUMERIC: Choice<MissingNumeric>[] = [
  { value: "none", label: "Leave" },
  { value: "median", label: "Median" },
  { value: "mean", label: "Mean" },
  { value: "zero", label: "Zero" },
  { value: "drop_rows", label: "Drop rows" },
];

export const MISSING_CATEGORICAL: Choice<MissingCategorical>[] = [
  { value: "none", label: "Leave" },
  { value: "mode", label: "Most common" },
  { value: "constant", label: "\"Missing\" label" },
];

export const OUTLIERS: Choice<OutlierHandling>[] = [
  { value: "none", label: "Leave" },
  { value: "cap_iqr", label: "Cap at 1.5 × IQR" },
];

export const SCALING: Choice<Scaling>[] = [
  { value: "none", label: "None" },
  { value: "standard", label: "Standard" },
  { value: "minmax", label: "Min–max" },
  { value: "robust", label: "Robust" },
];

export function choiceLabel<T extends string>(choices: Choice<T>[], value: string): string {
  return choices.find((c) => c.value === value)?.label ?? value;
}

export function thresholdLabel(value: number | null | undefined): string {
  return value == null ? "off" : `${Math.round(value * 100)}%`;
}

export function isDefaultPreprocessing(o: PreprocessingOptions): boolean {
  return (Object.keys(DEFAULT_PREPROCESSING) as (keyof PreprocessingOptions)[]).every(
    (k) => o[k] === DEFAULT_PREPROCESSING[k],
  );
}

/** One line describing the choices, for a collapsed panel. */
export function summarizePreprocessing(o: PreprocessingOptions): string {
  const analysed: string[] = [];
  if (o.missing_numeric !== "none")
    analysed.push(
      o.missing_numeric === "drop_rows"
        ? "drop rows with missing numbers"
        : `fill missing numbers with ${choiceLabel(MISSING_NUMERIC, o.missing_numeric).toLowerCase()}`,
    );
  if (o.missing_categorical !== "none")
    analysed.push(
      o.missing_categorical === "mode" ? "fill missing categories with most common" : "label missing categories",
    );
  if (o.outliers !== "none") analysed.push("cap outliers");

  const exportParts: string[] = [
    o.scaling === "none" ? "no scaling" : `${choiceLabel(SCALING, o.scaling).toLowerCase()} scaling`,
    o.encode_categoricals ? "encode categories" : "no encoding",
    o.date_features ? "date features" : "no date features",
    `drop columns over ${thresholdLabel(o.drop_columns_missing_above)} missing`,
  ];
  if (o.drop_columns_missing_above == null) exportParts[3] = "keep sparse columns";

  const head = analysed.length ? `Analysed data: ${analysed.join(", ")}` : "Analysed data unchanged";
  return `${head}. Export: ${exportParts.join(", ")}.`;
}
