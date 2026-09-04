import "server-only";

/**
 * Where the ForgeXL backend actually listens (build plan 6G.3, 6G.5).
 *
 * This is the only module in the repository that knows FastAPI's address, and
 * it is **server-only**: the `server-only` import above makes importing it from
 * a Client Component a build error, so the hostname and port can never reach
 * the browser bundle. That is the whole point of Phase 6 architectural rules
 * 7-9 — the browser addresses ForgeXL as the same-origin path `/forge-api/...`
 * and never learns that FastAPI exists, which is what lets a second laptop on
 * the LAN use ForgeXL with nothing but a browser.
 *
 * Only `src/app/forge-api/[...path]/route.js` imports this.
 */

/** Where the FastAPI development server listens, by build plan default. */
const DEFAULT_BACKEND_HOST = "127.0.0.1";
const DEFAULT_BACKEND_PORT = "8000";

/**
 * Origin the `/forge-api/*` route handler forwards to.
 *
 * Derived from the same `FORGEXL_BACKEND_HOST` / `FORGEXL_BACKEND_PORT`
 * variables `backend/app/config.py` reads, so moving the backend port does not
 * require setting it twice. `FORGEXL_BACKEND_ORIGIN` overrides the whole URL.
 *
 * Read per request rather than captured at module load: none of these is a
 * `NEXT_PUBLIC_` variable, so none is inlined anywhere, and reading them at
 * call time keeps `next dev` from having to be restarted after an env change.
 */
export function backendOrigin() {
  const explicit = process.env.FORGEXL_BACKEND_ORIGIN?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const host = process.env.FORGEXL_BACKEND_HOST?.trim() || DEFAULT_BACKEND_HOST;
  const port = process.env.FORGEXL_BACKEND_PORT?.trim() || DEFAULT_BACKEND_PORT;

  // 0.0.0.0 is an address to listen on, not one to dial; a backend bound to it
  // is still reached over loopback.
  return `http://${host === "0.0.0.0" ? DEFAULT_BACKEND_HOST : host}:${port}`;
}
