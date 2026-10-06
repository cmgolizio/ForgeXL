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
const ids = ["sales_history", "sample_history"];
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
    const custom = await handler(url, options);
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
function button(text) { const found = [...document.querySelectorAll("button")].find((element) => element.textContent.replace(/[→↓]/g, "").trim() === text); assert.ok(found, `Missing button ${text}`); return found; }
async function click(element) { await act(async () => element.click()); await settle(); }
async function upload(index, filename = "monthly.csv") {
  const inputs = [...document.querySelectorAll('input[type="file"]')];
  const element = inputs[index]; // Monthly upload controls appear first.
  assert.ok(element);
  Object.defineProperty(element, "files", { configurable: true, value: [new File(["Customer\nSynthetic Account\n"], filename, { type: "text/csv" })] });
  await act(async () => element.dispatchEvent(new dom.Event("change", { bubbles: true })));
}

async function uploadBoth() { for (let index = 0; index < 2; index++) await upload(index, ids[index] + ".csv"); }

test("only sales and sample uploads are offered; generate explains missing files", async () => {
  await mount();
  assert.equal(button("Generate reports").disabled, true);
  assert.match(document.body.textContent, /Add sales data and sample data/);
  assert.equal(document.querySelector('[aria-label="Account Assignments"]'), null);
  assert.equal(document.querySelector('[aria-label="Sales data"]').type, "file");
  await upload(0);
  assert.equal(button("Generate reports").disabled, true);
  await upload(1);
  assert.equal(button("Generate reports").disabled, false);
});

test("one generate button validates and automatically generates clean inputs", async () => {
  handler = (url) => url.endsWith("/validate") ? { ...review, warnings: [] } : url.endsWith("/generate") ? generated : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  const request = calls.find((item) => item.url.endsWith("/validate"));
  assert.equal(request.options.body.get("period"), "2026-09");
  for (const id of ids) assert.equal(request.options.body.get(id).name, id + ".csv");
  assert.equal(request.options.body.has("account_assignments"), false);
  assert.equal(calls.filter((item) => item.url.endsWith("/generate")).length, 1);
  assert.match(document.body.textContent, /Your reports are ready/);
  assert.ok(document.querySelector('a[href="/forge-api/api/runs/run-1/artifacts/download/zip"]'));
  await click(button("Release preview and downloads"));
  assert.ok(calls.some((item) => item.url.endsWith("/run-1/discard")));
});

