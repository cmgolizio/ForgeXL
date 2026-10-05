"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, discardMonthlyValidation, fetchMonthlyCatalog, generateMonthlyReports, runArtifactsZipUrl, validateMonthlyUploads, validateSavedReports } from "@/lib/api";
import { fileExtension } from "@/lib/formatters";
import FileUploadSlot from "@/components/workbench/FileUploadSlot";
import ArtifactDownloads from "@/components/workbench/ArtifactDownloads";
import AuditSummary from "@/components/workbench/AuditSummary";
import DataPreview from "@/components/workbench/DataPreview";
import ReleaseRun from "@/components/workbench/ReleaseRun";
import WorkflowSteps, { StepHeading } from "@/components/workflow/WorkflowSteps";
import HistoryImporter from "./HistoryImporter";
import Review, { buttonClass, controlClass, DateFormat, ErrorNotice, WarningConsent } from "./Review";

export default function MonthlyReports() {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState(null);
  const loading = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    fetchMonthlyCatalog({ signal: controller.signal }).then(setCatalog).catch((cause) => {
      if (!controller.signal.aborted) setError(cause);
    });
    return () => controller.abort();
  }, []);

  async function refresh() {
    if (loading.current) return;
    loading.current = true;
    try { setCatalog(await fetchMonthlyCatalog()); setError(null); }
    catch (cause) { setError(cause); }
    finally { loading.current = false; }
  }

  if (!catalog) return <div className="workflow-panel space-y-4"><ErrorNotice error={error} />
    {error ? <button className={buttonClass} onClick={refresh}>Retry connection</button> : <p role="status">Loading your saved data…</p>}
  </div>;

  return <main><ErrorNotice error={error} /><Cycle catalog={catalog} onSaved={refresh} /></main>;
}

