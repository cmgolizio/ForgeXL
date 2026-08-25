"use client";

/**
 * The Run control (build plan 5.9).
 *
 * Disabled whenever a Run cannot legitimately start — no Action selected, a
 * required file missing, or a Run already executing — so repeated clicking
 * cannot submit twice.
 */
export default function RunButton({
  onRun,
  disabled = false,
  running = false,
}) {
  return (
    <button
      type='button'
      onClick={onRun}
      disabled={disabled || running}
      aria-busy={running}
      className='inline-flex w-full items-center justify-center rounded-lg bg-zinc-900 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto sm:self-start dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200'
    >
      {running ? "Processing…" : "Run Action"}
    </button>
  );
}
