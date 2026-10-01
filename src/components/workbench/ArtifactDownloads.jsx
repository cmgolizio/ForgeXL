import { artifactDownloadUrl, runArtifactsZipUrl } from "@/lib/api";
import { formatArtifactTypeLabel, formatFileSize } from "@/lib/formatters";

/**
 * The finished files a Run produced (build plan 12A, 12G).
 *
 * Separate from `ExportButtons` because the two answer different questions.
 * An export is a result *table* rendered into a format on request — the same
 * data, as CSV or as Excel. An artifact is a file the Action itself produced:
 * a formatted report, an archive, a document. Offering them together would
 * suggest they are alternatives, and they are not.
 *
 * Entirely generic. Every line below comes from `manifest.artifacts`, so an
 * Action that produces one report and an Action that produces fifty render
 * here identically and neither needs a change to this file (build plan 12G:
 * "Do not hardcode sales-rep names into the frontend"). Nothing branches on an
 * Action ID, an artifact ID or a filename.
 *
 * Renders nothing at all when a Run produced no artifacts, which is every Run
 * either registered Action can produce today.
 *
 * These are plain links, not fetches, for the same reason the export links
 * are: following one is an ordinary same-origin navigation, the file streams
 * straight to the downloads folder, and the backend's `Content-Disposition`
 * names it — under the Action's own filename, which is the point of an
 * artifact.
 */
export default function ArtifactDownloads({ runId, artifacts }) {
  const files = Array.isArray(artifacts) ? artifacts : [];
  if (!runId || files.length === 0) return null;

  return (
    <section className='flex flex-col gap-3'>
      <h3 className='text-sm font-medium text-zinc-900 dark:text-zinc-100'>
        Generated Files
      </h3>

      <ul className='flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800/60'>
        {files.map((artifact) => (
          <li
            key={artifact.id}
            className='flex flex-wrap items-center justify-between gap-3 py-2'
          >
            <div className='flex min-w-0 flex-col gap-0.5'>
              <span className='truncate text-sm text-zinc-900 dark:text-zinc-100'>
                {artifact.label}
              </span>
              <span className='truncate font-mono text-xs text-zinc-500 dark:text-zinc-500'>
                {artifact.filename}
              </span>
            </div>

            <div className='flex shrink-0 items-center gap-3'>
              <span className='text-xs text-zinc-500 dark:text-zinc-500'>
                {formatArtifactTypeLabel(artifact.artifact_type)}
                {artifact.size_bytes ? " · " : ""}
                {formatFileSize(artifact.size_bytes)}
              </span>
              <DownloadLink
                href={artifactDownloadUrl({ runId, artifactId: artifact.id })}
              >
                Download
              </DownloadLink>
            </div>
          </li>
        ))}
      </ul>

      {/*
        Offered only when there is more than one file: a bundle of one is a
        slower way to download the file that is already on the line above it.
      */}
      {files.length > 1 ? (
        <div className='flex flex-wrap items-center gap-3'>
          <DownloadLink href={runArtifactsZipUrl({ runId })}>
            Download All ({files.length} files, ZIP)
          </DownloadLink>
          <span className='text-xs text-zinc-500 dark:text-zinc-500'>
            One archive holding every file above.
          </span>
        </div>
      ) : null}
    </section>
  );
}

/** One download link, styled as the export buttons beside it are. */
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
