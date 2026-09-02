import { outputDownloadUrl, runWorkbookUrl } from "@/lib/api";
import { formatExportLabel } from "@/lib/formatters";

/**
 * Download the result (build plan section 29, 6F).
 *
 * One link per export format the backend says this output is available in, so
 * an Action offering a format this file has never heard of still gets a working
 * button. Nothing here is Action-specific, and nothing branches on an Action ID.
 *
 * These are plain links, not fetches. Following one is an ordinary navigation:
 * the file goes straight from the backend to the user's downloads folder, the
 * page never holds a second copy of the result, and the backend's
 * `Content-Disposition` names the file (build plan 6F.6) — which is why no
 * `download` attribute appears here.
 *
 * When a Run produced more than one table, a further link fetches all of them
 * as one workbook, a worksheet each (build plan 6F.4). A single-table Run does
 * not show it: that workbook would be the Excel download beside it.
 */
export default function ExportButtons({ runId, output, outputs }) {
  if (!runId || !output) return null;

  const formats = Array.isArray(output.formats) ? output.formats : [];
  const tableCount = Array.isArray(outputs) ? outputs.length : 0;

  if (formats.length === 0 && tableCount < 2) return null;

  return (
    <section className='flex flex-col gap-3'>
      <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
        Export
      </h3>

      <div className='flex flex-wrap gap-2'>
        {formats.map((format) => (
          <DownloadLink
            key={format}
            href={outputDownloadUrl({ runId, outputId: output.id, format })}
          >
            {formatExportLabel(format)}
          </DownloadLink>
        ))}

        {tableCount > 1 ? (
          <DownloadLink href={runWorkbookUrl({ runId })}>
            Download All Results (Excel)
          </DownloadLink>
        ) : null}
      </div>

      <p className='text-xs text-zinc-500 dark:text-zinc-500'>
        {tableCount > 1
          ? `Downloads ${output.label}. The workbook holds all ${tableCount} result tables.`
          : "Generated from this Run when you download it."}
      </p>
    </section>
  );
}

/** One download link, styled as the buttons beside it are. */
function DownloadLink({ href, children }) {
  return (
    <a
      href={href}
      className='rounded-lg border border-zinc-300 px-3 py-1.5 text-sm text-zinc-800 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-900'
    >
      {children}
    </a>
  );
}
