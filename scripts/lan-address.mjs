/**
 * Print the LAN URLs a second laptop can use to open ForgeXL (build plan 6G.6).
 *
 * Run by `npm run dev:lan` before `next dev` binds to every interface. It is
 * informational only: nothing here starts, configures or exposes a server. It
 * exists because build plan 6G's manual test begins "open
 * http://<development-machine>:3000", and the tester has to be told what that
 * address is.
 *
 * Only private (RFC 1918) IPv4 addresses are listed. A public address would
 * not be a trusted local network, and build plan 6G.10 rules out public
 * deployment.
 */

import { networkInterfaces } from "node:os";

const PORT = process.env.PORT?.trim() || "3000";

/** True for the RFC 1918 private ranges, i.e. an ordinary home/office LAN. */
function isPrivateIPv4(address) {
  const [first, second] = address.split(".").map(Number);
  if (first === 10) return true;
  if (first === 192 && second === 168) return true;
  return first === 172 && second >= 16 && second <= 31;
}

function privateAddresses() {
  return Object.entries(networkInterfaces()).flatMap(([name, entries]) =>
    (entries ?? [])
      .filter(
        (entry) =>
          entry.family === "IPv4" &&
          !entry.internal &&
          isPrivateIPv4(entry.address),
      )
      .map((entry) => ({ name, address: entry.address })),
  );
}

const found = privateAddresses();

console.log("");
console.log("  ForgeXL — LAN development mode");
console.log("");
console.log(`  This machine     http://127.0.0.1:${PORT}`);

if (found.length === 0) {
  console.log(
    "  Second laptop    no private IPv4 address found on this machine",
  );
  console.log(
    "                   (check that it is connected to the local network)",
  );
} else {
  for (const [index, { name, address }] of found.entries()) {
    const label = index === 0 ? "Second laptop" : "             ";
    console.log(`  ${label}    http://${address}:${PORT}   (${name})`);
  }
}

console.log("");
console.log("  Next.js is reachable on the local network; FastAPI stays on");
console.log("  127.0.0.1 and is not. The second laptop needs only a browser.");
console.log("");