test("warnings pause generation until explicitly acknowledged", async () => {
  handler = (url) => url.endsWith("/validate") ? review : url.endsWith("/generate") ? generated : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  assert.match(document.body.textContent, /Ready with warnings/);
  assert.equal(button("Generate reports").disabled, true);
  assert.equal(calls.some((item) => item.url.endsWith("/generate")), false);
  await click(document.querySelector('input[aria-label="Acknowledge validation warnings"]'));
  await click(button("Generate reports"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/generate")).options.body), { validation_id: "review-1", acknowledge_warnings: true });
});

test("saved cycles recreate reports without uploads using exact recorded sources", async () => {
  handler = (url) => url.endsWith("/catalog") ? { ...catalog, periods: [{ period: "2026-09", ready: true, cycles: [receipt] }] } : url.endsWith("/validate-saved") ? { ...review, warnings: [] } : url.endsWith("/generate") ? generated : null;
  await mount(); await click(button("Use saved data")); await click(button("Generate reports"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/validate-saved")).options.body), { period: "2026-09", cycle_id: "saved-cycle-1" });
  assert.equal(calls.some((item) => item.url.endsWith("/validate")), false);
  assert.match(document.body.textContent, /Your reports are ready/);
});

test("current corrected data is a deliberate saved-source choice", async () => {
  handler = (url) => url.endsWith("/catalog") ? { ...catalog, periods: [{ period: "2026-09", ready: true, cycles: [receipt] }] } : url.endsWith("/validate-saved") ? review : null;
  await mount(); await click(button("Use saved data"));
  const select = document.querySelector('[aria-label="Saved source selection"]');
  await act(async () => { select.value = "current"; select.dispatchEvent(new dom.Event("change", { bubbles: true })); });
  await click(button("Generate reports"));
  assert.equal(JSON.parse(calls.find((item) => item.url.endsWith("/validate-saved")).options.body).cycle_id, null);
});

test("changing files invalidates review and prevents stale generation", async () => {
  handler = (url) => url.endsWith("/validate") ? review : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  await upload(0, "different.csv");
  assert.ok(calls.some((item) => item.url.endsWith("/discard")));
  assert.equal(document.querySelector('[aria-label="Acknowledge validation warnings"]'), null);
  assert.equal(button("Generate reports").disabled, false);
  assert.equal(calls.some((item) => item.url.endsWith("/generate")), false);
});

test("changing report month retains already selected multi-year files", async () => {
  await mount(); await uploadBoth();
  const month = document.querySelector('input[type="month"]');
  await act(async () => {
    Object.getOwnPropertyDescriptor(dom.HTMLInputElement.prototype, "value").set.call(month, "2026-08");
    month.dispatchEvent(new dom.Event("input", { bubbles: true }));
  });
  assert.match(document.body.textContent, /sales_history.csv/);
  assert.match(document.body.textContent, /sample_history.csv/);
  assert.equal(button("Generate reports").disabled, false);
});

test("generation failure retries exact saved sources without reupload", async () => {
  let attempts = 0;
  handler = (url) => url.endsWith("/validate") || url.endsWith("/validate-saved") ? { ...review, warnings: [] } : url.endsWith("/generate") ?
    ++attempts === 1 ? { ...generated, status: "generation_failed", manifest: null, error: { code: "ACTION_FAILED", message: "Workbook rendering failed." } } : generated : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  assert.match(document.body.textContent, /Sources saved · Reports not generated/);
  assert.equal(document.querySelectorAll('a[href*="artifacts"]').length, 0);
  await click(button("Retry using saved data"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/validate-saved")).options.body), { period: "2026-09", cycle_id: "saved-cycle-1" });
  assert.match(document.body.textContent, /Your reports are ready/);
});

test("errors block generation and allow another attempt", async () => {
  handler = (url) => url.endsWith("/validate") ? { ...review, ready: false, validation_id: null, warnings: [], errors: [{ code: "MISSING_COLUMNS", message: "Required sales columns are missing." }] } : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  assert.match(document.body.textContent, /Required sales columns are missing/);
  assert.equal(calls.some((item) => item.url.endsWith("/generate")), false);
  handler = (url) => { if (url.endsWith("/validate")) throw new TypeError("Disconnected"); return null; };
  await click(button("Generate reports"));
  assert.match(document.body.textContent, /Could not reach ForgeXL/);
});

test("a running request locks controls and prevents double submission", async () => {
  let release;
  handler = (url) => url.endsWith("/validate") ? new Promise((resolve) => { release = resolve; }) : null;
  await mount(); await uploadBoth();
  await act(async () => { button("Generate reports").click(); button("Generate reports").click(); });
  assert.equal(button("Checking your files…").disabled, true);
  assert.equal(button("Use saved data").disabled, true);
  assert.equal(document.querySelector('input[type="month"]').disabled, true);
  assert.equal(calls.filter((item) => item.url.endsWith("/validate")).length, 1);
  await act(async () => release({ ...review, ready: false, errors: [], warnings: [] }));
});

test("preview connection failure retries without generating again", async () => {
  let attempts = 0;
  handler = (url) => {
    if (url.endsWith("/validate")) return { ...review, warnings: [] };
    if (url.endsWith("/generate")) return generated;
    if (url.includes("/preview") && attempts++ === 0) throw new TypeError("Temporary drop");
    return null;
  };
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  await click(button("Retry preview"));
  assert.match(document.body.textContent, /995/);
  assert.equal(calls.filter((item) => item.url.endsWith("/generate")).length, 1);
});

test("unsupported files fail clearly before sending any request", async () => {
  await mount(); await upload(0, "unsupported.xls");
  assert.match(document.body.textContent, /Choose a CSV or Excel/);
  assert.equal(button("Generate reports").disabled, true);
  assert.equal(calls.some((item) => item.url.endsWith("/validate")), false);
});

test("conflicting master offers saved-month recovery without losing sample upload", async () => {
  let attempts = 0;
  handler = (url) => url.endsWith("/validate") ? ++attempts === 1 ? { ...review, ready: false, validation_id: null, reps: [],
    errors: [{ code: "HISTORY_MONTH_CONFLICT", slot_id: "sales_history", message: "Saved August differs." }] } :
    { ...review, warnings: [{ code: "HISTORY_DIFFERENCES_IGNORED", message: "Using saved sales months." }] } : null;
  await mount(); await uploadBoth(); await click(button("Generate reports"));
  assert.match(document.body.textContent, /Rep detection waits/);
  await click(button("Use saved sales months"));
  assert.match(document.body.textContent, /sales_history.csv/);
  assert.match(document.body.textContent, /sample_history.csv/);
  assert.equal(document.body.textContent.includes("Saved August differs"), false);
  await click(button("Generate reports"));
  const latest = calls.filter((item) => item.url.endsWith("/validate")).at(-1).options.body;
  assert.equal(latest.get("sales_history.use_saved_months"), "true");
  assert.equal(latest.has("sample_history.use_saved_months"), false);
  assert.equal(latest.get("sample_history").name, "sample_history.csv");
  assert.equal(button("Generate reports").disabled, true);
  assert.equal(calls.some((item) => item.url.endsWith("/generate")), false);
  // Replacing a selected file clears its previous reuse decision.
  await upload(0, "corrected.csv");
  await click(button("Generate reports"));
  assert.equal(calls.filter((item) => item.url.endsWith("/validate")).at(-1).options.body.has("sales_history.use_saved_months"), false);
});
