// Capture README screenshots of a running InsightFlow instance with headless Chrome.
//
//   node infra/scripts/screenshots.mjs <base-url> <owner-id> <completed-job> <review-job> <out-dir> [report.html]
//
// Drives Chrome over the DevTools protocol (Node 22+ has a global WebSocket), so it
// can set the workspace id, click tabs and capture exact page regions. Requires the
// API to be serving the built UI at <base-url>.
import { spawn } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const [base, owner, completedJob, reviewJob, outDir, reportFile] = process.argv.slice(2);
if (!base || !owner || !completedJob || !reviewJob || !outDir) {
  console.error("usage: node screenshots.mjs <base-url> <owner-id> <completed-job> <review-job> <out-dir> [report.html]");
  process.exit(2);
}
const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
].filter(Boolean);
const chromePath = CHROME_CANDIDATES.find((p) => existsSync(p));
if (!chromePath) throw new Error("Chrome not found; set CHROME_PATH");

const WIDTH = 1440;
const PORT = 9333;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
mkdirSync(outDir, { recursive: true });

const chrome = spawn(chromePath, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "if-shots-"))}`,
  "--hide-scrollbars", "--force-color-profile=srgb", "--no-first-run", "--no-default-browser-check",
  `--window-size=${WIDTH},900`, "about:blank",
], { stdio: "ignore" });

let socket; let nextId = 1; const pending = new Map();
async function connect() {
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      const pages = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      const page = pages.find((p) => p.type === "page");
      if (page) {
        socket = new WebSocket(page.webSocketDebuggerUrl);
        await new Promise((ok, fail) => { socket.onopen = ok; socket.onerror = fail; });
        socket.onmessage = (event) => {
          const message = JSON.parse(event.data);
          if (message.id && pending.has(message.id)) {
            const { resolve: done, reject } = pending.get(message.id);
            pending.delete(message.id);
            message.error ? reject(new Error(message.error.message)) : done(message.result);
          }
        };
        return;
      }
    } catch { /* not up yet */ }
    await sleep(200);
  }
  throw new Error("could not connect to Chrome");
}
const send = (method, params = {}) => new Promise((ok, fail) => {
  const id = nextId++;
  pending.set(id, { resolve: ok, reject: fail });
  socket.send(JSON.stringify({ id, method, params }));
});
const evaluate = async (expression) => {
  const { result, exceptionDetails } = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
  if (exceptionDetails) throw new Error(exceptionDetails.text + " " + (exceptionDetails.exception?.description ?? ""));
  return result.value;
};
async function goto(url, settle = 2500) {
  await send("Page.navigate", { url });
  if (url.includes("#")) await send("Page.reload", {});  // a hash change alone does not reload
  await sleep(settle);
}
async function waitFor(expression, timeout = 20000) {
  const started = Date.now();
  while (Date.now() - started < timeout) {
    if (await evaluate(`Boolean(${expression})`)) return;
    await sleep(250);
  }
  throw new Error(`timed out waiting for ${expression}`);
}
const clickText = (selector, text) => evaluate(`(() => {
  const el = [...document.querySelectorAll(${JSON.stringify(selector)})].find(e => e.textContent.trim().startsWith(${JSON.stringify(text)}));
  if (!el) throw new Error("no element with text ${text}"); el.click(); return true; })()`);

/** Capture the page from `top` (document y) for `height` px, or a region around a selector. */
async function shot(name, { top = 0, height = 900, selector, pad = 16, maxHeight = 2600 } = {}) {
  if (selector) {
    // Lay the whole page out and let lazy images load first, so nothing above the
    // target shifts it after it has been measured.
    const full = await evaluate("document.documentElement.scrollHeight");
    await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: full, deviceScaleFactor: 1, mobile: false });
    await waitFor("[...document.images].every((i) => i.complete)").catch(() => {});
    await sleep(800);
    const rect = await evaluate(`(() => { const r = document.querySelector(${JSON.stringify(selector)}).getBoundingClientRect();
      return { top: r.top + scrollY, height: r.height }; })()`);
    top = Math.max(0, rect.top - pad);
    height = Math.min(rect.height + pad * 2, maxHeight);
  }
  const pageHeight = await evaluate("document.documentElement.scrollHeight");
  height = Math.max(200, Math.min(height, pageHeight - top));
  await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: Math.ceil(top + height), deviceScaleFactor: 1, mobile: false });
  await sleep(600);
  const { data } = await send("Page.captureScreenshot", {
    format: "png", captureBeyondViewport: true, clip: { x: 0, y: top, width: WIDTH, height, scale: 1 },
  });
  await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: 900, deviceScaleFactor: 1, mobile: false });
  writeFileSync(join(outDir, `${name}.png`), Buffer.from(data, "base64"));
  console.log(`saved ${name}.png (${Math.round(height)}px)`);
}

try {
  await connect();
  await send("Page.enable");
  await send("Runtime.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: WIDTH, height: 900, deviceScaleFactor: 1, mobile: false });
  await goto(`${base}/`);
  await evaluate(`localStorage.setItem("insightflow.owner_id", ${JSON.stringify(owner)}); true`);
  // The app reads the workspace id once at start-up, so reload before any capture.
  await send("Page.reload", { ignoreCache: true });
  await sleep(2000);

  // 1. New analysis form, with both option panels expanded.
  await goto(`${base}/#/new`);
  await waitFor(`document.querySelector("textarea")`);
  for (const label of ["Preprocessing", "Feature engineering"]) {
    await clickText("button", label).catch(() => {});
    await sleep(300);
  }
  await shot("01-new-analysis", { height: 1500 });

  // 2. Agent lifecycle of a completed job.
  await goto(`${base}/#/jobs/${completedJob}`, 3500);
  await waitFor(`document.body.innerText.includes("Completed")`);
  await shot("02-lifecycle", { height: 1050 });

  // 3. Report tab (the iframe sizes itself to the report).
  await clickText('[role="tab"]', "Report");
  await sleep(3500);
  await shot("03-report-tab", { selector: '[role="tabpanel"]', maxHeight: 1500 });

  // 4. Features tab.
  await clickText('[role="tab"]', "Features");
  await waitFor(`document.body.innerText.includes("Download feature set")`);
  await sleep(800);
  await shot("04-features-tab", { selector: '[role="tabpanel"]', maxHeight: 1800 });

  // 5. Preprocessing tab.
  await clickText('[role="tab"]', "Preprocessing");
  await waitFor(`document.body.innerText.includes("Download model-ready")`);
  await sleep(800);
  await shot("05-preprocessing-tab", { selector: '[role="tabpanel"]', maxHeight: 1500 });

  // 6. Human review gate.
  await goto(`${base}/#/jobs/${reviewJob}`, 3500);
  await waitFor(`document.body.innerText.includes("Apply & continue")`);
  await shot("06-cleaning-review", { height: 1300 });

  // 7. The standalone report: header + KPIs, and the feature section.
  if (reportFile) {
    await goto(pathToFileURL(resolve(reportFile)).href, 3000);
    await shot("07-report-overview", { height: 1250 });
    await shot("08-report-distributions", { selector: "#distributions", maxHeight: 1700 });
    await shot("09-report-features", { selector: "#features", maxHeight: 2400 });
    await shot("10-report-feature-charts", { selector: "#features .charts", maxHeight: 1600 });
  }
} finally {
  socket?.close();
  chrome.kill();
}
