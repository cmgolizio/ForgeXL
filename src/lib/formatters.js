/**
 * Small pure helpers for presenting a chosen file (build plan 5.6).
 *
 * Display concerns only: nothing here talks to the backend, and nothing here
 * decides whether a file is acceptable — the backend remains authoritative.
 */

const SIZE_UNITS = ["B", "KB", "MB", "GB"];

/**
 * Render a byte count the way a file manager would, e.g. `1.4 MB`.
 *
 * Returns an empty string for anything that is not a real byte count, so a
 * missing size renders as nothing rather than as `NaN`.
 */
export function formatFileSize(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;

  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < SIZE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(1)} ${SIZE_UNITS[unit]}`;
}

/**
 * Return the lowercase extension of a filename, e.g. `.csv`.
 *
 * Mirrors the backend's `extension_of`: any directory component is discarded
 * first, so `../../evil.csv` yields `.csv`, and a name that is nothing but an
 * extension (`.csv`) has no extension at all. Used both for display and for
 * the convenience check in build plan 5.7; the backend still decides.
 */
export function fileExtension(filename) {
  const basename = String(filename ?? "")
    .replace(/\\/g, "/")
    .split("/")
    .pop();
  const dot = basename.lastIndexOf(".");
  return dot > 0 ? basename.slice(dot).toLowerCase() : "";
}

/**
 * Join values into a readable list, e.g. `.csv or .xlsx`.
 *
 * Matches the phrasing the backend uses in its own validation messages, so the
 * convenience check and the authoritative one read the same way.
 */
export function joinWithOr(values) {
  const items = Array.from(values ?? []);
  if (items.length <= 1) return items.join("");
  return `${items.slice(0, -1).join(", ")} or ${items[items.length - 1]}`;
}
