/**
 * Next.js configuration (build plan Phase 6G).
 *
 * The browser never addresses FastAPI. Every backend request the frontend makes
 * is same-origin — `/forge-api/<path>` — and the Next.js server forwards it to
 * FastAPI on loopback (Phase 6 architectural rules 7-10). That indirection is
 * what lets a second laptop on the LAN use ForgeXL with a browser alone: it
 * reaches Next.js, and only the development machine reaches FastAPI (rules
 * 11-13).
 *
 * **The forwarding is not configured here.** It is a Node Route Handler at
 * `src/app/forge-api/[...path]/route.js`, and the backend's address lives in
 * `src/lib/backend-origin.js`, which is server-only. This file deliberately
 * holds no `rewrites()` entry: an external rewrite is served by the Next.js
 * router, which forwards a cloned copy of the request body buffered up to
 * `proxyClientMaxBodySize` (10 MiB by default) and, beyond that, silently
 * forwards a truncated body rather than refusing it. ForgeXL accepts uploads up
 * to 250 MB and answers an oversized one with a structured 413, so a rewrite
 * cannot express its upload contract. The Route Handler is handed the request's
 * own stream and forwards it unbuffered.
 */

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
};

export default nextConfig;
