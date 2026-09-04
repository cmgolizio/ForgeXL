import { backendOrigin } from "@/lib/backend-origin";

/**
 * The same-origin transport to FastAPI (build plan 6G.2-6G.5).
 *
 *     browser  ->  http://<dev-machine>:3000/forge-api/api/runs
 *     Next.js  ->  http://127.0.0.1:8000/api/runs
 *
 * Every backend request the browser makes is addressed to `/forge-api/...` on
 * whatever host served the page, and this handler is the one place that knows
 * where those requests actually go. FastAPI stays on loopback and the second
 * laptop needs nothing but a browser (Phase 6 architectural rules 7-13).
 *
 * **A Route Handler rather than a `rewrites()` entry.** An external rewrite is
 * served by the Next.js router, which forwards a *cloned, buffered* copy of the
 * request body — capped at `proxyClientMaxBodySize`, 10 MiB by default. Past
 * that cap the body is not rejected: Next logs a warning, truncates the stream
 * and forwards the first 10 MiB, so FastAPI receives a multipart body that ends
 * mid-part and waits for the rest that never arrives. Measured with a valid
 * 12.38 MB CSV: Next warned, the upload hung, and the request ended in a
 * `ClientDisconnect`. ForgeXL's own limit is 250 MB with a structured 413
 * (`config.MAX_UPLOAD_BYTES`, build plan 3.3), and a silently truncated upload
 * is the exact opposite of that contract.
 *
 * A Route Handler is handed the request's own stream, so this file forwards it
 * unbuffered and unread. Raising the rewrite's cap was not the fix: it would
 * still buffer the whole upload in the Node process, and a 250 MB file would
 * then be held in memory twice.
 *
 * **Transport only (build plan 6G.4).** The body is never read, parsed or
 * transformed here. There is exactly one spreadsheet implementation in ForgeXL
 * and it is in Python: no CSV or XLSX is parsed in Node, and the bytes the
 * browser sent are the bytes FastAPI receives.
 *
 * The `/forge-api` prefix this file is mounted under is the browser half of the
 * same contract, held as `API_BASE_PATH` in `src/lib/api.js`. Both are fixed by
 * build plan 6G.2; neither is configurable, because a same-origin path has no
 * address to configure. The prefix is the route's own directory name here, so
 * moving this file is what moves the namespace.
 */

/**
 * Headers that describe *this* connection and must not be forwarded onto the
 * next one.
 *
 * The hop-by-hop headers of RFC 9110 §7.6.1, plus three that are equally
 * connection-scoped here:
 *
 * * `host` — would name the Next.js server to FastAPI. `fetch` sets the right
 *   one for the upstream connection.
 * * `expect` — a `100-continue` negotiated with the browser is already settled
 *   by the time this handler runs; replaying it upstream would stall the
 *   forwarded request.
 * * `content-length` — the body is re-framed as a chunked upstream request, so
 *   the browser's own framing header would contradict it.
 */
const CONNECTION_HEADERS = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "host",
  "expect",
  "content-length",
]);

/**
 * Response headers forwarded back to the browser.
 *
 * `content-type` so a JSON body is read as JSON, and `content-disposition` so a
 * download arrives as an attachment under the filename the backend chose
 * (build plan 6F.6). Everything else describes the FastAPI connection, and
 * Node sets the browser's own framing headers for the streamed response.
 */
const FORWARDED_RESPONSE_HEADERS = ["content-type", "content-disposition"];

/**
 * The ForgeXL API is GET and POST, and so is FastAPI's CORS allowlist
 * (`main.py`). Every other method is answered 405 by Next.js, because it is not
 * exported here.
 */
export async function GET(request, context) {
  return forward(request, context);
}

export async function POST(request, context) {
  return forward(request, context);
}

/** Never prerendered or cached: this is a proxy, and every request is live. */
export const dynamic = "force-dynamic";

/** Node, for `fetch` with a streamed request body (`duplex: "half"`). */
export const runtime = "nodejs";

/**
 * Stream one request through to FastAPI and stream its response back.
 *
 * Nothing about the request is inspected beyond its path, its query string and
 * its headers. In particular `request.body` is passed straight to `fetch` and
 * is never awaited, read or parsed: `request.formData()` here would buffer an
 * entire 250 MB upload in the Node process and give ForgeXL a second place
 * that understands multipart uploads.
 */
async function forward(request, context) {
  const { path } = await context.params;

  let upstream;
  try {
    upstream = await fetch(upstreamUrl(request, path), {
      method: request.method,
      headers: forwardedRequestHeaders(request.headers),
      body: request.body,
      // Required by `fetch` whenever the body is a stream: it says this request
      // starts sending before the response has begun, which is what makes the
      // upload flow through rather than being buffered first.
      ...(request.body ? { duplex: "half" } : {}),
      // A cancelled preview or a closed tab should not leave a Run executing
      // in FastAPI with nobody to answer.
      signal: request.signal,
      redirect: "manual",
      cache: "no-store",
    });
  } catch (cause) {
    if (request.signal.aborted) {
      // The browser hung up first — a cancelled request, a reloaded page. There
      // is no longer anyone to answer.
      return new Response(null, { status: 499 });
    }
    // The backend is not running, or refused the connection. Deliberately a
    // plain body with no `error` object: `src/lib/api.js` turns any 5xx that
    // does not carry the backend's own structured error into its readable
    // NETWORK_ERROR, which is the right thing to tell the user here.
    console.error("ForgeXL backend unreachable:", cause);
    return new Response("The ForgeXL backend could not be reached.\n", {
      status: 502,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: forwardedResponseHeaders(upstream.headers),
  });
}

/**
 * The FastAPI URL one `/forge-api/...` request maps to.
 *
 * The captured segments arrive decoded, so each is re-encoded on the way out:
 * a segment can then never contribute a `/` or a `?` of its own and reshape the
 * upstream URL. The query string is copied verbatim — `?offset=100&limit=100`
 * is the preview's paging and has to survive the hop unchanged.
 */
function upstreamUrl(request, path) {
  const segments = Array.isArray(path) ? path : [path];
  const forwardedPath = segments.map(encodeURIComponent).join("/");
  return `${backendOrigin()}/${forwardedPath}${request.nextUrl.search}`;
}

/** The request's headers, less the ones that belong to the browser's connection. */
function forwardedRequestHeaders(headers) {
  const forwarded = new Headers();
  for (const [name, value] of headers) {
    if (!CONNECTION_HEADERS.has(name.toLowerCase()))
      forwarded.append(name, value);
  }
  return forwarded;
}

/** The upstream response's headers, less the ones that described its connection. */
function forwardedResponseHeaders(headers) {
  const forwarded = new Headers();
  for (const name of FORWARDED_RESPONSE_HEADERS) {
    const value = headers.get(name);
    if (value !== null) forwarded.set(name, value);
  }
  return forwarded;
}
