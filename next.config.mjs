/**
 * Next.js configuration (build plan Phase 6G).
 *
 * The browser never addresses FastAPI. Every backend request the frontend makes
 * is same-origin — `/forge-api/<path>` — and this file is the one place that
 * knows where those requests actually go (Phase 6 architectural rules 7-10).
 * Because the rewrite is resolved by the Next.js server, the backend address
 * stays server-side and is never inlined into the browser bundle.
 *
 *     browser  ->  http://<dev-machine>:3000/forge-api/api/runs
 *     Next.js  ->  http://127.0.0.1:8000/api/runs
 *
 * That indirection is what lets a second laptop on the LAN use ForgeXL with a
 * browser alone: it reaches Next.js, and only the development machine reaches
 * FastAPI on loopback (rules 11-13).
 */

/**
 * Same-origin namespace for backend requests.
 *
 * Kept in step with `API_BASE_PATH` in `src/lib/api.js`, which is the browser
 * half of the same contract. Both are fixed by build plan 6G.2; neither is
 * configurable, because a same-origin path has no address to configure.
 */
const FORGE_API_BASE_PATH = "/forge-api";

/** Where the FastAPI development server listens, by build plan default. */
const DEFAULT_BACKEND_HOST = "127.0.0.1";
const DEFAULT_BACKEND_PORT = "8000";

/**
 * Hosts allowed to load Next.js development assets from another device.
 *
 * `next dev` refuses cross-origin requests for its own dev-only resources
 * (HMR, `/_next/*`) unless the requesting host is listed here, so without this
 * the page loads from the second laptop but its client bundle does not. The
 * defaults are the private IPv4 ranges of RFC 1918 plus mDNS `.local` names,
 * which is how a machine on a trusted LAN is actually addressed. Patterns are
 * matched per dot-separated segment, so `192.168.*.*` covers that whole range.
 *
 * This only ever relaxes `next dev`. It grants nothing in a production build,
 * and it does not expose FastAPI, which stays bound to loopback either way.
 */
const PRIVATE_NETWORK_ORIGINS = [
  "127.0.0.1",
  "10.*.*.*",
  "192.168.*.*",
  // 172.16.0.0/12 is 172.16.* through 172.31.*, which segment matching cannot
  // express as one pattern without also allowing the public rest of 172.*.
  ...Array.from({ length: 16 }, (_, index) => `172.${16 + index}.*.*`),
  "*.local",
];

/**
 * Origin the Next.js server proxies `/forge-api/*` to.
 *
 * Derived from the same `FORGEXL_BACKEND_HOST` / `FORGEXL_BACKEND_PORT`
 * variables `backend/app/config.py` reads, so moving the backend port does not
 * require setting it twice. `FORGEXL_BACKEND_ORIGIN` overrides the whole URL.
 */
function backendOrigin() {
  const explicit = process.env.FORGEXL_BACKEND_ORIGIN?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const host = process.env.FORGEXL_BACKEND_HOST?.trim() || DEFAULT_BACKEND_HOST;
  const port = process.env.FORGEXL_BACKEND_PORT?.trim() || DEFAULT_BACKEND_PORT;

  // 0.0.0.0 is an address to listen on, not one to dial; a backend bound to it
  // is still reached over loopback.
  return `http://${host === "0.0.0.0" ? DEFAULT_BACKEND_HOST : host}:${port}`;
}

/** Extra hosts for `allowedDevOrigins`, e.g. `FORGEXL_DEV_ALLOWED_ORIGINS=my-mac.lan`. */
function extraDevOrigins() {
  return (process.env.FORGEXL_DEV_ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((origin) => origin.trim())
    .filter(Boolean);
}

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactCompiler: true,

  allowedDevOrigins: [...PRIVATE_NETWORK_ORIGINS, ...extraDevOrigins()],

  /**
   * Transport only (build plan 6G.4).
   *
   * The request body is forwarded untouched: an uploaded workbook is passed
   * straight through to FastAPI, and no CSV or XLSX is ever parsed here. There
   * is exactly one spreadsheet implementation in ForgeXL and it is in Python.
   */
  rewrites() {
    return [
      {
        source: `${FORGE_API_BASE_PATH}/:path*`,
        destination: `${backendOrigin()}/:path*`,
      },
    ];
  },
};

export default nextConfig;
