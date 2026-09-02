import { formatCount, formatDuration } from "@/lib/formatters";

/**
 * What happened during the Run (build plan 6E.5).
 *
 * The backend assembles this from the Run's own record and the UI presents it
 * unchanged. Nothing here is recomputed in the browser, so the explanation the
 * user reads is the explanation the audit record holds.
 *
 * `rows_affected` is shown only when the Action stated one. An Action that
 * does not report a figure leaves the row out rather than having the interface
 * invent one from the difference between two counts.
 */
export default function AuditSummary({ manifest }) {
  const audit = manifest?.audit;
  if (!audit) return null;

  return (
    <section className='flex flex-col gap-3'>
      <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
        Audit Summary
      </h3>

      <dl className='flex flex-col gap-1 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/40'>
        <Row label='Action'>
          {audit.action?.name}{" "}
          <span className='text-zinc-500'>version {audit.action?.version}</span>
        </Row>
        <Row label='Status'>{audit.status}</Row>
        <Row label='Rows received'>{formatCount(audit.rows_received)}</Row>
        {Number.isFinite(audit.rows_returned) ? (
          <Row label='Rows returned'>{formatCount(audit.rows_returned)}</Row>
        ) : null}
        {Number.isFinite(audit.rows_affected) ? (
          <Row label='Rows affected'>{formatCount(audit.rows_affected)}</Row>
        ) : null}
        {Number.isFinite(audit.duration_ms) ? (
          <Row label='Execution'>{formatDuration(audit.duration_ms)}</Row>
        ) : null}
        <Row label='Run ID'>
          <span className='font-mono text-xs'>{manifest.run_id}</span>
        </Row>
      </dl>

      <Inputs inputs={audit.inputs} />
      <Results results={audit.results} primaryId={audit.primary_result_id} />
    </section>
  );
}

function Row({ label, children }) {
  return (
    <div className='flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5'>
      <dt className='text-sm text-zinc-600 dark:text-zinc-400'>{label}</dt>
      <dd className='text-sm text-zinc-900 dark:text-zinc-100'>{children}</dd>
    </div>
  );
}

/** The files this Run actually used, with what each one contributed. */
function Inputs({ inputs }) {
  const used = Array.isArray(inputs) ? inputs : [];
  if (used.length === 0) return null;

  return (
    <div className='flex flex-col gap-1'>
      <h4 className='text-xs font-medium text-zinc-500 dark:text-zinc-500'>
        Inputs Used
      </h4>
      <ul className='flex flex-col gap-1'>
        {used.map((input) => (
          <li
            key={input.slot_id}
            className='flex flex-wrap items-baseline justify-between gap-x-4 text-sm'
          >
            <span className='text-zinc-800 dark:text-zinc-200'>
              {input.original_filename}
            </span>
            <span className='tabular-nums text-zinc-500 dark:text-zinc-500'>
              {formatCount(input.row_count)} rows ·{" "}
              {formatCount(input.column_count)} columns
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** The result tables this Run makes available. */
function Results({ results, primaryId }) {
  const tables = Array.isArray(results) ? results : [];
  if (tables.length === 0) return null;

  return (
    <div className='flex flex-col gap-1'>
      <h4 className='text-xs font-medium text-zinc-500 dark:text-zinc-500'>
        Results Produced
      </h4>
      <ul className='flex flex-col gap-1'>
        {tables.map((table) => (
          <li
            key={table.output_id}
            className='flex flex-wrap items-baseline justify-between gap-x-4 text-sm'
          >
            <span className='text-zinc-800 dark:text-zinc-200'>
              {table.label}
              {table.output_id === primaryId && tables.length > 1 ? (
                <span className='text-zinc-500'> (primary)</span>
              ) : null}
            </span>
            <span className='tabular-nums text-zinc-500 dark:text-zinc-500'>
              {formatCount(table.row_count)} rows ·{" "}
              {formatCount(table.column_count)} columns
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