function Cycle({ catalog, onSaved }) {
  const [period, setPeriod] = useState(catalog.default_period);
  const [mode, setMode] = useState("uploads");
  const [files, setFiles] = useState({});
  const [dates, setDates] = useState({});
  const [replacing, setReplacing] = useState({});
  const [reason, setReason] = useState("");
  const [cycleId, setCycleId] = useState("");
  const [review, setReview] = useState(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState("");
  const [historyBusy, setHistoryBusy] = useState(false);
  const [error, setError] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const [outputId, setOutputId] = useState("company_summary");
  const working = useRef(false);
  const validation = useRef(null);
  const blocked = Boolean(busy) || historyBusy;
  const savedPeriod = catalog.periods.find((item) => item.period === period);
  const datasets = catalog.datasets.filter((dataset) => ["sales_history", "sample_history"].includes(dataset.id));
  const missing = mode === "uploads" ? datasets.filter((dataset) => !files[dataset.id] && !dataset.versions.some((version) => version.period === period)) : [];

  useEffect(() => () => {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
  }, []);

  function change(update) {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
    validation.current = null;
    update(); setReview(null); setOutcome(null); setError(null); setAcknowledged(false);
  }

  async function generate(reviewed, consent) {
    setBusy("generate");
    const result = await generateMonthlyReports({ validation_id: reviewed.validation_id, acknowledge_warnings: consent });
    validation.current = null; setOutcome(result); setReview(null);
    // A partial save keeps the files selected for safe same-file resumption.
    if (result.sources_committed) { setFiles({}); setReplacing({}); setReason(""); }
    await onSaved();
  }

  async function perform(retryReceipt) {
    if (working.current || historyBusy) return;
    working.current = true; setBusy("validate"); setError(null);
    try {
      if (review?.ready && !retryReceipt) {
        if (review.warnings.length && !acknowledged) return;
        await generate(review, acknowledged);
      } else {
        if (validation.current) await discardMonthlyValidation(validation.current);
        validation.current = null; setReview(null); setAcknowledged(false); setOutcome(null);
        let result;
        if (mode === "saved" || retryReceipt) {
          result = await validateSavedReports({ period, cycle_id: retryReceipt?.cycle_id || (cycleId === "current" ? null : cycleId || savedPeriod?.cycles?.[0]?.cycle_id || null) });
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
        validation.current = result.validation_id; setReview(result);
        if (result.ready && !result.warnings.length) await generate(result, false);
      }
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError("Your reports could not be generated. Try again."));
      validation.current = null; setReview(null);
      await onSaved();
    } finally { working.current = false; setBusy(""); }
  }

  function chooseFile(dataset, file) {
    change(() => setFiles((previous) => ({ ...previous, [dataset.id]: null })));
    if (![".csv", ".xlsx"].includes(fileExtension(file.name))) {
      setError(new ApiError(`${file.name} is not supported. Choose a CSV or Excel (.xlsx) file.`));
      return;
    }
    setFiles((previous) => ({ ...previous, [dataset.id]: file }));
  }

  const generated = outcome?.status === "reports_generated";
  return <div className="flex flex-col gap-6" aria-busy={Boolean(busy) || historyBusy}>
    <WorkflowSteps current={review || busy || outcome ? 3 : 2} />
    <section className="workflow-panel">
      <StepHeading number="2" title={mode === "uploads" ? "Add your sales and sample files" : "Use saved data"}>
        {mode === "uploads" ? "A single month or several years both work. Saved months are reused without double-counting." : "Generate another copy using the data you already saved."}
      </StepHeading>
      <div className="mb-6 flex flex-wrap items-end justify-between gap-5">
        <label className="flex w-full flex-col gap-2 text-sm font-medium sm:w-60">Report month
          <input type="month" min="0003-01" className={controlClass} value={period} onChange={(event) => change(() => { setPeriod(event.target.value); setReplacing({}); setReason(""); setCycleId(""); })} disabled={blocked} />
        </label>
        <div className="flex gap-2" aria-label="Source mode">
          <button className={buttonClass} aria-pressed={mode === "uploads"} disabled={blocked} onClick={() => change(() => setMode("uploads"))}>Upload files</button>
          <button className={buttonClass} aria-pressed={mode === "saved"} disabled={blocked} onClick={() => change(() => setMode("saved"))}>Use saved data</button>
        </div>
      </div>
      {mode === "uploads" ? <>
        <div className="upload-grid">
          {datasets.map((dataset) => {
            const saved = dataset.versions.find((item) => item.period === period);
            return <div key={dataset.id}>
              <FileUploadSlot input={{ id: dataset.id, label: dataset.id === "sales_history" ? "Sales data" : "Sample data", accepted_extensions: [".csv", ".xlsx"], required: !saved, description: dataset.id === "sales_history" ? "Company sales, including credits and returns." : "Sample transactions, including sample credits." }} file={files[dataset.id]} disabled={blocked} onSelect={(file) => chooseFile(dataset, file)} onRemove={() => change(() => { setFiles((previous) => ({ ...previous, [dataset.id]: null })); setReplacing((previous) => ({ ...previous, [dataset.id]: null })); })} />
              {saved ? <p className="saved-notice">✓ {saved.row_count.toLocaleString()} rows saved for this month. Upload only if you have new data.</p> : null}
              <details className="mt-4 text-xs text-zinc-500"><summary>File requirements & date options</summary><p className="my-3 leading-relaxed">Columns: {dataset.required_columns.join(", ")}. Each month must contain its complete transactions. Multiple data worksheets must be saved separately.</p>
                <DateFormat value={dates[dataset.id] ?? ""} onChange={(value) => change(() => setDates((previous) => ({ ...previous, [dataset.id]: value })))} disabled={blocked} label={`${dataset.id === "sales_history" ? "Sales" : "Samples"} date format`} />
                {files[dataset.id] && saved ? <label className="mt-4 flex items-start gap-2 text-sm"><input type="checkbox" checked={Boolean(replacing[dataset.id])} disabled={blocked} onChange={(event) => { const checked = event.target.checked; change(() => setReplacing((previous) => ({ ...previous, [dataset.id]: checked ? saved.version_id : null }))); }} /><span>Replace saved month with a corrected single-month file</span></label> : null}
              </details>
            </div>;
          })}
        </div>
        {Object.values(replacing).some(Boolean) ? <label className="mt-5 flex flex-col gap-2 text-sm">What changed in this correction?<textarea className={controlClass} value={reason} onChange={(event) => change(() => setReason(event.target.value))} disabled={blocked} /></label> : null}
      </> : <div className="space-y-4">
        <p className="text-sm text-zinc-500">No uploads needed. Choose the month you want to recreate.</p>
        <label className="flex flex-col gap-2 text-sm">Saved month<select className={controlClass} value={savedPeriod ? period : ""} disabled={blocked} onChange={(event) => change(() => { setPeriod(event.target.value); setCycleId(""); })}><option value="" disabled>Choose a saved month</option>{catalog.periods.map((item) => <option key={item.period} value={item.period}>{item.period}{item.ready ? "" : " · incomplete"}</option>)}</select></label>
        {savedPeriod?.cycles?.length ? <details className="text-sm text-zinc-500"><summary>Choose a previous report’s exact data</summary><select aria-label="Saved source selection" className={`${controlClass} mt-3`} value={cycleId} disabled={blocked} onChange={(event) => change(() => setCycleId(event.target.value))}><option value="">Most recent report’s exact data</option>{savedPeriod.cycles.map((cycle) => <option key={cycle.cycle_id} value={cycle.cycle_id}>{new Date(cycle.created_at).toLocaleString()}</option>)}<option value="current">Current saved data, including corrections</option></select></details> : null}
        {!savedPeriod?.ready ? <p className="text-sm text-amber-700 dark:text-amber-300">This month has missing data. Upload its sales and samples to continue.</p> : null}
      </div>}
    </section>
    <ErrorNotice error={error} />
    {review ? <Review review={review} /> : null}
    {!generated ? <section className="workflow-panel">
      <StepHeading number="3" title="Generate your reports">ForgeXL checks the files, saves new history, and creates every rep’s workbook.</StepHeading>
      {review?.ready && review.warnings.length ? <div className="mb-5"><WarningConsent checked={acknowledged} onChange={setAcknowledged} disabled={blocked} /></div> : null}
      <button className="primary-button" disabled={blocked || !period || missing.length > 0 || (mode === "saved" && !savedPeriod?.ready) || (review?.ready && review.warnings.length > 0 && !acknowledged)} onClick={() => perform()}>
        {busy === "validate" ? "Checking your files…" : busy === "generate" ? "Generating your reports…" : "Generate reports"}<span aria-hidden="true">→</span>
      </button>
      <p className="generate-help" role="status">{missing.length ? `Add ${missing.map((dataset) => dataset.id === "sales_history" ? "sales data" : "sample data").join(" and ")} to continue.` : review?.ready && review.warnings.length && !acknowledged ? "Review the warnings above and check the box to continue." : "Excel workbook for each rep + one ZIP with all reports"}</p>
    </section> : null}
    {outcome ? <section className="workflow-panel space-y-5" aria-label="Reporting result">
      <div role="status"><h2 className="text-2xl font-semibold">{generated ? "Your reports are ready." : "Reports need another attempt."}</h2><p className="mt-2 text-sm text-zinc-500">Sources {outcome.sources_committed ? "saved" : "partially saved or not saved"} · {generated ? "Reports generated" : "Reports not generated"}</p></div>
      <ErrorNotice error={outcome.error} />
      {generated ? <>
        <a className="primary-button" href={runArtifactsZipUrl({ runId: outcome.manifest.run_id })}>Download all reports (ZIP)<span aria-hidden="true">↓</span></a>
        <ArtifactDownloads runId={outcome.manifest.run_id} artifacts={outcome.manifest.artifacts} showBundle={false} />
        <details className="advanced-options"><summary>Preview report data</summary><div className="mt-4 space-y-4"><label className="flex flex-col gap-2 text-sm">Report table<select className={controlClass} value={outputId} onChange={(event) => setOutputId(event.target.value)}>{outcome.manifest.outputs.map((output) => <option key={output.id} value={output.id}>{output.label}</option>)}</select></label><DataPreview key={`${outcome.manifest.run_id}-${outputId}`} runId={outcome.manifest.run_id} outputId={outputId} /></div></details>
        <details className="advanced-options"><summary>Run details & cleanup</summary><div className="mt-4 space-y-4"><AuditSummary manifest={outcome.manifest} /><ReleaseRun runId={outcome.manifest.run_id} onReleased={() => setOutcome(null)} disabled={blocked} /></div></details>
      </> : <><p className="text-sm">Saved months remain available. Your selected files can be reused to finish an interrupted save.</p>{outcome.receipt ? <button className={buttonClass} disabled={blocked} onClick={() => perform(outcome.receipt)}>Retry using saved data</button> : null}<details className="text-xs text-zinc-500"><summary>Saved months from this attempt</summary><pre className="mt-3 overflow-auto">{JSON.stringify(outcome.committed_versions, null, 2)}</pre></details></>}
    </section> : null}
    <details className="advanced-options"><summary>More options: add history without generating reports</summary><div className="mt-4"><HistoryImporter onSaved={async () => { change(() => {}); await onSaved(); }} disabled={Boolean(busy)} onBusy={(value) => { if (value) change(() => {}); setHistoryBusy(value); }} /></div></details>
  </div>;
}
