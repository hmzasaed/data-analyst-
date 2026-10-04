import { useId, useState, type ReactNode } from "react";
import {
  DEFAULT_PREPROCESSING,
  isDefaultPreprocessing,
  MISSING_CATEGORICAL,
  MISSING_NUMERIC,
  OUTLIERS,
  SCALING,
  summarizePreprocessing,
  thresholdLabel,
  type Choice,
} from "../preprocessing";
import type { PreprocessingOptions } from "../types";

/** A row of mutually exclusive buttons (radio semantics). */
export function Segmented<T extends string>({
  label,
  choices,
  value,
  onChange,
}: {
  label: string;
  choices: Choice<T>[];
  value: T;
  onChange: (next: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup" aria-label={label}>
      {choices.map((c) => (
        <button
          key={c.value}
          type="button"
          role="radio"
          aria-checked={value === c.value}
          className={`seg${value === c.value ? " is-on" : ""}`}
          onClick={() => onChange(c.value)}
        >
          {c.label}
        </button>
      ))}
    </div>
  );
}

function Control({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="prep__control">
      <div className="prep__controlhead">
        <span className="prep__controllabel">{label}</span>
        {hint && <span className="prep__controlhint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

export function PreprocessingPanel({
  value,
  onChange,
}: {
  value: PreprocessingOptions;
  onChange: (next: PreprocessingOptions) => void;
}) {
  const [open, setOpen] = useState(false);
  const [lastThreshold, setLastThreshold] = useState(DEFAULT_PREPROCESSING.drop_columns_missing_above ?? 0.5);
  const bodyId = useId();
  const sliderId = useId();
  const set = <K extends keyof PreprocessingOptions>(key: K, next: PreprocessingOptions[K]) =>
    onChange({ ...value, [key]: next });

  const thresholdOn = value.drop_columns_missing_above != null;
  const custom = !isDefaultPreprocessing(value);

  return (
    <section className={`section prep${open ? " is-open" : ""}`}>
      <button
        type="button"
        className="prep__toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="prep__chevron" aria-hidden="true" />
        <span className="prep__titles">
          <span className="prep__title">
            Preprocessing {custom && <span className="field__optional">customised</span>}
          </span>
          {!open && <span className="prep__summary">{summarizePreprocessing(value)}</span>}
        </span>
      </button>

      {open && (
        <div className="prep__body" id={bodyId}>
          <div className="prep__group">
            <div className="prep__grouphead">
              <h3 className="prep__grouptitle">Analysed data</h3>
              <p className="prep__note">These change the figures in the report; every change is logged.</p>
            </div>
            <Control label="Missing numbers">
              <Segmented
                label="Missing numbers"
                choices={MISSING_NUMERIC}
                value={value.missing_numeric}
                onChange={(v) => set("missing_numeric", v)}
              />
            </Control>
            <Control label="Missing categories">
              <Segmented
                label="Missing categories"
                choices={MISSING_CATEGORICAL}
                value={value.missing_categorical}
                onChange={(v) => set("missing_categorical", v)}
              />
            </Control>
            <Control label="Outliers">
              <Segmented label="Outliers" choices={OUTLIERS} value={value.outliers} onChange={(v) => set("outliers", v)} />
            </Control>
          </div>

          <div className="prep__group">
            <div className="prep__grouphead">
              <h3 className="prep__grouptitle">Model-ready export</h3>
              <p className="prep__note">Applied only to the downloadable dataset; the report's figures are unaffected.</p>
            </div>
            <Control label="Drop sparse columns" hint="Columns with more missing values than this are left out.">
              <div className="prep__threshold">
                <label className="check">
                  <input
                    type="checkbox"
                    checked={thresholdOn}
                    onChange={(e) => set("drop_columns_missing_above", e.target.checked ? lastThreshold : null)}
                  />
                  <span className="toggle__label prep__checklabel">{thresholdOn ? "On" : "Off"}</span>
                </label>
                <input
                  id={sliderId}
                  type="range"
                  className="prep__range"
                  min={5}
                  max={95}
                  step={5}
                  disabled={!thresholdOn}
                  aria-label="Missing-value threshold"
                  aria-valuetext={thresholdLabel(value.drop_columns_missing_above)}
                  value={Math.round((value.drop_columns_missing_above ?? lastThreshold) * 100)}
                  onChange={(e) => {
                    const next = Number(e.target.value) / 100;
                    setLastThreshold(next);
                    set("drop_columns_missing_above", next);
                  }}
                />
                <output htmlFor={sliderId} className="prep__rangevalue tnum">
                  {thresholdOn ? `> ${thresholdLabel(value.drop_columns_missing_above)}` : "off"}
                </output>
              </div>
            </Control>
            <Control label="Scaling">
              <Segmented label="Scaling" choices={SCALING} value={value.scaling} onChange={(v) => set("scaling", v)} />
            </Control>
            <label className="check">
              <input
                type="checkbox"
                checked={value.encode_categoricals}
                onChange={(e) => set("encode_categoricals", e.target.checked)}
              />
              <span className="toggle__text">
                <span className="prep__controllabel">Encode categorical columns</span>
                <span className="toggle__hint">Turn text categories into numeric indicator columns.</span>
              </span>
            </label>
            <label className="check">
              <input
                type="checkbox"
                checked={value.date_features}
                onChange={(e) => set("date_features", e.target.checked)}
              />
              <span className="toggle__text">
                <span className="prep__controllabel">Add date features</span>
                <span className="toggle__hint">Split dates into parts such as year, month and weekday.</span>
              </span>
            </label>
          </div>

          {custom && (
            <button type="button" className="linkbtn" onClick={() => onChange({ ...DEFAULT_PREPROCESSING })}>
              Reset to defaults
            </button>
          )}
        </div>
      )}
    </section>
  );
}
