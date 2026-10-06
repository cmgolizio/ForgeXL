import { useId } from "react";

export const buttonClass = "secondary-button";
export const controlClass = "form-control";
export const panelClass = "workflow-panel flex flex-col gap-4";

export function Issues({ title, issues = [] }) {
  if (!issues.length) return null;
  return <div className="flex flex-col gap-2">
    <h3 className="text-sm font-semibold">{title} ({issues.length})</h3>
    <ul className="list-disc space-y-2 pl-5 text-sm">
      {issues.map((issue, index) => <li key={`${issue.code}-${index}`}>
        {issue.message}
        {issue.details && Object.keys(issue.details).length ? <details className="mt-1 text-xs text-zinc-500">
          <summary className="cursor-pointer">Details</summary>
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap break-words">{JSON.stringify(issue.details, null, 2)}</pre>
        </details> : null}
      </li>)}
    </ul>
  </div>;
}

export function ErrorNotice({ error }) {
  if (!error) return null;
  return <div role="alert" className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-900 dark:border-red-900 dark:bg-red-950/30 dark:text-red-200">
    <Issues title="Could not complete this step" issues={error.issues ?? [error]} />
  </div>;
}

export function WarningConsent({ checked, onChange, disabled }) {
  return <label className="flex items-start gap-2 text-sm">
    <input type="checkbox" aria-label="Acknowledge validation warnings" checked={checked} onChange={(event) => onChange(event.target.checked)} disabled={disabled} className="mt-1" />
    <span>I reviewed the warnings and accept the stated limits of these reports.</span>
  </label>;
}

export function DateFormat({ value, onChange, disabled, label = "Invoice date format" }) {
  const id = useId();
  return <div className="flex flex-col gap-1">
    <label htmlFor={id} className="text-xs text-zinc-500">{label}</label>
    <select id={id} className={controlClass} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
      <option value="">Detect unambiguous dates</option>
      <option value="%Y-%m-%d">ISO · YYYY-MM-DD</option>
      <option value="%Y-%m-%d %H:%M:%S">ISO date and time</option>
      <option value="%m/%d/%Y">US · MM/DD/YYYY</option>
      <option value="%d/%m/%Y">International · DD/MM/YYYY</option>
    </select>
  </div>;
}

export default function Review({ review, onUseSavedMonths, disabled }) {
  const tone = !review.ready ? "border-red-300 dark:border-red-900" : review.warnings.length ? "border-amber-400 dark:border-amber-800" : "border-emerald-400 dark:border-emerald-800";
  return <section className={`${panelClass} ${tone}`} aria-label="Validation summary">
    <h2 className="text-lg font-semibold">{!review.ready ? "Validation needs attention" : review.warnings.length ? "Ready with warnings" : "Ready to generate"}</h2>
    <p className="text-sm text-zinc-500">{review.period} · {review.source_selection}</p>
    <ul className="space-y-2 text-sm">
      {review.checks.map((check) => <li key={check.label} className="flex flex-wrap justify-between gap-2">
        <span>{check.label} · <strong>{check.status}</strong></span><span className="text-zinc-500">{check.detail}</span>
      </li>)}
    </ul>
    <details>
      <summary className="cursor-pointer text-sm">File details and sales reps ({review.reps.length})</summary>
      <ul className="my-3 space-y-2 text-sm">{review.sources.map((source) => <li key={source.dataset_id}>
        {source.label}: {source.filename} · {source.row_count.toLocaleString()} rows · {source.operation}{source.imported_periods?.length ? ` · ${source.imported_periods.length} new months` : ""}{source.reused_periods?.length ? ` · ${source.reused_periods.length} saved months reused` : ""}
        {source.version_id ? <span className="block break-all font-mono text-xs text-zinc-500">Saved version {source.version_id}</span> : null}
      </li>)}</ul>
      <p className="text-sm">{review.reps.join(", ") || (!review.ready ? "Rep detection waits until source errors are resolved." : "No rep roster available")}</p>
    </details>
    {review.coverage.some((item) => item.missing_months.length) ? <details>
      <summary className="cursor-pointer text-sm">Missing calendar months</summary>
      <ul className="mt-2 space-y-2 text-xs">{review.coverage.filter((item) => item.missing_months.length).map((item) => <li key={`${item.dataset_id}-${item.window}`}>
        {item.dataset_id} · {item.window}: {item.missing_months.join(", ")}
      </li>)}</ul>
    </details> : null}
    {onUseSavedMonths ? [...new Set(review.errors.filter((issue) => issue.code === "HISTORY_MONTH_CONFLICT").map((issue) => issue.slot_id))].map((id) => <div key={id} className="space-y-2">
      <p className="text-sm">Keep the saved {id === "sales_history" ? "sales" : "sample"} history and import only missing months from this file. Your other selected file stays available.</p>
      <button className={buttonClass} disabled={disabled} onClick={() => onUseSavedMonths(id)}>Use saved {id === "sales_history" ? "sales" : "sample"} months</button>
    </div>) : null}
    <Issues title="Errors" issues={review.errors} />
    <Issues title="Warnings" issues={review.warnings} />
    {review.ready ? <p className="text-xs text-zinc-500">This review expires in 15 minutes. Sources are saved only when you generate.</p> : null}
  </section>;
}
