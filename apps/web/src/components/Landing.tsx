import { navigate } from "../hooks";
import { Icon } from "./ui";

const STEPS = [
  { name: "Ingest", text: "Detects encoding and delimiter, normalises headers and hashes the raw file. Unsafe or oversized files are refused." },
  { name: "Profile", text: "Infers column types and business roles, flags possible personal data and lists what the data cannot support." },
  { name: "Clean", text: "Proposes fixes. Only safe ones apply automatically; you can approve the rest. The raw file is never changed." },
  { name: "Plan", text: "Maps your question onto registered analysis tools. Steps the dataset can't support are named and dropped." },
  { name: "Execute", text: "Runs each tool with validated parameters. Failed steps are recorded, not hidden." },
  { name: "Verify", text: "Checks every finding for evidence, artifact existence, dataset version and causal or absolute wording." },
  { name: "Report", text: "Writes a self-contained report where each figure links back to the computed value behind it." },
];

export function Landing() {
  return (
    <div className="landing">
      <section className="landing__intro">
        <h1 className="landing__title">Ask a question of a spreadsheet and get an answer you can check.</h1>
        <p className="landing__lede">
          InsightFlow runs your file through a fixed, auditable sequence. Numbers are computed by registered tools,
          never written by a model, and every claim in the report carries the evidence that produced it.
        </p>
        <button type="button" className="btn btn--primary btn--lg" onClick={() => navigate("/new")}>
          <Icon name="upload" /> Start an analysis
        </button>
      </section>

      <ol className="steps" aria-label="What happens to your file">
        {STEPS.map((step, i) => (
          <li className="steps__item" key={step.name}>
            <span className="steps__index tnum" aria-hidden="true">
              {i + 1}
            </span>
            <div>
              <p className="steps__name">{step.name}</p>
              <p className="steps__text">{step.text}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
