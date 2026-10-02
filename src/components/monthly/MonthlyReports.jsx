"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, discardMonthlyValidation, fetchMonthlyCatalog, generateMonthlyReports, runArtifactsZipUrl, validateMonthlyUploads, validateSavedReports } from "@/lib/api";
import FileUploadSlot from "@/components/workbench/FileUploadSlot";
import ArtifactDownloads from "@/components/workbench/ArtifactDownloads";
import AuditSummary from "@/components/workbench/AuditSummary";
import DataPreview from "@/components/workbench/DataPreview";
import ReleaseRun from "@/components/workbench/ReleaseRun";
import HistoryImporter from "./HistoryImporter";
import Review, { buttonClass, controlClass, DateFormat, ErrorNotice, panelClass, WarningConsent } from "./Review";

export default function MonthlyReports() {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(null);
  const [period, setPeriod] = useState("");
  const [mode, setMode] = useState("uploads");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchMonthlyCatalog({ signal: controller.signal }).then((loaded) => {
      setCatalog(loaded); setPeriod(loaded.default_period);
    }).catch((cause) => { if (!controller.signal.aborted) setError(cause); });
    return () => controller.abort();
  }, []);

  async function refresh() {
    try { setCatalog(await fetchMonthlyCatalog()); setError(null); }
    catch (cause) { setError(cause); }
  }

  if (!catalog) return <div className="flex flex-col gap-3"><ErrorNotice error={error} />
    {error ? <button className={buttonClass} onClick={async () => { const loaded = await fetchMonthlyCatalog().catch((cause) => { setError(cause); return null; }); if (loaded) { setCatalog(loaded); setPeriod(loaded.default_period); setError(null); } }}>Retry connection</button> : <p className="text-sm text-zinc-500">Loading saved reporting periods…</p>}
  </div>;

  return <main className="flex flex-col gap-6">
    <ErrorNotice error={error} />
    <HistoryImporter onSaved={refresh} disabled={busy} onBusy={setBusy} />
    <section className={panelClass} aria-label="Choose reporting cycle">
      <div className="flex flex-wrap gap-2">
        <button className={buttonClass} aria-pressed={mode === "uploads"} disabled={busy} onClick={() => setMode("uploads")}>Upload monthly sources</button>
        <button className={buttonClass} aria-pressed={mode === "saved"} disabled={busy} onClick={() => setMode("saved")}>Rerun saved reports</button>
      </div>
      <label className="flex flex-col gap-2 text-sm">Reporting period
        <input type="month" min="0003-01" className={controlClass} value={period} onChange={(event) => setPeriod(event.target.value)} disabled={busy} />
      </label>
      {catalog.periods.length ? <label className="flex flex-col gap-2 text-sm">Choose a stored period
        <select className={controlClass} value={catalog.periods.some((item) => item.period === period) ? period : ""} disabled={busy} onChange={(event) => setPeriod(event.target.value)}>
          <option value="" disabled>Select a month</option>
          {catalog.periods.map((item) => <option key={item.period} value={item.period}>{item.period}{item.ready ? " · sources saved" : " · some sources missing"}</option>)}
        </select>
      </label> : null}
      <button className={`${buttonClass} self-start`} onClick={refresh} disabled={busy}>Refresh stored periods</button>
    </section>
    <Cycle key={`${mode}-${period}`} catalog={catalog} period={period} mode={mode} onSaved={refresh} onBusy={setBusy} disabled={busy} />
    <p className="text-xs text-zinc-500">Source versions and cycle records survive a backend restart. Downloads and previews are held in memory; rerun a saved cycle to recreate them. Calendar coverage does not prove that every invoice or credit was supplied.</p>
  </main>;
}

