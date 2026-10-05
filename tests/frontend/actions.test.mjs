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
const output = path.join(rootPath, "node_modules/.cache/forgexl-actions-test/actions.cjs");
await build({ entryPoints: ["src/components/workbench/ActionRunner.jsx"], absWorkingDir: rootPath,
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", outfile: output,
  alias: { "@": path.join(rootPath, "src") }, external: ["react", "react/jsx-runtime"],
  plugins: [{ name: "link-dom", setup(builder) {
    builder.onResolve({ filter: /^next\/link$/ }, () => ({ path: "link", namespace: "test" }));
    builder.onLoad({ filter: /.*/, namespace: "test" }, () => ({ contents: 'import React from "react"; export default function Link(props) { return React.createElement("a", props); }' }));
  } }] });
const ActionRunner = createRequire(import.meta.url)(output).default;
const slot = (id) => ({ id, label: id, source: "upload", required: true,
  accepted_extensions: [".csv", ".xlsx"], required_columns: ["Customer"] });
const actions = [
  { id: "generic", name: "Clean duplicate rows", description: "Keep one of each exact row.", inputs: [slot("source_file")] },
  { id: "future", name: "Compare two files", description: "A newly registered action.", inputs: [slot("first"), slot("second")] },
  { id: "monthly", name: "Monthly sales rep reports", description: "Reports for every rep.", inputs: [], workflow_path: "/monthly-reports" },
];
const manifest = { run_id: "run-1", action: actions[0], duration_ms: 10,
  audit: { rows_received: 3, rows_returned: 2 }, validation: { warnings: [] }, metrics: {}, artifacts: [],
  outputs: [{ id: "cleaned", label: "Cleaned rows", row_count: 2, input_row_count: 3, column_count: 1, formats: ["xlsx", "csv"] }] };
let dom, root, calls, handler;
const originalFetch = globalThis.fetch;
beforeEach(() => {
  dom = new Window({ url: "http://localhost/" });
  globalThis.window = dom; globalThis.document = dom.document;
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  calls = []; handler = () => null;
  globalThis.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const custom = await handler(url, options);
    const payload = custom ?? (url.endsWith("/actions") ? { actions } : url.includes("/preview") ?
      { columns: ["Customer"], rows: [["A"]], total_rows: 2, offset: 0 } : manifest);
    return payload instanceof Response ? payload : new Response(JSON.stringify(payload), { status: 200 });
  };
  document.body.innerHTML = "<div id='root'></div>";
  root = createRoot(document.getElementById("root"));
});
afterEach(async () => {
  await act(async () => root.unmount()); await dom.happyDOM.close();
  globalThis.fetch = originalFetch; delete globalThis.window; delete globalThis.document;
});
after(async () => { await initialDom.happyDOM.close(); await fs.rm(path.dirname(output), { recursive: true, force: true }); });
async function settle() { await act(async () => new Promise((resolve) => setTimeout(resolve, 15))); }
async function mount() { await act(async () => root.render(React.createElement(ActionRunner))); await settle(); }
function button(text) {
  const found = [...document.querySelectorAll("button")].find((element) => element.textContent.replace(/[→↓]/g, "").trim() === text);
  assert.ok(found, `Missing button ${text}`); return found;
}
async function click(element) { await act(async () => element.click()); await settle(); }
async function choose(name) { await click([...document.querySelectorAll("button.action-card")].find((item) => item.textContent.includes(name))); }
async function upload(label, filename = "source.csv") {
  const input = document.querySelector(`input[aria-label="${label}"]`);
  Object.defineProperty(input, "files", { configurable: true, value: [new File(["Customer\nA\n"], filename)] });
  await act(async () => input.dispatchEvent(new dom.Event("change", { bubbles: true })));
}

test("start with action cards and route the monthly card to its workflow", async () => {
  await mount();
  assert.equal(document.querySelectorAll('input[type="file"]').length, 0);
  assert.equal(document.querySelectorAll(".action-card").length, 3);
  assert.equal(document.querySelector(".action-card").getAttribute("href"), "/monthly-reports");
  assert.equal(document.querySelector(".primary-button"), null);
});

test("required upload unlocks one generate button and returns download links", async () => {
  await mount(); await choose("Clean duplicate rows");
  assert.equal(button("Generate report").disabled, true);
  assert.equal(document.querySelectorAll(".action-card").length, 0);
  await upload("source_file"); await click(button("Generate report"));
  const request = calls.find((item) => item.url.endsWith("/runs"));
  assert.equal(request.options.body.get("action_id"), "generic");
  assert.equal(request.options.body.get("source_file").name, "source.csv");
  assert.match(document.body.textContent, /Your report is ready/);
  assert.ok(document.querySelector('a[href$="/download/xlsx"]'));
  assert.equal([...document.querySelectorAll("details")].find((item) => item.textContent.includes("Run details")).open, false);
  await click(button("Change action"));
  assert.equal(document.querySelectorAll(".action-card").length, 3);
  assert.equal(document.querySelector('a[href$="/download/xlsx"]'), null);
});

test("new action metadata renders both required inputs and resets previous files", async () => {
  await mount(); await choose("Clean duplicate rows"); await upload("source_file");
  await click(button("Change action")); await choose("Compare two files");
  assert.equal(document.querySelectorAll('input[type="file"]').length, 2);
  assert.equal(button("Generate report").disabled, true);
  await upload("first"); assert.equal(button("Generate report").disabled, true);
  await upload("second"); assert.equal(button("Generate report").disabled, false);
});

test("generating locks uploads and action changes and prevents double submission", async () => {
  let release;
  handler = (url) => url.endsWith("/runs") ? new Promise((resolve) => { release = resolve; }) : null;
  await mount(); await choose("Clean duplicate rows"); await upload("source_file");
  await act(async () => { button("Generate report").click(); button("Generate report").click(); });
  assert.equal(button("Generating your report…").disabled, true);
  assert.equal(button("Change action").disabled, true);
  assert.equal(document.querySelector('input[type="file"]').disabled, true);
  assert.equal(calls.filter((item) => item.url.endsWith("/runs")).length, 1);
  await act(async () => release(manifest)); await settle();
});

test("catalog connection failure offers retry and restores usable action cards", async () => {
  let attempts = 0;
  handler = (url) => { if (url.endsWith("/actions") && attempts++ === 0) throw new TypeError("Disconnected"); return null; };
  await mount(); assert.match(document.body.textContent, /Actions Unavailable/);
  await click(button("Retry connection"));
  assert.equal(document.querySelectorAll(".action-card").length, 3);
});
