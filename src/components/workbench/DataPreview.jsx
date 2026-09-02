"use client";

import { useEffect, useState } from "react";

import { ApiError, DEFAULT_PREVIEW_LIMIT, fetchPreview } from "@/lib/api";
import { formatCell, formatCount, isBlankCell } from "@/lib/formatters";

/**
 * A page of the result table (build plan section 31, 6E.2-6E.4).
 *
 * Only the page on screen is ever requested: paging forward asks the backend
 * for the next hundred rows rather than filtering a copy of the whole dataset
 * held in the browser. A plain HTML table is enough — the POC deliberately has
 * no spreadsheet component — and it scrolls horizontally inside its own box so
 * a wide result never makes the page scroll sideways.
 *
 * Values are rendered exactly as the backend sent them. The column types the
 * backend reports decide alignment only; they never change a value.
 *
 * The paging offset is this component's own state, so the caller gives it a
 * `key` that changes with the table. A different Run or a different output is
 * a different table and starts at its first page, rather than inheriting the
 * offset the previous one happened to be on.
 */
export default function DataPreview({ runId, outputId, label }) {
  const [page, setPage] = useState(null);
  const [offset, setOffset] = useState(0);
  const [status, setStatus] = useState("loading");
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!runId || !outputId) return undefined;

    const controller = new AbortController();

    async function load() {
      setStatus("loading");
      try {
        const loaded = await fetchPreview({
          runId,
          outputId,
          offset,
          limit: DEFAULT_PREVIEW_LIMIT,
          signal: controller.signal,
        });
        setPage(loaded);
        setStatus("ready");
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(
          cause instanceof ApiError
            ? cause
            : new ApiError("The preview could not be loaded."),
        );
        setStatus("error");
      }
    }

    load();
    return () => controller.abort();
  }, [runId, outputId, offset]);

  if (status === "error") {
    return (
      <section role='alert' className='flex flex-col gap-1'>
        <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
          Preview
        </h3>
        <p className='text-sm text-red-800 dark:text-red-300'>
          {error?.message}
        </p>
      </section>
    );
  }

  if (!page) {
    return (
      <section className='flex flex-col gap-2'>
        <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
          Preview
        </h3>
        <p className='text-sm text-zinc-600 dark:text-zinc-400'>
          Loading preview…
        </p>
      </section>
    );
  }

  const columns = page.columns ?? [];
  const rows = page.rows ?? [];
  const alignments = alignmentsFor(columns, page.column_schema);

  const first = page.total_rows === 0 ? 0 : page.offset + 1;
  const last = page.offset + rows.length;
  const hasPrevious = page.offset > 0;
  const hasNext = last < page.total_rows;

  return (
    <section className='flex flex-col gap-3' aria-busy={status === "loading"}>
      <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
        Preview{label ? ` — ${label}` : ""}
      </h3>

      <div className='overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-800'>
        <table className='min-w-full border-collapse text-sm'>
          <thead>
            <tr className='border-b border-zinc-200 bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900/60'>
              {columns.map((name, index) => (
                <th
                  key={name}
                  scope='col'
                  className={`whitespace-nowrap px-3 py-2 font-medium text-zinc-700 dark:text-zinc-300 ${alignments[index]}`}
                >
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr
                key={page.offset + rowIndex}
                className='border-b border-zinc-100 last:border-b-0 dark:border-zinc-800/60'
              >
                {columns.map((name, columnIndex) => (
                  <td
                    key={name}
                    className={`whitespace-nowrap px-3 py-1.5 ${
                      alignments[columnIndex]
                    } ${
                      isBlankCell(row?.[columnIndex])
                        ? "text-zinc-400 dark:text-zinc-600"
                        : "text-zinc-800 dark:text-zinc-200"
                    }`}
                  >
                    {formatCell(row?.[columnIndex])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? (
          <p className='px-3 py-4 text-sm text-zinc-600 dark:text-zinc-400'>
            This result has no rows.
          </p>
        ) : null}
      </div>

      <div className='flex flex-wrap items-center justify-between gap-3'>
        <p
          aria-live='polite'
          className='text-sm text-zinc-600 tabular-nums dark:text-zinc-400'
        >
          Showing {formatCount(first)}–{formatCount(last)} of{" "}
          {formatCount(page.total_rows)}
        </p>
        <div className='flex gap-2'>
          <PageButton
            onClick={() =>
              setOffset(Math.max(0, page.offset - DEFAULT_PREVIEW_LIMIT))
            }
            disabled={!hasPrevious || status === "loading"}
          >
            Previous
          </PageButton>
          <PageButton
            onClick={() => setOffset(page.offset + DEFAULT_PREVIEW_LIMIT)}
            disabled={!hasNext || status === "loading"}
          >
            Next
          </PageButton>
        </div>
      </div>
    </section>
  );
}

function PageButton({ onClick, disabled, children }) {
  return (
    <button
      type='button'
      onClick={onClick}
      disabled={disabled}
      className='rounded-lg border border-zinc-300 px-3 py-1.5 text-sm text-zinc-800 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:opacity-40 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900'
    >
      {children}
    </button>
  );
}

/**
 * Choose each column's alignment from the type the backend reported.
 *
 * Numbers read correctly only when their digits line up, so they are aligned
 * right; everything else is aligned left. A column the backend did not
 * describe — or described with a kind this UI does not know — falls back to
 * left, which is never wrong, only less tidy.
 */
function alignmentsFor(columns, schema) {
  const kinds = new Map(
    (Array.isArray(schema) ? schema : []).map((column) => [
      column?.name,
      column?.kind,
    ]),
  );
  return columns.map((name) =>
    kinds.get(name) === "number" ? "text-right" : "text-left",
  );
}
