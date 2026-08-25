"use client";

/**
 * The Action selector (build plan 5.3).
 *
 * Options come exclusively from `GET /api/actions`. There is no hardcoded
 * Action list and no branch on any particular Action ID, so a newly registered
 * backend Action appears here with no change to this file.
 */
export default function ActionSelector({
  actions,
  selectedActionId,
  onSelect,
  disabled = false,
}) {
  return (
    <div className='flex flex-col gap-2'>
      <label
        htmlFor='action-selector'
        className='text-sm font-medium text-zinc-900 dark:text-zinc-100'
      >
        Action
      </label>
      <select
        id='action-selector'
        value={selectedActionId}
        disabled={disabled}
        onChange={(event) => onSelect(event.target.value)}
        className='w-full rounded-lg border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 disabled:cursor-not-allowed disabled:opacity-60 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100'
      >
        <option value=''>Select Action</option>
        {actions.map((action) => (
          <option key={action.id} value={action.id}>
            {action.name}
          </option>
        ))}
      </select>
    </div>
  );
}
