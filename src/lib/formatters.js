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

/**
 * Render a row or column count the way a reader expects, e.g. `15,842`.
 *
 * Grouping separators are a presentation of a count the application itself
 * produced, never of a value from the user's file — the preview shows those
 * exactly as they were uploaded (build plan section 3.3).
 */
export function formatCount(value) {
  return Number.isFinite(value) ? value.toLocaleString() : "";
}

/**
 * Render an execution time, e.g. `0.82 s` or `42 ms`.
 *
 * Sub-100 ms durations stay in milliseconds: rounding them to two decimal
 * places of a second would report most of them as `0.00 s`.
 */
export function formatDuration(milliseconds) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0) return "";
  if (milliseconds < 100) return `${Math.round(milliseconds)} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}

/**
 * Turn a backend metric key into a label, e.g. `duplicates_removed` →
 * `Duplicates removed`.
 *
 * Metric keys are the Action's own, so they are presented rather than
 * translated: no key is renamed, reordered or dropped, and one this UI has
 * never seen still reads correctly.
 */
export function formatMetricLabel(key) {
  const words = String(key ?? "")
    .replace(/[_-]+/g, " ")
    .trim();
  if (!words) return "";
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * Render one preview cell.
 *
 * Values are shown exactly as the backend sent them: no number is regrouped,
 * no string is trimmed and no blank is filled in. Only `null` is given a
 * visible stand-in, because an empty cell and a cell holding an empty string
 * would otherwise look identical — and they are different facts about the
 * user's data.
 */
export function formatCell(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

/** True when a cell holds no value at all, so it can be styled as empty. */
export function isBlankCell(value) {
  return value === null || value === undefined;
}
