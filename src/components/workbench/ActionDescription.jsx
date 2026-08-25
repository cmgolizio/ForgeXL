/**
 * What the selected Action does (build plan 5.4).
 *
 * Shows the name, the description and the version. The Action ID is
 * deliberately not shown: it is an internal identifier, not something the user
 * needs in order to decide whether this is the right Action.
 */
export default function ActionDescription({ action }) {
  if (!action) return null;

  return (
    <section className='flex flex-col gap-1 rounded-lg border border-zinc-200 bg-zinc-50 px-4 py-3 dark:border-zinc-800 dark:bg-zinc-900/40'>
      <div className='flex flex-wrap items-baseline gap-x-3 gap-y-1'>
        <h2 className='text-base font-semibold text-zinc-900 dark:text-zinc-100'>
          {action.name}
        </h2>
        <span className='text-xs text-zinc-500 dark:text-zinc-500'>
          Version {action.version}
        </span>
      </div>
      <p className='text-sm text-zinc-600 dark:text-zinc-400'>
        {action.description}
      </p>
    </section>
  );
}
