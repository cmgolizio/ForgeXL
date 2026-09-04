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
 *
 * **This script never fails the run.** It is chained ahead of `next dev` with
 * `&&`, so a non-zero exit would stop the development server from starting over
 * nothing more than an unprintable address. Every failure path below prints
 * what it can and exits 0.
 */

import { networkInterfaces } from "node:os";

/**
 * Port to advertise.
 *
 * Taken from the command line — `npm run dev:lan` passes the same number it
 * passes to `next dev --port`, so the two can only disagree by editing one line
 * of `package.json` and not the other. `DEFAULT_PORT` matches that script and
 * stands in when the argument is absent or is not a port.
 *
 * Deliberately **not** read from `process.env.PORT`: `next dev` is launched with
 * an explicit `--port`, so a `PORT` variable that happens to be set in the shell
 * would change nothing about where Next.js listens while changing every URL
 * printed here into one that answers nothing.
 */
const DEFAULT_PORT = "3000";

function requestedPort(argument) {
  const port = Number(argument);
  return Number.isInteger(port) && port > 0 && port < 65536
    ? String(port)
    : DEFAULT_PORT;
}

const PORT = requestedPort(process.argv[2]);

/** True for the RFC 1918 private ranges, i.e. an ordinary home/office LAN. */
function isPrivateIPv4(address) {
  const [first, second] = address.split(".").map(Number);
  if (first === 10) return true;
  if (first === 192 && second === 168) return true;
  return first === 172 && second >= 16 && second <= 31;
}

/**
 * Every private IPv4 address this machine answers on.
 *
 * `networkInterfaces()` reads the OS's interface table and can fail — a
 * container with the syscall blocked, a permissions error, a platform quirk.
 * Enumerating an address is a convenience, not a prerequisite, so a failure
 * returns `null` ("could not look") rather than `[]` ("looked, found none"),
 * and the two are reported differently below.
 */
function privateAddresses() {
  let interfaces;
  try {
    interfaces = networkInterfaces();
  } catch {
    return null;
  }

  try {
    return Object.entries(interfaces).flatMap(([name, entries]) =>
      (entries ?? [])
        .filter(
          (entry) =>
            entry.family === "IPv4" &&
            !entry.internal &&
            isPrivateIPv4(entry.address),
        )
        .map((entry) => ({ name, address: entry.address })),
    );
  } catch {
    return null;
  }
}

const found = privateAddresses();

console.log("");
console.log("  ForgeXL — LAN development mode");
console.log("");
console.log(`  This machine     http://127.0.0.1:${PORT}`);

if (found === null) {
  console.log(
    "  Second laptop    this machine's network addresses could not be read",
  );
  console.log(
    "                   (find this machine's LAN address yourself, then",
  );
  console.log(`                   open http://<that-address>:${PORT})`);
} else if (found.length === 0) {
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
