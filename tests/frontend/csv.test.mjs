// Verify the generic choose → upload → generate flow and metadata-driven forms.
import assert from "node:assert/strict";
import { after, afterEach, beforeEach, test } from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs/promises";
import { build } from "esbuild";
import { Window } from "happy-dom";
import React, { act } from "react";

const initialDom = new Window();
globalThis.window = initialDom; globalThis.document = initialDom.document;
const { createRoot } = await import("react-dom/client");
const rootPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const output = path.join(rootPath, "node_modules/.cache/forgexl-csv-test/csv.cjs");
await build({ entryPoints: ["src/components/csv/CSVTools.jsx"], absWorkingDir: rootPath,
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", outfile: output,
  alias: { "@": path.join(rootPath, "src") }, external: ["react", "react/jsx-runtime"],
  plugins: [{ name: "link-dom", setup(builder) {
    builder.onResolve({ filter: /^next\/link$/ }, () => ({ path: "link", namespace: "test" }));
    builder.onLoad({ filter: /.*/, namespace: "test" }, () => ({ contents: 'import React from "react"; export default function Link(props) { return React.createElement("a", props); }' }));
  } }] });
const CSVTools = createRequire(import.meta.url)(output).default;
const actions = [
  { id: "combine_csv", name: "Combine CSV files", workflow_path: "/csv-tools?action=combine_csv", description: "Combine in order", inputs: [{ id: "combine_source", label: "Source CSV", required: true, max_files: 1 }, { id: "combine_additional", label: "Additional CSVs", required: true, max_files: 19 }] },
  { id: "filter_csv", name: "Filter a CSV", workflow_path: "/csv-tools?action=filter_csv", description: "Filter rows", inputs: [{ id: "filter_source", label: "Source CSV", required: true, max_files: 1 }] },
];
const manifest = { run_id: "run-1", action: actions[0], duration_ms: 5, inputs: [],
  audit: { rows_received: 250, rows_returned: 249 }, validation: { warnings: [] }, artifacts: [],
  metrics: { input_rows: 250, duplicates_removed: 1, rows_excluded: 0, output_rows: 249, effective_options: { remove_duplicates: true, conditions: [] } },
  outputs: [{ id: "csv_result", label: "CSV result", row_count: 249, input_row_count: 250, column_count: 2, formats: ["csv"] }] };
