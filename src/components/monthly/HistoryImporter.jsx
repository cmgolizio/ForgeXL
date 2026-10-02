"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, commitHistory, discardMonthlyValidation, validateHistory } from "@/lib/api";
import FileUploadSlot from "@/components/workbench/FileUploadSlot";
import { buttonClass, controlClass, DateFormat, ErrorNotice, Issues, WarningConsent } from "./Review";

export default function HistoryImporter({ onSaved, disabled, onBusy }) {
  const [dataset, setDataset] = useState("sales_history");
  const [file, setFile] = useState(null);
  const [dateFormat, setDateFormat] = useState("");
  const [skipExisting, setSkipExisting] = useState(false);
  const [review, setReview] = useState(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [outcome, setOutcome] = useState(null);
  const working = useRef(false);
  const validation = useRef(null);

  useEffect(() => () => {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
  }, []);

  function change(update) {
    if (validation.current) discardMonthlyValidation(validation.current).catch(() => {});
    validation.current = null;
    update(); setReview(null); setOutcome(null); setError(null); setAcknowledged(false);
  }

  async function perform(save) {
    if (working.current) return;
    working.current = true; setBusy(true); onBusy(true); setError(null);
    try {
      if (save) {
        const result = await commitHistory({ validation_id: review.validation_id, acknowledge_warnings: acknowledged });
        validation.current = null;
        setOutcome(result); setReview(null); setFile(null);
        await onSaved();
      } else {
        validation.current = null; setReview(null);
        const form = new FormData(); form.append("dataset_id", dataset); form.append("source_file", file);
        if (dateFormat) form.append("date_format", dateFormat);
        if (skipExisting) form.append("skip_existing", "true");
        setOutcome(null); setAcknowledged(false);
        const result = await validateHistory(form);
        validation.current = result.validation_id; setReview(result);
      }
    } catch (cause) {
      if (save) { validation.current = null; setReview(null); }
      setError(cause instanceof ApiError ? cause : new ApiError("History import could not be completed."));
    } finally { working.current = false; setBusy(false); onBusy(false); }
  }

  return <details className="rounded-xl border border-zinc-200 p-5 dark:border-zinc-800">
    <summary className="cursor-pointer font-medium">Initial history setup or add historical months</summary>
    <div className="mt-4 flex flex-col gap-4">
      <p className="text-sm text-zinc-500">Import company sales and samples in chunks, such as three-month exports. Each month is saved separately. Existing months are never appended or replaced here; use the monthly workflow for corrections. A month in a history chunk must contain its complete source data, not just missing rows.</p>
      <label className="flex flex-col gap-1 text-sm">Historical source
        <select className={controlClass} value={dataset} onChange={(event) => change(() => setDataset(event.target.value))} disabled={busy || disabled}>
          <option value="sales_history">Company sales history</option><option value="sample_history">Company sample history</option>
        </select>
      </label>
      <FileUploadSlot input={{ label: "Historical source file", accepted_extensions: [".csv", ".xlsx"], required: true }} file={file} disabled={busy || disabled} onSelect={(chosen) => change(() => setFile(chosen))} onRemove={() => change(() => setFile(null))} />
      <DateFormat value={dateFormat} onChange={(value) => change(() => setDateFormat(value))} disabled={busy || disabled} />
      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={skipExisting} onChange={(event) => change(() => setSkipExisting(event.target.checked))} disabled={busy || disabled} />
        <span>Import missing months only. Leave every already-stored month unchanged.</span>
      </label>
      <ErrorNotice error={error} />
      {review ? <div className="flex flex-col gap-3">
        <p className="text-sm font-medium">{review.ready ? "Ready to save" : "Validation needs attention"} · {review.row_count.toLocaleString()} source rows · {review.periods.length} month(s) to import</p>
        {review.ready ? <p className="text-xs text-zinc-500">Rows to save: {(review.imported_row_count ?? review.row_count).toLocaleString()}. Months: {review.periods.join(", ")}</p> : null}
        {review.skipped_periods?.length ? <p className="text-sm text-amber-700 dark:text-amber-300">Skipped unchanged: {review.skipped_periods.join(", ")}. No rows from these months will be imported.</p> : null}
        <Issues title="Errors" issues={review.errors} /><Issues title="Warnings" issues={review.warnings} />
        {review.warnings.length ? <WarningConsent checked={acknowledged} onChange={setAcknowledged} disabled={busy || disabled} /> : null}
      </div> : null}
      {outcome ? <div role="status" className="text-sm">
        <p>{outcome.status === "saved" ? "History saved." : "History save failed."} {outcome.committed_versions.length} monthly version(s) committed.</p>
        <ErrorNotice error={outcome.error} />
        {Object.keys(outcome.committed_periods ?? {}).length ? <p>Saved months: {Object.keys(outcome.committed_periods).join(", ")}</p> : null}
        {outcome.status === "save_failed" && outcome.committed_versions.length ? <p>Saved months remain available. Retry the same file with “Import missing months only” and review the remaining months.</p> : null}
      </div> : null}
      <div className="flex gap-3">
        <button className={buttonClass} onClick={() => perform(false)} disabled={busy || disabled || !file}>{busy ? "Working…" : "Validate history"}</button>
        {review?.ready ? <button className={buttonClass} onClick={() => perform(true)} disabled={busy || disabled || (review.warnings.length > 0 && !acknowledged)}>Save history</button> : null}
      </div>
    </div>
  </details>;
}
