/**
 * One input slot that reads stored data instead of an upload (build plan 11A).
 *
 * `FileUploadSlot` renders a slot the user fills with a file. Since Phase 11 a
 * slot may instead name a Data Library dataset, and since Phase 13 a registered
 * Action uses one — so the selector can now reach an Action with nothing to
 * upload. Rendering an upload control for it would ask the user for a file it
 * does not want and could not accept.
 *
 * Entirely generic: every line comes from the slot definition the backend
 * returned, and nothing branches on an Action ID, a slot ID or a dataset name.
 * An Action declaring one library slot and an Action declaring five render
 * here identically.
 *
 * Choosing *which* stored version to read is build plan 15A's Monthly Reports
 * workflow, with the reporting period it belongs to. Until that exists this
 * slot says so rather than pretending to be ready.
 */
export default function LibraryInputSlot({ input }) {
  const requiredColumns = input.required_columns ?? [];

  return (
    <div className='flex flex-col gap-2 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 p-4 dark:border-zinc-700 dark:bg-zinc-900/40'>
      <div className='flex flex-wrap items-baseline justify-between gap-2'>
        <span className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
          {input.label}
        </span>
        <span className='text-xs text-zinc-500 dark:text-zinc-400'>
          Stored data
        </span>
      </div>

      {input.description ? (
        <p className='text-xs text-zinc-600 dark:text-zinc-400'>
          {input.description}
        </p>
      ) : null}

      {requiredColumns.length > 0 ? (
        <p className='text-xs text-zinc-500 dark:text-zinc-500'>
          Required columns: {requiredColumns.join(", ")}
        </p>
      ) : null}

      <p className='text-xs text-zinc-600 dark:text-zinc-400'>
        This input is read from data already saved in ForgeXL, so there is
        nothing to upload.
      </p>
    </div>
  );
}
