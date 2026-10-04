import { useEffect, useId, useState } from "react";
import {
  clampInt,
  DEFAULT_FEATURES,
  isDefaultFeatures,
  MAX_ENGINEERED_RANGE,
  MAX_FEATURES_RANGE,
  summarizeFeatures,
} from "../features";
import type { FeatureOptions } from "../types";

/** Integer input that tolerates an empty field while typing and clamps on blur. */
function CountInput({
  id,
  value,
  min,
  max,
  disabled,
  onChange,
}: {
  id: string;
  value: number;
  min: number;
  max: number;
  disabled?: boolean;
  onChange: (next: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);

  return (
    <input
      id={id}
      type="number"
      inputMode="numeric"
      className="input feat__num tnum"
      min={min}
      max={max}
      step={1}
      disabled={disabled}
      value={draft}
      onChange={(e) => {
        setDraft(e.target.value);
        const n = Number(e.target.value);
        if (e.target.value !== "" && Number.isFinite(n) && n >= min && n <= max) onChange(Math.round(n));
      }}
      onBlur={() => {
        const next = clampInt(draft === "" ? value : Number(draft), min, max);
        setDraft(String(next));
        if (next !== value) onChange(next);
      }}
    />
  );
}

export function FeaturesPanel({
  value,
  onChange,
}: {
  value: FeatureOptions;
  onChange: (next: FeatureOptions) => void;
}) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const targetId = useId();
  const targetHelpId = useId();
  const maxFeaturesId = useId();
  const maxEngineeredId = useId();
  const set = <K extends keyof FeatureOptions>(key: K, next: FeatureOptions[K]) => onChange({ ...value, [key]: next });

  const custom = !isDefaultFeatures(value);
  const off = !value.enabled;

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
            Feature engineering &amp; selection {custom && <span className="field__optional">customised</span>}
          </span>
          {!open && <span className="prep__summary">{summarizeFeatures(value)}</span>}
        </span>
      </button>

      {open && (
        <div className="prep__body" id={bodyId}>
          <div className="prep__group">
            <label className="toggle">
              <input type="checkbox" checked={value.enabled} onChange={(e) => set("enabled", e.target.checked)} />
              <span className="toggle__track" aria-hidden="true">
                <span className="toggle__thumb" />
              </span>
              <span className="toggle__text">
                <span className="toggle__label">Engineer and select features</span>
                <span className="toggle__hint">
                  The agent proposes new features, validates them, then ranks every feature against the target and
                  keeps the strongest. You get a downloadable feature set.
                </span>
              </span>
            </label>
          </div>

          <div className={`prep__group${off ? " feat__group--off" : ""}`}>
            <div className="prep__control">
              <div className="prep__controlhead">
                <label className="prep__controllabel" htmlFor={targetId}>
                  Target column
                </label>
                <span className="prep__controlhint">optional</span>
              </div>
              <input
                id={targetId}
                type="text"
                className="input feat__target"
                placeholder="Detect automatically"
                autoComplete="off"
                spellCheck={false}
                disabled={off}
                aria-describedby={targetHelpId}
                value={value.target_column ?? ""}
                onChange={(e) => set("target_column", e.target.value === "" ? null : e.target.value)}
              />
              <p className="prep__note" id={targetHelpId}>
                The column a model would predict. Leave blank and the agent picks it from your question or the data.
              </p>
            </div>

            <div className="feat__counts">
              <div className="prep__control">
                <div className="prep__controlhead">
                  <label className="prep__controllabel" htmlFor={maxFeaturesId}>
                    Max selected features
                  </label>
                  <span className="prep__controlhint">
                    {MAX_FEATURES_RANGE.min}–{MAX_FEATURES_RANGE.max}
                  </span>
                </div>
                <CountInput
                  id={maxFeaturesId}
                  value={value.max_features}
                  min={MAX_FEATURES_RANGE.min}
                  max={MAX_FEATURES_RANGE.max}
                  disabled={off}
                  onChange={(n) => set("max_features", n)}
                />
              </div>
              <div className="prep__control">
                <div className="prep__controlhead">
                  <label className="prep__controllabel" htmlFor={maxEngineeredId}>
                    Max engineered features
                  </label>
                  <span className="prep__controlhint">
                    {MAX_ENGINEERED_RANGE.min}–{MAX_ENGINEERED_RANGE.max}, 0 means none
                  </span>
                </div>
                <CountInput
                  id={maxEngineeredId}
                  value={value.max_engineered}
                  min={MAX_ENGINEERED_RANGE.min}
                  max={MAX_ENGINEERED_RANGE.max}
                  disabled={off}
                  onChange={(n) => set("max_engineered", n)}
                />
              </div>
            </div>
          </div>

          {custom && (
            <button type="button" className="linkbtn" onClick={() => onChange({ ...DEFAULT_FEATURES })}>
              Reset to defaults
            </button>
          )}
        </div>
      )}
    </section>
  );
}
