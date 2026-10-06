"use client";

import { useEffect, useRef, useState } from "react";
import FilterCondition, { conditionValid, newCondition } from "@/components/csv/FilterCondition";
import DataPreview from "@/components/workbench/DataPreview";
import AuditSummary from "@/components/workbench/AuditSummary";
import ReleaseRun from "@/components/workbench/ReleaseRun";
import { fetchActions, inspectCSV, processCSV, reorderCSV, discardCSV, discardRun, outputDownloadUrl } from "@/lib/api";

export default function CSVTools({ initialActionId = "" }) {
  const [actions, setActions] = useState([]);
  const [actionId, setActionId] = useState(initialActionId);
  const [catalogStatus, setCatalogStatus] = useState("loading");
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [files, setFiles] = useState({});
  const [inspection, setInspection] = useState(null);
  const [inspectStatus, setInspectStatus] = useState("idle");
  const [inspectAttempt, setInspectAttempt] = useState(0);
  const [conditions, setConditions] = useState([]);
  const [match, setMatch] = useState("all");
  const [deduplicate, setDeduplicate] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(false);
  const busy = useRef(false);
  const session = useRef(null);
  const preparedFiles = useRef([]);
  const lastRun = useRef(null);
  const processController = useRef(null);
  const generation = useRef(0);

  const action = actions.find((item) => item.id === actionId);
  const combine = action?.inputs.some((slot) => slot.max_files > 1) ?? false;
  const selectedFiles = action?.inputs.flatMap((slot) => (files[slot.id] ?? [])) ?? [];
  const filesReady = !!action && action.inputs.every((slot) => (!slot.required || files[slot.id]?.length) && (files[slot.id]?.length ?? 0) <= slot.max_files);
  const invalidFile = selectedFiles.some((file) => !file.name.toLowerCase().endsWith(".csv"));
  const canProcess = inspectStatus === "ready" && inspection && !running && !invalidFile && (combine || conditions.length > 0) && conditions.every(conditionValid);

  useEffect(() => {
    const controller = new AbortController();
    fetchActions({ signal: controller.signal }).then((loaded) => {
      if (controller.signal.aborted) return;
      setActions(loaded.filter((item) => item.workflow_path?.startsWith("/csv-tools?")));
      setCatalogStatus("ready");
    }).catch((cause) => { if (!controller.signal.aborted) { setError(cause.message); setCatalogStatus("error"); } });
    return () => controller.abort();
  }, [catalogAttempt]);

  useEffect(() => {
    if (!action || !filesReady || invalidFile) return;
    const controller = new AbortController();
    const revision = generation.current;
    const data = new FormData();
    data.append("action_id", action.id);
    action.inputs.forEach((slot) => (files[slot.id] ?? []).forEach((file) => data.append(slot.id, file)));
    const ordered = action.inputs.flatMap((slot) => files[slot.id] ?? []);
    const canReuse = session.current && ordered.length === preparedFiles.current.length && ordered.every((file) => preparedFiles.current.includes(file));
    const operation = canReuse ? reorderCSV({ session_id: session.current, order: ordered.map((file) => preparedFiles.current.indexOf(file)) }, { signal: controller.signal }) : inspectCSV(data, { signal: controller.signal });
    operation.then((prepared) => {
      if (controller.signal.aborted || revision !== generation.current) { discardCSV(prepared.session_id).catch(() => {}); return; }
      session.current = prepared.session_id;
      preparedFiles.current = ordered;
      setInspection(prepared); setInspectStatus("ready"); setError(null);
    }).catch((cause) => { if (!controller.signal.aborted && revision === generation.current) { setInspectStatus("error"); setError(cause.message); } });
    return () => controller.abort();
  }, [action, files, filesReady, invalidFile, inspectAttempt]);

  useEffect(() => () => {
    generation.current += 1;
    processController.current?.abort();
    if (session.current) discardCSV(session.current).catch(() => {});
    if (lastRun.current) discardRun(lastRun.current).catch(() => {});
  }, []);

  function invalidateResult() {
    if (lastRun.current) { discardRun(lastRun.current).catch(() => {}); lastRun.current = null; }
    setResult(null); setError(null);
  }
  function invalidateFiles(keepPrepared = false) {
    generation.current += 1;
    if (!keepPrepared && session.current) { discardCSV(session.current).catch(() => {}); session.current = null; preparedFiles.current = []; }
    setInspection(null); if (!keepPrepared) setConditions([]); invalidateResult();
  }
  function updateFiles(next) {
    const ordered = action.inputs.flatMap((slot) => next[slot.id] ?? []);
    const orderOnly = inspectStatus === "ready" && session.current && ordered.length === preparedFiles.current.length && ordered.every((file) => preparedFiles.current.includes(file));
    invalidateFiles(Boolean(orderOnly)); setFiles(next);
    const withinCount = action.inputs.every((slot) => (next[slot.id]?.length ?? 0) <= slot.max_files);
    const ready = withinCount && action.inputs.every((slot) => !slot.required || next[slot.id]?.length);
    const valid = Object.values(next).flat().every((file) => file.name.toLowerCase().endsWith(".csv"));
    setInspectStatus(ready && valid ? "inspecting" : "idle");
    if (!valid) setError("Choose CSV files only. Remove or replace the unsupported file.");
    else if (!withinCount) setError("Too many files. Remove files to meet the displayed limit.");
  }
  function retryInspection() {
    generation.current += 1;
    if (session.current) { discardCSV(session.current).catch(() => {}); session.current = null; }
    setInspection(null); invalidateResult(); setInspectStatus("inspecting"); setInspectAttempt((attempt) => attempt + 1);
  }
  function changeAction(id) {
    invalidateFiles(); setFiles({}); setActionId(id); setInspectStatus("idle"); setDeduplicate(false); setMatch("all");
  }
  function editFilters(callback) { invalidateResult(); callback(); }

  async function process() {
    if (!canProcess || busy.current) return;
    busy.current = true; setRunning(true); invalidateResult();
    const controller = new AbortController(); processController.current = controller;
    const revision = generation.current;
    try {
      const manifest = await processCSV({ action_id: action.id, session_id: inspection.session_id, options: { match, remove_duplicates: combine || deduplicate, conditions } }, { signal: controller.signal });
      if (controller.signal.aborted || revision !== generation.current) { discardRun(manifest.run_id).catch(() => {}); return; }
      lastRun.current = manifest.run_id; setResult(manifest);
    } catch (cause) {
      if (!controller.signal.aborted) {
        setError(cause.message);
        if (/expired|restarted|released/.test(cause.message)) setInspectStatus("error");
      }
    } finally { busy.current = false; if (!controller.signal.aborted) setRunning(false); }
  }

  if (catalogStatus === "loading") return <p>Loading CSV actions…</p>;
  if (catalogStatus === "error") return <div role="alert"><p>{error}</p><button className="secondary-button" onClick={() => { setCatalogStatus("loading"); setCatalogAttempt((attempt) => attempt + 1); }}>Retry connection</button></div>;

  return <div className="flex flex-col gap-6" aria-busy={running}>
    <section className="workflow-panel"><label>Choose an action<select className="form-control mt-2" aria-label="CSV action" value={actionId} disabled={running} onChange={(event) => changeAction(event.target.value)}><option value="">Choose…</option>{actions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label></section>
    {action ? <>
      <section className="workflow-panel space-y-4"><h2>Upload files</h2><p className="text-sm">{action.description}</p>
        {action.inputs.map((slot) => <div key={slot.id}>
          <label>{slot.label}<input className="form-control mt-2" type="file" accept=".csv,text/csv" multiple={slot.max_files > 1} aria-label={slot.label} disabled={running} onChange={(event) => { const chosen = [...event.target.files]; if (chosen.length) updateFiles({ ...files, [slot.id]: slot.max_files > 1 ? [...(files[slot.id] ?? []), ...chosen] : chosen }); event.target.value = ""; }} /></label>
          <ol className="mt-3 space-y-2">{(files[slot.id] ?? []).map((file, index) => <li key={index} className="csv-file-row"><span>{slot.max_files > 1 ? `${index + 2}. ` : "1. "}{file.name}</span><div className="flex flex-wrap gap-2">
            {slot.max_files > 1 ? <><button className="secondary-button" aria-label={`Move ${file.name} ${index + 2} up`} disabled={running || inspectStatus === "inspecting" || index === 0} onClick={() => { const ordered = [...files[slot.id]]; [ordered[index - 1], ordered[index]] = [ordered[index], ordered[index - 1]]; updateFiles({ ...files, [slot.id]: ordered }); }}>↑</button><button className="secondary-button" aria-label={`Move ${file.name} ${index + 2} down`} disabled={running || inspectStatus === "inspecting" || index === files[slot.id].length - 1} onClick={() => { const ordered = [...files[slot.id]]; [ordered[index + 1], ordered[index]] = [ordered[index], ordered[index + 1]]; updateFiles({ ...files, [slot.id]: ordered }); }}>↓</button></> : null}
            <button className="secondary-button" aria-label={`Remove ${file.name} ${slot.max_files > 1 ? index + 2 : 1}`} disabled={running} onClick={() => updateFiles({ ...files, [slot.id]: files[slot.id].filter((_, n) => n !== index) })}>Remove</button>
          </div></li>)}</ol>
          {slot.max_files > 1 ? <p className="text-xs mt-2">Select more files to add them. Source first, then this append order. Maximum {slot.max_files} additional files.</p> : null}
        </div>)}
        <p className="text-sm">UTF-8 CSV. Empty fields are blank; spaces remain text.</p>
        {selectedFiles.length ? <button className="secondary-button" disabled={running} onClick={() => { invalidateFiles(); setFiles({}); setInspectStatus("idle"); }}>Clear files and results</button> : null}
        {inspectStatus === "inspecting" ? <p role="status">Uploading and inspecting files…</p> : null}
        {inspection && inspectStatus === "ready" ? <p role="status">{inspection.rows_received.toLocaleString()} rows received · {inspection.columns.length} columns</p> : null}
      </section>
      {inspection && inspectStatus === "ready" ? <section className="workflow-panel space-y-4"><h2>{combine ? "Optional filters" : "Configure filters"}</h2>
        <label>Match<select className="form-control" aria-label="Match conditions" value={match} disabled={running} onChange={(event) => editFilters(() => setMatch(event.target.value))}><option value="all">Match all conditions</option><option value="any">Match any condition</option></select></label>
        {conditions.map((condition, index) => <FilterCondition key={index} condition={condition} index={index} columns={inspection.columns} disabled={running} onChange={(changed) => editFilters(() => setConditions(conditions.map((item, n) => n === index ? changed : item)))} onRemove={() => editFilters(() => setConditions(conditions.filter((_, n) => n !== index)))} />)}
        <button className="secondary-button" disabled={running || conditions.length >= 50} onClick={() => editFilters(() => setConditions([...conditions, newCondition(inspection.columns[0])]))}>Add condition</button>
        {!combine ? <label className="block"><input type="checkbox" checked={deduplicate} disabled={running} onChange={(event) => editFilters(() => setDeduplicate(event.target.checked))} /> Remove exact duplicate rows</label> : null}
        <p className="text-xs">Exact duplicates have equal parsed values in every column. First occurrence stays; whitespace, case and number text are preserved. Filters run after duplicate removal.</p>
        <p className="text-xs">Text comparisons include blank fields. Numeric/date comparisons exclude blanks, including “does not equal”; unreadable populated values stop processing.</p>
      </section> : null}
      {error ? <div role="alert" className="workflow-panel text-red-700"><p>{error}</p>{inspectStatus === "error" ? <button className="secondary-button mt-3" disabled={running} onClick={retryInspection}>Retry inspection</button> : canProcess ? <button className="secondary-button mt-3" onClick={process}>Retry processing</button> : null}</div> : null}
      <section className="workflow-panel"><button className="primary-button" disabled={!canProcess} onClick={process}>{running ? "Processing CSV…" : combine ? "Combine files" : "Apply filters"}</button><p className="generate-help">{!filesReady ? "Choose the required files above." : !combine && !conditions.length ? "Add a filter condition to continue." : "Preview and download the complete result."}</p></section>
      {result ? <section className="workflow-panel space-y-5" aria-live="polite"><h2>{result.metrics.output_rows === 0 ? "No matching rows" : "Your CSV is ready"}</h2><dl className="csv-metrics">
        <Metric label="Rows received" value={result.metrics.input_rows} />
        {result.metrics.effective_options.remove_duplicates ? <Metric label="Exact duplicates removed" value={result.metrics.duplicates_removed} /> : null}
        {result.metrics.effective_options.conditions.length ? <Metric label="Rows excluded by filters" value={result.metrics.rows_excluded} /> : null}
        <Metric label="Final row count" value={result.metrics.output_rows} />
      </dl><a className="primary-button block text-center" href={outputDownloadUrl({ runId: result.run_id, outputId: result.outputs[0].id, format: "csv" })}>Download CSV</a>
        <DataPreview key={result.run_id} runId={result.run_id} outputId={result.outputs[0].id} />
        <details className="advanced-options"><summary>Run details & cleanup</summary><div className="mt-4 space-y-4"><AuditSummary manifest={result} /><ReleaseRun runId={result.run_id} onReleased={() => { lastRun.current = null; setResult(null); }} /></div></details>
      </section> : null}
    </> : null}
  </div>;
}
function Metric({ label, value }) { return <div><dt>{label}</dt><dd>{value.toLocaleString()}</dd></div>; }
