import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { api, ApiError, errorMessage, notifyJobsChanged } from "../api";
import { formatBytes } from "../format";
import { navigate } from "../hooks";
import { DEFAULT_FEATURES, normalizeFeatures } from "../features";
import { DEFAULT_PREPROCESSING } from "../preprocessing";
import type { FeatureOptions, Health, PreprocessingOptions } from "../types";
import { FeaturesPanel } from "./FeaturesPanel";
import { PreprocessingPanel } from "./PreprocessingPanel";
import { Icon, Notice, Spinner } from "./ui";

const ACCEPT = [".csv", ".tsv", ".txt", ".xlsx"];

const EXAMPLES = [
  "How did monthly revenue trend, and which region looks unusual?",
  "Which product categories contribute most to total sales?",
  "Are there outliers in order value within each store?",
  "Is discount associated with lower margin?",
];

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

export function NewAnalysis({ health }: { health: Health | null }) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [question, setQuestion] = useState("");
  const [context, setContext] = useState("");
  const [showContext, setShowContext] = useState(false);
  const [requireReview, setRequireReview] = useState(true);
  const [consent, setConsent] = useState(false);
  const [preprocessing, setPreprocessing] = useState<PreprocessingOptions>(DEFAULT_PREPROCESSING);
  const [features, setFeatures] = useState<FeatureOptions>(DEFAULT_FEATURES);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [sheets, setSheets] = useState<string[]>([]);
  const [sheet, setSheet] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const pick = (next: File | null | undefined) => {
    if (!next) return;
    const ext = extensionOf(next.name);
    setError(null);
    setSuggestions([]);
    setSheets([]);
    setSheet("");
    if (!ACCEPT.includes(ext)) {
      setFile(null);
      setError(`${next.name} isn't a supported file. Upload a ${ACCEPT.join(", ")} file.`);
      return;
    }
    setFile(next);
  };

  const onDrop = (e: DragEvent<HTMLLabelElement>) => {
    e.preventDefault();
    setDragging(false);
    pick(e.dataTransfer.files?.[0]);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || submitting) return;
    setSubmitting(true);
    setError(null);
    setSuggestions([]);
    try {
      const res = await api.upload({
        file,
        question,
        businessContext: context,
        sheetName: sheet || undefined,
        consentThirdPartyLlm: consent,
        requireReview,
        preprocessing,
        features: normalizeFeatures(features),
      });
      notifyJobsChanged();
      navigate(`/jobs/${encodeURIComponent(res.job_id)}`);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
        setSuggestions(err.suggestions);
        if (err.availableSheets.length) {
          setSheets(err.availableSheets);
          setSheet((current) => (current && err.availableSheets.includes(current) ? current : err.availableSheets[0]));
        }
      } else {
        setError(errorMessage(err));
      }
      setSubmitting(false);
    }
  };

  const llmOn = !!health?.llm_enabled;

  return (
    <form className="newjob" onSubmit={(e) => void submit(e)}>
      <header className="page-head">
        <h1 className="page-title">New analysis</h1>
        <p className="page-sub">Upload a table and say what you want to know. You can leave the question blank for an exploratory pass.</p>
      </header>

      <section className="section">
        <label
          className={`dropzone${dragging ? " is-dragging" : ""}${file ? " has-file" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
        >
          <input
            ref={inputRef}
            type="file"
            className="visually-hidden"
            accept={ACCEPT.join(",")}
            onChange={(e) => pick(e.target.files?.[0])}
          />
          {file ? (
            <span className="dropzone__file">
              <span className="dropzone__icon">
                <Icon name="file" size={20} />
              </span>
              <span className="dropzone__meta">
                <span className="dropzone__name">{file.name}</span>
                <span className="dropzone__size">{formatBytes(file.size)}</span>
              </span>
              <span className="dropzone__change">Choose a different file</span>
            </span>
          ) : (
            <span className="dropzone__empty">
              <span className="dropzone__icon">
                <Icon name="upload" size={20} />
              </span>
              <span className="dropzone__prompt">Drop a file here, or browse</span>
              <span className="dropzone__hint">CSV, TSV, TXT or XLSX</span>
            </span>
          )}
        </label>

        {sheets.length > 0 && (
          <div className="sheetpick">
            <label className="field__label" htmlFor="sheet">
              Which sheet should be analysed?
            </label>
            <div className="sheetpick__row">
              <select id="sheet" className="input" value={sheet} onChange={(e) => setSheet(e.target.value)}>
                {sheets.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <button type="submit" className="btn btn--primary" disabled={submitting}>
                Retry with this sheet
              </button>
            </div>
          </div>
        )}
      </section>

      <section className="section">
        <label className="field__label" htmlFor="question">
          Business question
        </label>
        <textarea
          id="question"
          className="input textarea"
          rows={3}
          placeholder="For example: which regions grew fastest last quarter?"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <div className="examples" aria-label="Example questions">
          {EXAMPLES.map((ex) => (
            <button key={ex} type="button" className="chip" onClick={() => setQuestion(ex)}>
              {ex}
            </button>
          ))}
        </div>

        {showContext ? (
          <div className="field">
            <label className="field__label" htmlFor="context">
              Business context <span className="field__optional">optional</span>
            </label>
            <textarea
              id="context"
              className="input textarea"
              rows={2}
              placeholder="What the data covers, known gaps, or what a good answer looks like."
              value={context}
              onChange={(e) => setContext(e.target.value)}
            />
          </div>
        ) : (
          <button type="button" className="linkbtn" onClick={() => setShowContext(true)}>
            Add business context
          </button>
        )}
      </section>

      <PreprocessingPanel value={preprocessing} onChange={setPreprocessing} />

      <FeaturesPanel value={features} onChange={setFeatures} />

      <section className="section options">
        <label className="toggle">
          <input type="checkbox" checked={requireReview} onChange={(e) => setRequireReview(e.target.checked)} />
          <span className="toggle__track" aria-hidden="true">
            <span className="toggle__thumb" />
          </span>
          <span className="toggle__text">
            <span className="toggle__label">Review cleaning before it's applied</span>
            <span className="toggle__hint">
              The agent pauses after proposing fixes so you can approve or reject each one. Turn off to apply only the
              automatic, safe fixes and continue.
            </span>
          </span>
        </label>

        <label className="check">
          <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
          <span className="toggle__text">
            <span className="toggle__label">Allow sending a redacted profile to the language model</span>
            <span className="toggle__hint">
              Only column summaries and short samples, never full rows.{" "}
              {health
                ? llmOn
                  ? "The language model is on for this server."
                  : "The language model is off on this server, so this has no effect right now."
                : "This only matters when the server has a language model enabled."}
            </span>
          </span>
        </label>
      </section>

      {error && (
        <Notice tone="bad" title="The file wasn't accepted">
          <p>{error}</p>
          {suggestions.length > 0 && (
            <ul className="bullets">
              {suggestions.map((s) => (
                <li key={s}>{s}</li>
              ))}
            </ul>
          )}
        </Notice>
      )}

      <div className="newjob__submit">
        <button type="submit" className="btn btn--primary btn--lg" disabled={!file || submitting}>
          {submitting ? <Spinner label="Uploading" /> : "Start analysis"}
        </button>
        {!file && <span className="muted">Choose a file to continue.</span>}
      </div>
    </form>
  );
}
