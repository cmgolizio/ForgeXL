// DOM interaction coverage; this is not a substitute for a live-browser check.
import assert from "node:assert/strict";
import { after, afterEach, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import { build } from "esbuild";
import { Window } from "happy-dom";
import React, { act } from "react";
// Load the client after a DOM exists, as it does in a browser.
const initialDom = new Window();
globalThis.window = initialDom; globalThis.document = initialDom.document;
const { createRoot } = await import("react-dom/client");

const rootPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.join(rootPath, "node_modules/.cache/forgexl-frontend-test/monthly.cjs");
await build({ entryPoints: ["src/components/monthly/MonthlyReports.jsx"], absWorkingDir: rootPath,
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", outfile: output,
  alias: { "@": path.join(rootPath, "src") }, external: ["react", "react/jsx-runtime"] });
const MonthlyReports = createRequire(import.meta.url)(output).default;
const ids = ["sales_history", "sample_history", "account_assignments"];
const catalog = {
  default_period: "2026-09",
  datasets: ids.map((id) => ({ id, label: id, required_columns: ["Customer"], versions: [] })),
  periods: [],
};
const review = { period: "2026-09", ready: true, validation_id: "review-1", sources: [],
  reps: ["Synthetic Rep"], checks: [{ label: "Source rows", status: "passed", detail: "5 rows" }],
  coverage: [], errors: [], warnings: [{ code: "HISTORY", message: "Prior history is incomplete." }],
  source_selection: "Reviewed monthly inputs" };
const receipt = { period: "2026-09", cycle_id: "saved-cycle-1", action: { version: "0.2.0" },
  created_at: "2026-10-01T12:00:00Z", versions: Object.fromEntries(ids.map((id) => [id, [id + "-v1"]])) };
const generated = { period: "2026-09", status: "reports_generated", sources_committed: true,
  committed_versions: { sales_history: "sales-v1" }, receipt,
  manifest: { run_id: "run-1", outputs: [{ id: "company_summary", label: "Company summary" }],
    artifacts: [{ id: "rep-1", label: "Synthetic Rep", filename: "Synthetic Rep - September 2026.xlsx", artifact_type: "workbook", size_bytes: 2000 }] } };
let dom, root, calls, handler;
const originalFetch = globalThis.fetch;

beforeEach(() => {
  dom = new Window({ url: "http://localhost/monthly-reports" });
  globalThis.window = dom; globalThis.document = dom.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  calls = []; handler = () => null;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const custom = handler(url, options);
    const payload = custom ?? (url.endsWith("/catalog") ? catalog : url.includes("/preview") ?
      { columns: ["Revenue"], rows: [[995]], total_rows: 1, offset: 0 } : { discarded: true });
    return new Response(JSON.stringify(payload), { status: 200, headers: { "content-type": "application/json" } });
  };
  document.body.innerHTML = "<div id='root'></div>";
  root = createRoot(document.getElementById("root"));
});
afterEach(async () => {
  await act(async () => root.unmount());
  await dom.happyDOM.close();
  globalThis.fetch = originalFetch;
  delete globalThis.window; delete globalThis.document;
});
after(async () => { await initialDom.happyDOM.close(); await fs.rm(path.dirname(output), { recursive: true, force: true }); });

async function settle() { await act(async () => { await new Promise((resolve) => setTimeout(resolve, 15)); }); }
async function mount() { await act(async () => root.render(React.createElement(MonthlyReports))); await settle(); }
function button(text) { const found = [...document.querySelectorAll("button")].find((element) => element.textContent === text); assert.ok(found, `Missing button ${text}`); return found; }
async function click(element) { await act(async () => element.click()); await settle(); }
async function upload(index, filename = "monthly.csv") {
  const inputs = [...document.querySelectorAll('input[type="file"]')];
  const element = inputs[index + 1]; // history input precedes the three monthly slots
  assert.ok(element);
  Object.defineProperty(element, "files", { configurable: true, value: [new File(["Customer\nSynthetic Account\n"], filename, { type: "text/csv" })] });
  await act(async () => element.dispatchEvent(new dom.Event("change", { bubbles: true })));
}

