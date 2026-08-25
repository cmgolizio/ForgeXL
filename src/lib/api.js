/**
 * The single place the browser talks to the FastAPI backend (build plan 5.1).
 *
 * Every request goes straight from the browser to FastAPI. Nothing is proxied
 * through a Next.js Route Handler, so an uploaded file is copied exactly once
 * (build plan section 5).
 *
 * No backend URL and no endpoint path appears anywhere else in the frontend.
 * This module is browser-only by design: it uses `fetch` and `FormData` and
 * imports no Node modules, so it is safe in a client component.
 */

const DEFAULT_API_BASE_URL = "http://127.0.0.1:8000";

/**
 * Base URL of the local backend.
 *
 * `NEXT_PUBLIC_API_BASE_URL` is inlined at build time; the fallback keeps the
 * app working with no `.env.local` at all. Trailing slashes are trimmed so
 * joining a path can never produce a double slash.
 */
export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || DEFAULT_API_BASE_URL
).replace(/\/+$/, "");

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

/** Report whether the backend answers `GET /health`. */
export async function fetchHealth({ signal } = {}) {
  const payload = await request("/health", { signal });
  return payload?.status === "ok";
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

/**
 * Issue one request, converting any failure into an {@link ApiError}.
 *
 * An `AbortError` is re-thrown untouched: a cancelled request is the caller's
 * own doing, not a backend failure.
 */
async function request(path, { method = "GET", body, signal } = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      body,
      signal,
      cache: "no-store",
    });
  } catch (cause) {
    if (cause?.name === "AbortError") throw cause;
    throw new ApiError(
      `Could not reach the backend at ${API_BASE_URL}. Make sure it is running.`,
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
