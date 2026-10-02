// Transport safety, not authentication. Browser writes must come from this page.
export function sameOriginWrite(request) {
  if (request.headers.get("sec-fetch-site") === "cross-site") return false;
  const origin = request.headers.get("origin");
  if (!origin) return true; // Local command-line/test clients have no browser Origin.
  try {
    const incoming = new URL(request.url);
    const host = request.headers.get("host") || incoming.host;
    return new URL(origin).origin === `${incoming.protocol}//${host}`;
  } catch { return false; }
}