test("monthly uploads require validation and warning consent before generation", async () => {
  handler = (url) => url.endsWith("/validate") ? review : url.endsWith("/generate") ? generated : null;
  await mount();
  for (let index = 0; index < 3; index++) await upload(index, ids[index] + ".csv");
  assert.equal([...document.querySelectorAll("button")].some((item) => item.textContent === "Generate reports"), false);
  await click(button("Validate sources"));
  const request = calls.find((item) => item.url.endsWith("/validate"));
  assert.equal(request.options.body.get("period"), "2026-09");
  for (const id of ids) assert.equal(request.options.body.get(id).name, id + ".csv");
  assert.match(document.body.textContent, /Ready with warnings/);
  assert.equal(button("Generate reports").disabled, true);
  await click(document.querySelector('input[type="checkbox"]'));
  await click(button("Generate reports"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/generate")).options.body), { validation_id: "review-1", acknowledge_warnings: true });
  assert.match(document.body.textContent, /Sources saved · Reports generated/);
  assert.ok(document.querySelector('a[href="/forge-api/api/runs/run-1/artifacts/download/zip"]'));
  assert.match(document.body.textContent, /995/);
});

test("saved cycles select exact sources without uploading again", async () => {
  handler = (url) => url.endsWith("/catalog") ? { ...catalog, periods: [{ period: "2026-09", ready: true, cycles: [receipt] }] } : url.endsWith("/validate-saved") ? review : null;
  await mount(); await click(button("Rerun saved reports")); await click(button("Validate sources"));
  const request = calls.find((item) => item.url.endsWith("/validate-saved"));
  assert.deepEqual(JSON.parse(request.options.body), { period: "2026-09", cycle_id: "saved-cycle-1" });
  assert.equal(calls.some((item) => item.url.endsWith("/validate")), false);
});

test("failed generation displays saved sources and retries the recorded cycle", async () => {
  handler = (url) => url.endsWith("/validate") || url.endsWith("/validate-saved") ? review : url.endsWith("/generate") ?
    { ...generated, status: "generation_failed", manifest: null, error: { code: "ACTION_FAILED", message: "Workbook rendering failed." } } : null;
  await mount(); await click(button("Validate sources")); await click(document.querySelector('input[type="checkbox"]')); await click(button("Generate reports"));
  assert.match(document.body.textContent, /Sources saved · Reports not generated/);
  assert.match(document.body.textContent, /Workbook rendering failed/);
  assert.equal(document.querySelectorAll('a[href*="artifacts"]').length, 0);
  await click(button("Review saved sources to retry"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/validate-saved")).options.body), { period: "2026-09", cycle_id: "saved-cycle-1" });
});

test("corrections name the saved version and changes discard an earlier review", async () => {
  handler = (url) => url.endsWith("/catalog") ? { ...catalog, datasets: catalog.datasets.map((item) => ({ ...item, versions: [{ period: "2026-09", version_id: item.id + "-old", source_filename: "saved.csv", row_count: 5 }] })) } : url.endsWith("/validate") ? { ...review, warnings: [] } : null;
  await mount(); await upload(0, "corrected.csv");
  await click(document.querySelector('input[type="checkbox"]'));
  // React tracks textarea values through its native setter.
  const textarea = document.querySelector("textarea");
  await act(async () => { Object.getOwnPropertyDescriptor(dom.HTMLTextAreaElement.prototype, "value").set.call(textarea, "Missing invoice included"); textarea.dispatchEvent(new dom.Event("input", { bubbles: true })); });
  await click(button("Validate sources"));
  const request = calls.find((item) => item.url.endsWith("/validate"));
  assert.equal(request.options.body.get("sales_history.replaces"), "sales_history-old");
  assert.equal(request.options.body.get("reason"), "Missing invoice included");
  await click(button("Remove"));
  assert.ok(calls.some((item) => item.url.endsWith("/discard")));
  assert.equal([...document.querySelectorAll("button")].some((item) => item.textContent === "Generate reports"), false);
});

test("history setup validates then saves through the dedicated endpoints", async () => {
  handler = (url) => url.endsWith("/history/validate") ? { ready: true, validation_id: "history-1", row_count: 20, periods: ["2026-07", "2026-08"], errors: [], warnings: [] } :
    url.endsWith("/history/commit") ? { status: "saved", committed_versions: ["july", "august"] } : null;
  await mount();
  await click([...document.querySelectorAll("summary")].find((item) => item.textContent.includes("Initial history setup")));
  const element = document.querySelector('input[type="file"]');
  Object.defineProperty(element, "files", { configurable: true, value: [new File(["history"], "history.csv")] });
  await act(async () => element.dispatchEvent(new dom.Event("change", { bubbles: true })));
  await click(button("Validate history"));
  const request = calls.find((item) => item.url.endsWith("/history/validate"));
  assert.equal(request.options.body.get("dataset_id"), "sales_history");
  assert.equal(request.options.body.get("source_file").name, "history.csv");
  await click(button("Save history"));
  assert.match(document.body.textContent, /History saved\. 2 monthly version/);
});

test("validation errors and failed revalidation remove permission to generate", async () => {
  handler = (url) => url.endsWith("/validate") ? review : null;
  await mount(); await click(button("Validate sources"));
  assert.ok(button("Generate reports"));
  handler = (url) => { if (url.endsWith("/validate")) throw new TypeError("Disconnected"); return null; };
  await click(button("Validate sources"));
  assert.match(document.body.textContent, /Could not reach ForgeXL/);
  assert.equal([...document.querySelectorAll("button")].some((item) => item.textContent === "Generate reports"), false);
  handler = (url) => url.endsWith("/validate") ? { ...review, ready: false, validation_id: null, warnings: [], errors: [{ code: "MISSING_COLUMNS", message: "Required sales columns are missing." }] } : null;
  await click(button("Validate sources"));
  assert.match(document.body.textContent, /Validation needs attention/);
  assert.match(document.body.textContent, /Required sales columns are missing/);
  assert.equal([...document.querySelectorAll("button")].some((item) => item.textContent === "Generate reports"), false);
});
