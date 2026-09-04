/**
 * The single place the browser talks to the ForgeXL backend (build plan 5.1).
 *
 * Every request is **same-origin**: it is addressed to `/forge-api/...` on
 * whatever host served the page, and the Route Handler at
 * `src/app/forge-api/[...path]/route.js` forwards it to FastAPI (build plan
 * 6G.2/6G.3). The browser therefore never learns the backend's address or port,
 * which is what lets a second laptop on the LAN use ForgeXL with nothing but a
 * browser (Phase 6 architectural rules 7-9).
 *
 * That handler is transport and nothing more (6G.4): it streams the request
 * body straight through without reading or parsing it, so no spreadsheet is
 * ever parsed in Node — the only implementation that understands a CSV or an
 * XLSX is the Python one, and Polars parses the upload there. (The backend does
 * read the uploaded bytes into memory before parsing them, so it can enforce
 * its 250 MB limit while receiving rather than after; the *file* is still
 * transferred once and parsed once.)
 *
 * No backend path appears anywhere else in the frontend. This module is
 * browser-only by design: it uses `fetch` and `FormData` and imports no Node
 * modules, so it is safe in a client component.
 */

/**
 * Same-origin namespace every backend request is addressed to.
 *
 * Paired with the Route Handler mounted at that path, which strips this prefix
 * and forwards the rest to FastAPI. It is a path, not a URL: there is
 * deliberately no environment variable, because a same-origin request has no
 * host to configure and a configurable one could be pointed off-origin.
 */
export const API_BASE_PATH = "/forge-api";

/**
 * Rows requested per preview page.
 *
 * Matches the backend's own default. The backend refuses anything above its
 * maximum rather than clamping, so this is a page size, not a limit to widen.
 */
export const DEFAULT_PREVIEW_LIMIT = 100;

/** The backend could not be reached at all — it is probably not running. */
export const NETWORK_ERROR_CODE = "NETWORK_ERROR";

/** Something failed in a way the backend's error contract does not describe. */
export const UNEXPECTED_ERROR_CODE = "UNEXPECTED_ERROR";

/**
 * One failure, in the shape build plan section 22 defines.
 *
 * `issues` always holds at least one entry with a human-readable `message`, so
 * the UI can render a failure without inspecting `code` and without ever
 * printing an object (build plan 5.10).
 */
export class ApiError extends Error {
  constructor(
    message,
    { status = 0, code = UNEXPECTED_ERROR_CODE, details = {}, issues } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.issues = issues?.length
      ? issues
      : [{ code, message, details, slotId: null }];
  }
}

/**
 * Return the Action definitions that drive the whole interface (build plan 5.2).
 *
 * The Action list is never hardcoded in the browser: adding a backend Action
 * makes it appear here with no frontend change (build plan section 3.2).
 */
export async function fetchActions({ signal } = {}) {
  const payload = await request("/api/actions", { signal });
  return Array.isArray(payload?.actions) ? payload.actions : [];
}

/**
 * Execute one Action and return its Run manifest (build plan 5.8).
 *
 * `files` is keyed by Action input slot ID. Those keys are sent verbatim as
 * the multipart field names — the frontend never renames an Action's input.
 */
export async function createRun({ actionId, files = {}, signal } = {}) {
  const formData = new FormData();
  formData.append("action_id", actionId);
  for (const [slotId, file] of Object.entries(files)) {
    if (file) formData.append(slotId, file);
  }
  return request("/api/runs", { method: "POST", body: formData, signal });
}

/**
 * Return one page of a Run's result table (build plan 6E.2, 6E.3).
 *
 * Only the requested page crosses the network: the backend slices the result
 * frame it is holding and serves at most `limit` rows, so a large result is
 * never sent to the browser in order to show the first hundred rows of it.
 */
export async function fetchPreview({
  runId,
  outputId,
  offset = 0,
  limit = DEFAULT_PREVIEW_LIMIT,
  signal,
} = {}) {
  const query = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  return request(
    `/api/runs/${encodeURIComponent(runId)}/outputs/${encodeURIComponent(
      outputId,
    )}/preview?${query}`,
    { signal },
  );
}

