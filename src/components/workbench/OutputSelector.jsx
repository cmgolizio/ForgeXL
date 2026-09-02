"use client";

/**
 * Choose which result table to look at (build plan 6E.1, "available result
 * tables").
 *
 * Rendered only when a Run produced more than one table, so the common
 * single-output Action shows no control the user does not need. The options
 * come from the Run's own manifest, so an Action that declares three outputs
 * needs no change here.
 */
export default function OutputSelector({
  outputs,
  selectedOutputId,
  onSelect,
}) {
  if (!Array.isArray(outputs) || outputs.length < 2) return null;

  return (
    <div className='flex flex-col gap-2'>
      <label
        htmlFor='output-selector'
        className='text-sm font-medium text-zinc-900 dark:text-zinc-100'
      >
        Result
      </label>
      <select
        id='output-selector'
        value={selectedOutputId ?? ""}
        onChange={(event) => onSelect(event.target.value)}
        className='w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100'
      >
        {outputs.map((output) => (
          <option key={output.id} value={output.id}>
            {output.label}
          </option>
        ))}
      </select>
    </div>
  );
}
