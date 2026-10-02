// Local preflight only: no installs, telemetry, business-data reads or writes.
import { access, readFile } from "node:fs/promises";
import { constants } from "node:fs";
import { createServer } from "node:net";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const repository = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

export function validateNode(version = process.versions.node) {
  const [major, minor] = version.split(".").map(Number);
  if (major < 20 || (major === 20 && minor < 9)) {
    throw new Error("ForgeXL requires Node.js 20.9 or newer. Install it, then run npm ci.");
  }
}

export function webPort(environment = process.env) {
  const value = environment.FORGEXL_WEB_PORT ?? "3000";
  if (!/^\d+$/.test(value) || Number(value) < 1 || Number(value) > 65535) {
    throw new Error("FORGEXL_WEB_PORT must be an integer from 1 to 65535.");
  }
  return Number(value);
}

export async function requireFreePort(host, port) {
  await new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", () => reject(new Error(`Port ${port} on ${host} is unavailable. Close the existing server or choose another port; ForgeXL will not stop it for you.`)));
    server.listen({ host, port, exclusive: true }, () => server.close(resolve));
  });
}

export async function inspectSetup({ root = repository, environment = process.env, production = false } = {}) {
  validateNode();
  const port = webPort(environment);
  const next = path.join(root, "node_modules/next/dist/bin/next");
  try { await access(next); }
  catch { throw new Error("Frontend dependencies are missing. From the ForgeXL folder, run npm ci."); }
  const python = path.join(root, "backend/.venv/bin/python");
  try { await access(python, constants.X_OK); }
  catch { throw new Error("Backend setup is missing. Run python3 -m venv backend/.venv, then backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt."); }
  const result = spawnSync(python, ["-c", [
    "import sys, json",
    "assert sys.version_info >= (3, 10), 'Python 3.10 or newer is required'",
    "import fastapi, uvicorn, polars, fastexcel, openpyxl, xlsxwriter, python_multipart",
    "from app import config",
    "print(json.dumps({'host': config.HOST, 'port': config.PORT, 'python_version': sys.version.split()[0]}))",
  ].join("\n")], { cwd: path.join(root, "backend"), env: environment, encoding: "utf8", timeout: 15000 });
  if (result.error || result.status !== 0) {
    throw new Error("Backend dependencies could not be loaded. Run backend/.venv/bin/python -m pip install -r backend/requirements.lock.txt.\n" + (result.error?.message ?? result.stderr.trim()));
  }
  const backend = JSON.parse(result.stdout);
  if (!["127.0.0.1", "localhost", "::1"].includes(backend.host)) {
    throw new Error("Normal startup requires a loopback FastAPI host. Remove FORGEXL_BACKEND_HOST or set it to 127.0.0.1. For trusted LAN testing use npm run dev:lan instead.");
  }
  if (!Number.isInteger(backend.port) || backend.port < 1 || backend.port > 65535 || backend.port === port) {
    throw new Error("Use distinct valid web and backend ports. FORGEXL_BACKEND_PORT defaults to 8000.");
  }
  if (production) {
    if (environment.FORGEXL_BACKEND_ORIGIN) {
      let explicit;
      try { explicit = new URL(environment.FORGEXL_BACKEND_ORIGIN); }
      catch { throw new Error("FORGEXL_BACKEND_ORIGIN must be a valid loopback URL, or remove it to use the default."); }
      if (explicit.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(explicit.hostname) ||
          explicit.hostname !== (backend.host === "::1" ? "[::1]" : backend.host) || Number(explicit.port || 80) !== backend.port || explicit.pathname !== "/" || explicit.username || explicit.password || explicit.search || explicit.hash) {
        throw new Error("FORGEXL_BACKEND_ORIGIN does not point to the local backend port. Remove it to use the shared backend host and port settings.");
      }
    }
    try { if (!(await readFile(path.join(root, ".next/BUILD_ID"), "utf8")).trim()) throw new Error(); }
    catch { throw new Error("The production build is missing. Run npm run build once, and again after updating ForgeXL."); }
  }
  return { root, python, next, backend, port, url: `http://127.0.0.1:${port}` };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const setup = await inspectSetup({ production: process.argv.includes("--production") });
    console.log(`Setup ready: Node ${process.versions.node}, Python ${setup.backend.python_version}. Web ${setup.port}, private backend ${setup.backend.port}.`);
    console.log("This check did not create, modify or inspect your business data.");
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