/**
 * The address one result table is downloaded from (build plan 6F.1, 6F.2).
 *
 * A URL rather than a request: the browser follows it as an ordinary
 * navigation, so the file is streamed straight to the user's downloads folder
 * and never becomes a copy of the result held in page memory. The backend
 * names the file through `Content-Disposition` (build plan 6F.6) — forwarded
 * unchanged by the same-origin handler — which is also why no `download`
 * attribute is needed at the link.
 */
export function outputDownloadUrl({ runId, outputId, format }) {
  return (
    `${API_BASE_PATH}/api/runs/${encodeURIComponent(runId)}` +
    `/outputs/${encodeURIComponent(outputId)}` +
    `/download/${encodeURIComponent(format)}`
  );
}

/**
 * The address a Run's complete workbook is downloaded from (build plan 6F.4).
 *
 * Every result table of the Run, one worksheet each. Only meaningful for an
 * Action that produced more than one table; the caller decides when to offer
 * it.
 */
export function runWorkbookUrl({ runId }) {
  return `${API_BASE_PATH}/api/runs/${encodeURIComponent(runId)}/download/xlsx`;
}

/** Report whether the backend answers `GET /health`. */
export async function fetchHealth({ signal } = {}) {
  const payload = await request("/health", { signal });
  return payload?.status === "ok";
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/**
 * Issue one same-origin request, converting any failure into an {@link ApiError}.
 *
 * An `AbortError` is re-thrown untouched: a cancelled request is the caller's
 * own doing, not a backend failure.
 */
async function request(path, { method = "GET", body, signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_PATH}${path}`, {
      method,
      body,
      signal,
      cache: "no-store",
    });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    // Same-origin, so this is the server that served the page failing to
    // answer at all — a dropped connection rather than a backend problem.
    throw new ApiError(
      "Could not reach ForgeXL. Check your connection to the server and reload the page.",
      { code: NETWORK_ERROR_CODE },
    );
  }

  if (!response.ok) throw await errorFrom(response);

  try {
    return await response.json();
  } catch {
    throw new ApiError(
      "The backend returned a response that could not be read.",
      {
        status: response.status,
      },
    );
  }
}

/**
 * Build an {@link ApiError} from a non-2xx response.
 *
 * The backend renders `{"error": {code, message, details}}` for everything it
 * raises deliberately. A response that does not match — a framework-generated
 * error, say — still produces a readable message rather than `[object Object]`.
 */
async function errorFrom(response) {
  let payload = null;
  try {
    payload = (await response.json())?.error;
  } catch {
    payload = null;
  }

  // A 5xx that does not carry the backend's own error object never reached a
  // working FastAPI: the same-origin handler answers 502 with a plain body when
  // it cannot connect (build plan 6G.9, "disconnected backend"). Every error
  // FastAPI raises deliberately — including a failed Action, which is also
  // 500 — arrives structured and is reported in its own words instead.
  if (payload === null && response.status >= 500) {
    return new ApiError(
      "The ForgeXL backend did not respond. Check that it is running on the machine serving this page.",
      { status: response.status, code: NETWORK_ERROR_CODE },
    );
  }

  const code = textOr(payload?.code, UNEXPECTED_ERROR_CODE);
  const message = textOr(
    payload?.message,
    `The backend returned an unexpected ${response.status} response.`,
  );
  const details = objectOr(payload?.details);

  return new ApiError(message, {
    status: response.status,
    code,
    details,
    issues: issuesFrom(code, message, details),
  });
}

/**
 * Flatten a backend error into a list of individual issues.
 *
 * A Run that fails several checks at once reports them together under
 * `details.issues`; a single failure is reported directly. Both become the
 * same list so the UI has one thing to render.
 */
function issuesFrom(code, message, details) {
  const reported = Array.isArray(details.issues) ? details.issues : [];
  if (reported.length === 0) {
    return [{ code, message, details, slotId: textOr(details.slot_id, null) }];
  }
  return reported.map((issue) => ({
    code: textOr(issue?.code, code),
    message: textOr(issue?.message, message),
    details: objectOr(issue?.details),
    slotId: textOr(issue?.slot_id, null),
  }));
}

function textOr(value, fallback) {
  return typeof value === "string" && value.trim() ? value : fallback;
}

function objectOr(value) {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value
    : {};
}
