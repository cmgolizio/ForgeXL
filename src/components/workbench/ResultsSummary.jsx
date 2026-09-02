import {
  formatCount,
  formatDuration,
  formatMetricLabel,
} from "@/lib/formatters";

/**
 * What the Run produced (build plan 6E.1, section 29).
 *
 * Shows the counts and the Action's own metrics for one result table. Every
 * number here was measured by the backend and is displayed as it was reported:
 * nothing is derived in the browser, so the UI can never disagree with the
 * manifest about what happened.
 */
export default function ResultsSummary({ manifest, output }) {
  if (!manifest || !output) return null;

  return (
    <section className='flex flex-col gap-4'>
      <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
        Results
      </h3>

      <dl className='grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4'>
        <Figure
          label='Input Rows'
          value={formatCount(output.input_row_count)}
        />
        <Figure label='Output Rows' value={formatCount(output.row_count)} />
        <Figure label='Columns' value={formatCount(output.column_count)} />
        <Figure
          label='Execution'
          value={formatDuration(manifest.duration_ms)}
        />
      </dl>

      <ColumnChange
        label='Columns removed'
        columns={output.columns_removed}
        description='Present in the uploaded data and not in this result.'
      />
      <ColumnChange
        label='Columns added'
        columns={output.columns_added}
        description='Created by the Action; they were not in the uploaded data.'
      />

      <Metrics metrics={manifest.metrics} />
    </section>
  );
}

/** One labelled number. */
function Figure({ label, value }) {
  if (!value) return null;

  return (
    <div className='flex flex-col gap-0.5'>
      <dt className='text-xs text-zinc-500 dark:text-zinc-500'>{label}</dt>
      <dd className='text-lg font-semibold tabular-nums text-zinc-900 dark:text-zinc-100'>
        {value}
      </dd>
    </div>
  );
}

/**
 * Name the columns this result gained or lost.
 *
 * Renders nothing when there are none, so an Action that changes no columns
 * says nothing rather than saying "none".
 */
function ColumnChange({ label, columns, description }) {
  const names = Array.isArray(columns)
    ? columns.filter((name) => typeof name === "string")
    : [];
  if (names.length === 0) return null;

  return (
    <div className='flex flex-col gap-1'>
      <p className='text-xs text-zinc-500 dark:text-zinc-500'>
        {label} <span className='text-zinc-400'>— {description}</span>
      </p>
      <ul className='flex flex-wrap gap-1.5'>
        {names.map((name) => (
          <li
            key={name}
            className='rounded border border-zinc-200 bg-zinc-50 px-1.5 py-0.5 font-mono text-xs text-zinc-700 dark:border-zinc-800 dark:bg-zinc-900/60 dark:text-zinc-300'
          >
            {name}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * The Action's own reported counts.
 *
 * The keys are the Action's, so they are listed in the order the backend sent
 * them and none is dropped: a new Action's metrics appear here with no change
 * to this file. Only values that are genuinely numbers or strings are shown,
 * so an unexpected shape can never render as `[object Object]`.
 */
function Metrics({ metrics }) {
  const entries = Object.entries(metrics ?? {}).filter(
    ([, value]) => typeof value === "number" || typeof value === "string",
  );
  if (entries.length === 0) return null;

  return (
    <div className='flex flex-col gap-2'>
      <h4 className='text-xs font-medium text-zinc-500 dark:text-zinc-500'>
        Action Metrics
      </h4>
      <dl className='grid grid-cols-1 gap-x-6 gap-y-1 sm:grid-cols-2'>
        {entries.map(([key, value]) => (
          <div
            key={key}
            className='flex items-baseline justify-between gap-4 border-b border-zinc-100 py-1 dark:border-zinc-800/60'
          >
            <dt className='text-sm text-zinc-600 dark:text-zinc-400'>
              {formatMetricLabel(key)}
            </dt>
            <dd className='text-sm font-medium tabular-nums text-zinc-900 dark:text-zinc-100'>
              {typeof value === "number" ? formatCount(value) : value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