function Cycle({ catalog, period, mode, onSaved, onBusy, disabled }) {
  const [files, setFiles] = useState({});
  const [dates, setDates] = useState({});
  const [replacing, setReplacing] = useState({});
  const [reason, setReason] = useState("");
  const savedPeriod = catalog.periods.find((item) => item.period === period);
  const [cycleId, setCycleId] = useState(savedPeriod?.cycles[0]?.cycle_id ?? "");
  const [review, setReview] = useState(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [outputId, setOutputId] = useState("company_summary");
  const working = useRef(false);
  const validation = useRef(null);
  const blocked = Boolean(busy) || disabled;

  useEffect(() => () => {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
  }, []);

  function change(update) {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
    validation.current = null;
    update(); setReview(null); setOutcome(null); setError(null); setAcknowledged(false);
  }

  async function perform(operation, retryReceipt) {
    if (working.current) return;
    working.current = true; setBusy(operation); onBusy(true); setError(null);
    try {
      if (operation === "generate") {
        const result = await generateMonthlyReports({ validation_id: review.validation_id, acknowledge_warnings: acknowledged });
        validation.current = null; setOutcome(result); setReview(null);
        // Files are already persisted. Retrying uses the recorded source IDs.
        setFiles({}); setReplacing({}); setReason("");
        await onSaved();
      } else {
        validation.current = null; setReview(null); setAcknowledged(false); setOutcome(null);
        let result;
        if (mode === "saved" || retryReceipt) {
          result = await validateSavedReports({ period, cycle_id: retryReceipt?.cycle_id || cycleId || null });
        } else {
          const form = new FormData(); form.append("period", period);
          for (const [id, file] of Object.entries(files)) if (file) {
            form.append(id, file);
            if (dates[id]) form.append(`${id}.date_format`, dates[id]);
            if (replacing[id]) form.append(`${id}.replaces`, replacing[id]);
          }
          if (reason.trim()) form.append("reason", reason.trim());
          result = await validateMonthlyUploads(form);
        }
        validation.current = result.validation_id; setReview(result); setAcknowledged(false); setOutcome(null);
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError("This reporting step could not be completed."));
      if (operation === "generate") {
        validation.current = null; setReview(null);
        await onSaved();
      }
    } finally { working.current = false; setBusy(""); onBusy(false); }
  }

  return <div className="flex flex-col gap-6" aria-busy={Boolean(busy)}>
    <section className={panelClass}>
      <h2 className="text-lg font-semibold">{mode === "uploads" ? `Sources for ${period || "your reporting month"}` : `Saved reports for ${period || "your reporting month"}`}</h2>
      {mode === "uploads" ? <>
        {catalog.datasets.map((dataset) => {
          const saved = dataset.versions.find((item) => item.period === period);
          return <div key={dataset.id} className="flex flex-col gap-3 border-b border-zinc-100 pb-4 last:border-0 dark:border-zinc-800">
            {saved ? <p className="text-xs text-zinc-500">Saved: {saved.source_filename} · {saved.row_count.toLocaleString()} rows. Leave this slot empty to reuse it.</p> : null}
            <FileUploadSlot input={{ label: dataset.label, accepted_extensions: [".csv", ".xlsx"], required: !saved }} file={files[dataset.id]} disabled={blocked} onSelect={(file) => change(() => setFiles((previous) => ({ ...previous, [dataset.id]: file })))} onRemove={() => change(() => { setFiles((previous) => ({ ...previous, [dataset.id]: null })); setReplacing((previous) => ({ ...previous, [dataset.id]: null })); })} />
            {files[dataset.id] && dataset.id !== "account_assignments" ? <DateFormat value={dates[dataset.id] ?? ""} onChange={(value) => change(() => setDates((previous) => ({ ...previous, [dataset.id]: value })))} disabled={blocked} label={`${dataset.label} date format`} /> : null}
            {files[dataset.id] && saved ? <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={Boolean(replacing[dataset.id])} disabled={blocked} onChange={(event) => { const selected = event.target.checked; change(() => setReplacing((previous) => ({ ...previous, [dataset.id]: selected ? saved.version_id : null }))); }} />
              <span>Correct this saved month by replacing version <span className="break-all font-mono text-xs">{saved.version_id}</span></span>
            </label> : null}
            <details className="text-xs text-zinc-500"><summary className="cursor-pointer">Required columns</summary><p className="mt-2">{dataset.required_columns.join(", ")}</p></details>
          </div>;
        })}
        {Object.values(replacing).some(Boolean) ? <label className="flex flex-col gap-2 text-sm">Correction reason
          <textarea className={controlClass} value={reason} onChange={(event) => change(() => setReason(event.target.value))} disabled={blocked} placeholder="Explain what changed in the replacement source." />
        </label> : null}
      </> : <>
        <p className="text-sm text-zinc-500">No uploads needed. Choose the exact sources from a previous cycle, or deliberately capture the current stored versions.</p>
        <label className="flex flex-col gap-2 text-sm">Source selection
          <select className={controlClass} value={cycleId} disabled={blocked} onChange={(event) => change(() => setCycleId(event.target.value))}>
            {(savedPeriod?.cycles ?? []).map((cycle) => <option key={cycle.cycle_id} value={cycle.cycle_id}>{new Date(cycle.created_at).toLocaleString()} · exact recorded sources · Action {cycle.action.version}</option>)}
            <option value="">Current stored versions · record a new cycle</option>
          </select>
        </label>
        {!savedPeriod?.ready ? <p className="text-sm text-amber-700 dark:text-amber-300">This period is missing one or more saved sources. Validation will list what is needed.</p> : null}
      </>}
      <button className={`${buttonClass} self-start`} disabled={blocked || !period} onClick={() => perform("validate")}>{busy === "validate" ? "Validating…" : "Validate sources"}</button>
    </section>
    <ErrorNotice error={error} />
    {review ? <Review review={review} /> : null}
    {review?.ready ? <div className="flex flex-col items-start gap-3">
      {review.warnings.length ? <WarningConsent checked={acknowledged} onChange={setAcknowledged} disabled={blocked} /> : null}
      <button className={buttonClass} disabled={blocked || (review.warnings.length > 0 && !acknowledged)} onClick={() => perform("generate")}>{busy === "generate" ? "Saving sources and generating…" : "Generate reports"}</button>
    </div> : null}
    {outcome ? <section className={panelClass} aria-label="Reporting result">
      <div role="status" className="space-y-2">
        <p className="font-medium">Sources {outcome.sources_committed ? "saved" : "partially saved or not saved"} · {outcome.status === "reports_generated" ? "Reports generated" : "Reports not generated"}</p>
        {Object.keys(outcome.committed_versions).length ? <p className="text-xs text-zinc-500">New versions committed: {Object.keys(outcome.committed_versions).join(", ")}</p> : null}
      </div>
      <ErrorNotice error={outcome.error} />
      {outcome.receipt ? <details className="text-xs text-zinc-500"><summary className="cursor-pointer">Recorded source versions</summary><pre className="mt-2 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(outcome.receipt, null, 2)}</pre></details> : null}
      {outcome.status !== "reports_generated" ? <>
        <p className="text-sm">Committed sources remain available. {outcome.receipt ? "Review the exact saved sources, then retry generation." : "Refresh stored periods, supply any missing source, and validate again."}</p>
        {outcome.receipt ? <button className={`${buttonClass} self-start`} disabled={blocked} onClick={() => perform("validate", outcome.receipt)}>Review saved sources to retry</button> : null}
      </> : <>
        <a className={`${buttonClass} self-start`} href={runArtifactsZipUrl({ runId: outcome.manifest.run_id })}>Download {period} reports ZIP</a>
        <ArtifactDownloads runId={outcome.manifest.run_id} artifacts={outcome.manifest.artifacts} />
        <label className="flex flex-col gap-2 text-sm">Spot-check a result
          <select className={controlClass} value={outputId} onChange={(event) => setOutputId(event.target.value)}>
            {outcome.manifest.outputs.map((output) => <option key={output.id} value={output.id}>{output.label}</option>)}
          </select>
        </label>
        <DataPreview key={`${outcome.manifest.run_id}-${outputId}`} runId={outcome.manifest.run_id} outputId={outputId} />
        <AuditSummary manifest={outcome.manifest} />
        <ReleaseRun runId={outcome.manifest.run_id} onReleased={() => setOutcome(null)} disabled={blocked} />
      </>}
    </section> : null}
  </div>;
}
