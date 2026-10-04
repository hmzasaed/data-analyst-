import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorMessage, saveBlob, type ReportFormat } from "../api";
import { useResource, useWindowEvent } from "../hooks";
import { Icon, Notice, ResourceView } from "./ui";

const FORMATS: { format: ReportFormat; label: string; ext: string }[] = [
  { format: "html", label: "HTML", ext: "html" },
  { format: "markdown", label: "Markdown", ext: "md" },
  { format: "json", label: "JSON", ext: "json" },
];

const MIN_FRAME = 600;
const BLOB_TTL_MS = 60_000;

/**
 * The report is server-generated, escaped and script-free. The frame gets
 * `allow-same-origin` (never `allow-scripts`) so the parent can read its height and
 * size it to the content; nothing inside it can run code.
 */
function ReportFrame({ html }: { html: string }) {
  const ref = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState<number | null>(null);

  const measure = useCallback((initial: boolean) => {
    const frame = ref.current;
    let doc: Document | null = null;
    try {
      doc = frame?.contentDocument ?? null;
    } catch {
      doc = null;
    }
    if (!doc?.documentElement) return;
    const root = doc.documentElement;
    const body = doc.body;
    let content = Math.ceil(root.getBoundingClientRect().height);
    if (body) {
      const margin = parseFloat(doc.defaultView?.getComputedStyle(body).marginBottom ?? "0") || 0;
      content = Math.max(content, Math.ceil(body.getBoundingClientRect().bottom + margin));
    }
    // scrollHeight never drops below the frame's own height, so only trust it on load.
    if (initial) content = Math.max(content, root.scrollHeight);
    const next = Math.max(MIN_FRAME, content);
    setHeight((h) => (h === next ? h : next));
  }, []);

  useWindowEvent("resize", () => measure(false));

  useEffect(() => {
    setHeight(null);
  }, [html]);

  const observer = useRef<ResizeObserver | null>(null);
  useEffect(() => () => observer.current?.disconnect(), []);

  const onLoad = () => {
    measure(true);
    observer.current?.disconnect();
    observer.current = null;
    try {
      const body = ref.current?.contentDocument?.body;
      if (body && typeof ResizeObserver !== "undefined") {
        observer.current = new ResizeObserver(() => measure(false));
        observer.current.observe(body);
      }
    } catch {
      /* not measurable: keep the CSS height */
    }
  };

  return (
    <iframe
      ref={ref}
      className="report__frame"
      title="Analysis report"
      sandbox="allow-same-origin"
      srcDoc={html}
      onLoad={onLoad}
      style={height ? { height } : undefined}
    />
  );
}

export function ReportTab({ jobId, refreshKey, filename }: { jobId: string; refreshKey: string; filename?: string | null }) {
  const report = useResource(() => api.reportHtml(jobId), `${jobId}:${refreshKey}`);
  const [busy, setBusy] = useState<ReportFormat | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [printHint, setPrintHint] = useState(false);
  const blobUrls = useRef<Set<string>>(new Set());

  useEffect(() => {
    const urls = blobUrls.current;
    return () => {
      urls.forEach((u) => URL.revokeObjectURL(u));
      urls.clear();
    };
  }, []);

  const base = (filename || "insightflow-report").replace(/\.[^.]+$/, "").replace(/[^\w.-]+/g, "_");

  const download = async (format: ReportFormat, ext: string) => {
    setBusy(format);
    setError(null);
    try {
      const blob = await api.reportBlob(jobId, format);
      saveBlob(blob, `${base}-report.${ext}`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  const openInTab = (html: string, forPrint: boolean) => {
    setError(null);
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    blobUrls.current.add(url);
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      blobUrls.current.delete(url);
    }, BLOB_TTL_MS);
    const win = window.open(url, "_blank");
    if (!win) {
      setError("The browser blocked the new tab. Allow pop-ups for this site, or download the HTML instead.");
      return;
    }
    win.opener = null;
    if (forPrint) setPrintHint(true);
  };

  return (
    <ResourceView
      resource={report}
      notReadyTitle="The report hasn't been written yet"
      notReadyText="It appears here once findings are verified and the report stage finishes."
    >
      {(html) => (
        <div className="report">
          <div className="report__bar">
            <span className="muted">Self-contained report. Charts are embedded, nothing loads from the network.</span>
            <span className="report__downloads">
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => openInTab(html, false)}>
                Open in new tab
              </button>
              <button type="button" className="btn btn--ghost btn--sm" onClick={() => openInTab(html, true)}>
                Print / Save as PDF
              </button>
              {FORMATS.map((f) => (
                <button
                  key={f.format}
                  type="button"
                  className="btn btn--ghost btn--sm"
                  disabled={busy !== null}
                  onClick={() => void download(f.format, f.ext)}
                >
                  <Icon name="download" size={14} /> {busy === f.format ? "Preparing" : f.label}
                </button>
              ))}
            </span>
          </div>
          {printHint && (
            <Notice
              tone="info"
              title="Report opened in a new tab"
              action={
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => setPrintHint(false)}>
                  Dismiss
                </button>
              }
            >
              Use your browser's Print → Save as PDF in that tab.
            </Notice>
          )}
          {error && <Notice tone="bad" title="That didn't work">{error}</Notice>}
          <ReportFrame html={html} />
        </div>
      )}
    </ResourceView>
  );
}
