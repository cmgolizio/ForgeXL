// Supervise the two local production servers. No shell, reload worker or daemon.
import { spawn } from "node:child_process";
import { setTimeout as pause } from "node:timers/promises";
import path from "node:path";
import { inspectSetup, requireFreePort } from "./check-setup.mjs";

const children = [];
let stopping = false;
let exitCode = 0;
let finished;
const completion = new Promise((resolve) => { finished = resolve; });

async function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  exitCode = code;
  await Promise.all(children.map((child) => new Promise((resolve) => {
    if (!child.pid || child.exitCode !== null || child.signalCode !== null) { resolve(); return; }
    const timer = setTimeout(() => child.kill("SIGKILL"), 5000);
    child.once("exit", () => { clearTimeout(timer); resolve(); });
    child.kill("SIGTERM");
  })));
  finished();
}

function start(label, command, args, options) {
  const child = spawn(command, args, { ...options, stdio: "inherit" });
  children.push(child);
  child.once("error", (error) => {
    console.error(`${label} could not start: ${error.message}`);
    void stop(1);
  });
  child.once("exit", (code, signal) => {
    if (!stopping) {
      console.error(`${label} stopped (${signal ?? code}). Stopping the other ForgeXL server.`);
      void stop(1);
    }
  });
}

process.once("SIGINT", () => { void stop(0); });
process.once("SIGTERM", () => { void stop(0); });

try {
  const setup = await inspectSetup({ production: true });
  await Promise.all([requireFreePort("127.0.0.1", setup.port), requireFreePort(setup.backend.host, setup.backend.port)]);
  if (!stopping) {
    const environment = { ...process.env, NEXT_TELEMETRY_DISABLED: "1",
      FORGEXL_BACKEND_HOST: setup.backend.host, FORGEXL_BACKEND_PORT: String(setup.backend.port) };
    start("FastAPI", setup.python, ["-m", "app.main", "--no-reload"], { cwd: path.join(setup.root, "backend"), env: environment });
    start("Next.js", process.execPath, [setup.next, "start", "--hostname", "127.0.0.1", "--port", String(setup.port)], { cwd: setup.root, env: environment });
    let ready = false;
    const deadline = Date.now() + 45000;
    while (!stopping && Date.now() < deadline) {
      try {
        const response = await fetch(`${setup.url}/forge-api/health`, { signal: AbortSignal.timeout(1000), cache: "no-store" });
        ready = response.ok && (await response.json()).status === "ok";
        if (ready) break;
      } catch { /* The servers are still starting. */ }
      await pause(150);
    }
    if (!stopping && !ready) throw new Error("ForgeXL did not become ready within 45 seconds. Check the server messages above.");
    if (!stopping) {
      console.log(`\nForgeXL ready: ${setup.url}\nBoth servers are local. Press Control-C in this window to stop them.\n`);
      if (process.argv.includes("--open")) {
        if (process.platform === "darwin") {
          const opener = spawn("open", [setup.url], { stdio: "ignore" });
          opener.once("error", () => console.error(`Open ${setup.url} in your browser.`));
          opener.once("exit", (code) => { if (code) console.error(`Open ${setup.url} in your browser.`); });
        } else console.log(`Automatic browser opening is Mac-only. Open ${setup.url} in your browser.`);
      }
    }
  }
} catch (error) { console.error(error.message); await stop(1); }

await completion;
process.exitCode = exitCode;
