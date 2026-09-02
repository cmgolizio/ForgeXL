import { formatCount } from "@/lib/formatters";

/**
 * What happened to the Run (build plan 5.9, 5.10, 6E.5).
 *
 * Renders the processing indicator, the success confirmation and the failure
 * panel. Only strings that the backend meant for a human are shown: an error
 * object is never printed, and a traceback never reaches here — the backend
 * does not send one.
 *
 * There is no progress percentage. The Run's real progress is unknown while it
 * executes, so the indicator says so rather than inventing a number.
 *
 * A successful Run may still carry validation warnings — a submitted file the
 * Action does not use, for instance. Those are shown beside the success
 * confirmation rather than folded into it: a warning never failed the Run
 * (build plan section 6.2), but hiding it would leave the user unaware of
 * something the backend deliberately reported.
 */
export default function RunStatus({ state, error, manifest }) {
  if (state === "running") {
    return (
      <p
        role='status'
        aria-live='polite'
        className='flex items-center gap-2 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 text-sm text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-300'
      >
        <span
          className='h-2 w-2 animate-pulse rounded-full bg-zinc-500'
          aria-hidden='true'
        />
        Processing…
      </p>
    );
  }

  if (state === "success" && manifest) {
    const audit = manifest.audit;
    return (
      <div className='flex flex-col gap-3'>
        <section
          aria-live='polite'
          className='flex flex-col gap-1 rounded-lg border border-emerald-300 bg-emerald-50 px-4 py-3 dark:border-emerald-900 dark:bg-emerald-950/40'
        >
          <h3 className='text-sm font-semibold text-emerald-900 dark:text-emerald-200'>
            Run Successful
          </h3>
          <p className='text-sm text-emerald-800 dark:text-emerald-300'>
            {manifest.action?.name} read {formatCount(audit?.rows_received)}{" "}
            rows and returned {formatCount(audit?.rows_returned)}.
          </p>
        </section>
        <ValidationWarnings warnings={manifest.validation?.warnings} />
      </div>
    );
  }

  if ((state === "validation_error" || state === "server_error") && error) {
    return (
      <section
        role='alert'
        className='flex flex-col gap-2 rounded-lg border border-red-300 bg-red-50 px-4 py-3 dark:border-red-900 dark:bg-red-950/40'
      >
        <h3 className='text-sm font-semibold text-red-900 dark:text-red-200'>
          {state === "validation_error" ? "Validation Failed" : "Run Failed"}
        </h3>
        <ul className='flex flex-col gap-2'>
          {error.issues.map((issue, index) => (
            <li
              key={`${issue.code}-${index}`}
              className='text-sm text-red-800 dark:text-red-300'
            >
              <p>{issue.message}</p>
              <IssueColumns
                label='Missing required columns:'
                columns={issue.details?.missing_columns}
              />
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return null;
}

/**
 * Warnings the backend reported about a Run that nevertheless succeeded.
 *
 * Rendered only when there are some, so a clean Run says nothing rather than
 * announcing that it has no warnings.
 */
function ValidationWarnings({ warnings }) {
  const issues = Array.isArray(warnings)
    ? warnings.filter((issue) => typeof issue?.message === "string")
    : [];
  if (issues.length === 0) return null;

  return (
    <section className='flex flex-col gap-1 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 dark:border-amber-900 dark:bg-amber-950/40'>
      <h3 className='text-sm font-semibold text-amber-900 dark:text-amber-200'>
        {issues.length === 1 ? "Warning" : "Warnings"}
      </h3>
      <ul className='flex flex-col gap-1'>
        {issues.map((issue, index) => (
          <li
            key={`${issue.code}-${index}`}
            className='text-sm text-amber-800 dark:text-amber-300'
          >
            {issue.message}
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * List the columns named in a structured validation issue.
 *
 * Renders nothing unless the backend actually supplied a list of strings, so a
 * differently-shaped `details` payload can never become `[object Object]`.
 */
function IssueColumns({ label, columns }) {
  const names = Array.isArray(columns)
    ? columns.filter((name) => typeof name === "string")
    : [];
  if (names.length === 0) return null;

  return (
    <>
      <p className='mt-1'>{label}</p>
      <ul className='list-inside list-disc font-mono text-xs'>
        {names.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
    </>
  );
}