let dom, root, calls, handler, inspectedNumber;
const originalFetch = globalThis.fetch;
beforeEach(() => {
  dom = new Window({ url: "http://localhost/csv-tools" });
  globalThis.window = dom; globalThis.document = dom.document; globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  calls = []; handler = () => null; inspectedNumber = 0;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const custom = await handler(url, options);
    const payload = custom ?? (url.endsWith("/actions") ? { actions } : (url.endsWith("/inspect") || url.endsWith("/reorder")) ? { session_id: `session-${++inspectedNumber}`, columns: ["ID", "Customer"], rows_received: 250 } : url.endsWith("/preview?offset=0&limit=100") ? { columns: ["ID","Customer"], rows: [["0001","Château"]], total_rows: 249, offset: 0 } : manifest);
    return payload instanceof Response ? payload : new Response(JSON.stringify(payload), { status: 200 });
  };
  document.body.innerHTML = "<div id='root'></div>"; root = createRoot(document.getElementById("root"));
});
afterEach(async () => { await act(async () => root.unmount()); await dom.happyDOM.close(); globalThis.fetch = originalFetch; delete globalThis.window; delete globalThis.document; });
after(async () => { await initialDom.happyDOM.close(); await fs.rm(path.dirname(output), { recursive: true, force: true }); });
async function settle() { await act(async () => new Promise((resolve) => setTimeout(resolve, 15))); }
async function mount(action = "combine_csv") { await act(async () => root.render(React.createElement(CSVTools, { initialActionId: action }))); await settle(); }
function button(label) { const found = [...document.querySelectorAll("button")].find((item) => item.textContent.trim() === label); assert.ok(found, `Missing button ${label}`); return found; }
async function click(element) { await act(async () => element.click()); await settle(); }
async function upload(label, names) {
  const input = document.querySelector(`input[aria-label="${label}"]`);
  Object.defineProperty(input, "files", { configurable: true, value: names.map((name) => new File(["ID,Customer\n0001,Château\n"], name)) });
  await act(async () => input.dispatchEvent(new dom.Event("change", { bubbles: true }))); await settle();
}
async function set(label, value) {
  const input = document.querySelector(`[aria-label="${label}"]`); assert.ok(input, `Missing ${label}`);
  await act(async () => {
    // Use the native setter so React's controlled-input tracker sees the edit.
    const setter = Object.getOwnPropertyDescriptor(input.tagName === "SELECT" ? dom.HTMLSelectElement.prototype : input.tagName === "TEXTAREA" ? dom.HTMLTextAreaElement.prototype : dom.HTMLInputElement.prototype, "value").set;
    setter.call(input, value); input.dispatchEvent(new dom.Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  }); await settle();
}
async function combined() { await mount(); await upload("Source CSV", ["source.csv"]); await upload("Additional CSVs", ["same.csv", "same.csv"]); }

 test("discovered CSV actions, required uploads, no browser file reading, and header-driven filters", async () => {
  await mount(); assert.equal(document.querySelectorAll("select[aria-label='CSV action'] option").length, 3);
  assert.equal(button("Combine files").disabled, true);
  await upload("Source CSV", ["source.csv"]); assert.equal(calls.filter((item) => item.url.endsWith("/inspect")).length, 0);
  await upload("Additional CSVs", ["append.csv"]); assert.equal(button("Combine files").disabled, false);
  await click(button("Add condition")); assert.deepEqual([...document.querySelector('[aria-label="Column 1"]').options].map((item) => item.value), ["ID","Customer"]);
  assert.equal(document.querySelector('input[type="checkbox"]').checked, false);
 });

 test("append files with duplicate names, add more, reorder and remove individual entries", async () => {
  await combined(); await upload("Additional CSVs", ["last.csv"]);
  const up = document.querySelector('[aria-label="Move last.csv 4 up"]'); await click(up);
  const reordered = calls.find((item) => item.url.endsWith("/reorder"));
  assert.deepEqual(JSON.parse(reordered.options.body).order, [0,1,3,2]);
  assert.equal(calls.filter((item) => item.url.endsWith("/inspect")).length, 2);
  await click(document.querySelector('[aria-label="Remove same.csv 4"]'));
  assert.deepEqual(calls.filter((item) => item.url.endsWith("/inspect")).at(-1).options.body.getAll("combine_additional").map((file) => file.name), ["same.csv","last.csv"]);
  assert.ok(calls.some((item) => item.url.endsWith("/discard")));
 });

 test("combine then filter passes structured options and downloads full result through same origin", async () => {
  await combined(); await click(button("Add condition")); await set("Column 1", "Customer"); await set("Operator 1", "contains"); await set("Value 1", ".");
  await click(button("Combine files"));
  const processed = JSON.parse(calls.find((item) => item.url.endsWith("/csv/runs")).options.body);
  assert.equal(processed.session_id, "session-1"); assert.equal(processed.options.remove_duplicates, true);
  assert.deepEqual(processed.options.conditions[0], { column: "Customer", kind: "text", operator: "contains", value: ".", upper: null, values: [], ignore_case: false, date_format: "YYYY-MM-DD" });
  assert.ok(document.querySelector('a[href="/forge-api/api/runs/run-1/outputs/csv_result/download/csv"]'));
  assert.match(document.body.textContent, /Final row count249/);
  await set("Value 1", "new"); assert.equal(document.querySelector('a[href$="/download/csv"]'), null);
  assert.equal(calls.filter((item) => item.url.endsWith("/inspect")).length, 1);
 });

 test("filter requires a condition, retains repeated rows by default and supports OR and ignore case", async () => {
  await mount("filter_csv"); await upload("Source CSV", ["filter.csv"]); assert.equal(button("Apply filters").disabled, true);
  await click(button("Add condition")); await set("Value 1", "A"); await set("Match conditions", "any");
  await click([...document.querySelectorAll('input[type="checkbox"]')][0]); await click(button("Apply filters"));
  const options = JSON.parse(calls.find((item) => item.url.endsWith("/csv/runs")).options.body).options;
  assert.equal(options.match, "any"); assert.equal(options.remove_duplicates, false); assert.equal(options.conditions[0].ignore_case, true);
 });

 test("numeric/date invalid values gate submit; blank and multi-value conditions have appropriate controls", async () => {
  await combined(); await click(button("Add condition")); await set("Comparison type 1", "number");
  assert.equal(button("Combine files").disabled, true); await set("Value 1", "1,000"); assert.equal(button("Combine files").disabled, true);
  await set("Value 1", "+1.00"); assert.equal(button("Combine files").disabled, false);
  await set("Comparison type 1", "date"); await set("Value 1", "2026-02-30"); assert.equal(button("Combine files").disabled, true);
  await set("Value 1", "2026-02-28"); assert.equal(button("Combine files").disabled, false);
  await set("Comparison type 1", "blank"); assert.equal(document.querySelector('[aria-label="Value 1"]'), null);
  await set("Comparison type 1", "text"); await set("Operator 1", "is_any_of"); await set("Selected value 1.1", "A,B"); await click(button("Add text value")); await set("Selected value 1.2", "Château");
  await click(button("Combine files"));
  assert.deepEqual(JSON.parse(calls.find((item) => item.url.endsWith("/csv/runs")).options.body).options.conditions[0].values, ["A,B","Château"]);
 });

 test("zero matches still offer download, paginated preview and accurate metrics", async () => {
  handler = (url) => url.endsWith("/csv/runs") ? { ...manifest, metrics: { ...manifest.metrics, output_rows: 0, rows_excluded: 249, effective_options: { remove_duplicates: true, conditions: [{}] } } } : null;
  await combined(); await click(button("Combine files"));
  assert.match(document.body.textContent, /No matching rows/); assert.match(document.body.textContent, /Rows excluded by filters249/);
  assert.ok(document.querySelector('a[href$="/download/csv"]'));
 });

 test("processing locks inputs and prevents duplicate clicks", async () => {
  let release; handler = (url) => url.endsWith("/csv/runs") ? new Promise((resolve) => { release = resolve; }) : null;
  await combined(); await act(async () => { button("Combine files").click(); button("Combine files").click(); });
  assert.equal(button("Processing CSV…").disabled, true); assert.equal(document.querySelector('[aria-label="CSV action"]').disabled, true);
  assert.ok([...document.querySelectorAll('input[type="file"]')].every((input) => input.disabled));
  assert.equal(calls.filter((item) => item.url.endsWith("/csv/runs")).length, 1);
  await act(async () => release(manifest)); await settle();
 });

 test("correctable processing failure preserves files/options and retries prepared data", async () => {
  let attempts = 0; handler = (url) => url.endsWith("/csv/runs") && attempts++ === 0 ? new Response(JSON.stringify({ error: { code: "INVALID_REQUEST", message: "Unreadable column Customer", details: {} } }), { status: 400 }) : null;
  await combined(); await click(button("Add condition")); await set("Value 1", "A"); await click(button("Combine files"));
  assert.match(document.body.textContent, /Unreadable column/); assert.equal(document.querySelector('[aria-label="Value 1"]').value, "A");
  await click(button("Retry processing")); assert.ok(document.querySelector('a[href$="/download/csv"]'));
  assert.equal(calls.filter((item) => item.url.endsWith("/inspect")).length, 1);
 });

 test("inspection failure and expired sessions can retry with selected files", async () => {
  let count = 0; handler = (url) => url.endsWith("/inspect") && count++ === 0 ? new Response(JSON.stringify({ error: { code: "INVALID_REQUEST", message: "Wrong headers", details: {} } }), { status: 400 }) : null;
  await combined(); assert.match(document.body.textContent, /Wrong headers/); await click(button("Retry inspection"));
  assert.equal(button("Combine files").disabled, false);
  handler = (url) => url.endsWith("/csv/runs") ? new Response(JSON.stringify({ error: { code: "INVALID_REQUEST", message: "CSV session expired or backend restarted", details: {} } }), { status: 400 }) : null;
  await click(button("Combine files")); assert.equal(button("Combine files").disabled, true); assert.ok(button("Retry inspection"));
 });

 test("late inspection response cannot overwrite changed files and is explicitly released", async () => {
  let release;
  handler = (url) => url.endsWith("/inspect") && !release ? new Promise((resolve) => { release = resolve; }) : null;
  await combined(); await upload("Source CSV", ["new-source.csv"]);
  await act(async () => release({ session_id: "obsolete", columns: ["OLD"], rows_received: 1 })); await settle();
  await click(button("Add condition")); assert.deepEqual([...document.querySelector('[aria-label="Column 1"]').options].map((item) => item.value), ["ID","Customer"]);
  assert.ok(calls.some((item) => item.url.endsWith("/csv/discard") && JSON.parse(item.options.body).session_id === "obsolete"));
 });

 test("changing files/order/action invalidates old result and releases Run", async () => {
  await combined(); await click(button("Combine files")); assert.ok(document.querySelector('a[href$="/download/csv"]'));
  await click(document.querySelector('[aria-label="Move same.csv 3 up"]')); assert.equal(document.querySelector('a[href$="/download/csv"]'), null);
  assert.ok(calls.some((item) => item.url.includes('/runs/run-1/discard')));
  await set("CSV action", "filter_csv"); assert.equal(document.querySelectorAll('.csv-file-row').length, 0);
 });

 test("unsupported files and removed conditions disable processing", async () => {
  await mount("filter_csv"); await upload("Source CSV", ["filter.xlsx"]);
  assert.match(document.body.textContent, /Choose CSV files only/); assert.equal(calls.filter((item) => item.url.endsWith('/inspect')).length, 0);
  await upload("Source CSV", ["filter.csv"]); await click(button("Add condition")); await click(button("Remove condition 1")); assert.equal(button("Apply filters").disabled, true);
 });

 test("reinspection after expiry keeps configured filters and Clear files releases preparation", async () => {
  await combined(); await click(button("Add condition")); await set("Value 1", "retained");
  handler = (url) => url.endsWith("/csv/runs") ? new Response(JSON.stringify({ error: { code: "INVALID_REQUEST", message: "CSV session expired", details: {} } }), { status: 400 }) : null;
  await click(button("Combine files")); handler = () => null; await click(button("Retry inspection"));
  assert.equal(document.querySelector('[aria-label="Value 1"]').value, "retained");
  await click(button("Clear files and results")); assert.equal(document.querySelectorAll('.csv-file-row').length, 0);
  assert.equal(button("Combine files").disabled, true);
 });

 test("too many additional files are stopped before inspection and recover by removing a file", async () => {
  await mount(); await upload("Source CSV", ["source.csv"]); await upload("Additional CSVs", Array.from({ length: 20 }, (_, n) => `${n}.csv`));
  assert.match(document.body.textContent, /Too many files/); assert.equal(button("Combine files").disabled, true);
  assert.equal(calls.filter((item) => item.url.endsWith('/inspect')).length, 0);
  await click(document.querySelector('[aria-label="Remove 19.csv 21"]')); assert.equal(button("Combine files").disabled, false);
 });

 test("reversed numeric/date endpoints disable submission without rounding large numbers", async () => {
  await combined(); await click(button("Add condition")); await set("Comparison type 1", "number"); await set("Operator 1", "between");
  await set("Value 1", "1000000000000000000000000000"); await set("Upper value 1", "999999999999999999999999999");
  assert.equal(button("Combine files").disabled, true);
  await set("Upper value 1", "1e27"); assert.equal(button("Combine files").disabled, false);
  await set("Comparison type 1", "date"); await set("Operator 1", "between"); await set("Date format 1", "DD/MM/YYYY");
  await set("Value 1", "02/01/2026"); await set("Upper value 1", "01/01/2026"); assert.equal(button("Combine files").disabled, true);
  await set("Upper value 1", "03/01/2026"); assert.equal(button("Combine files").disabled, false);
 });
