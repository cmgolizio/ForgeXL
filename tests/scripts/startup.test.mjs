import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:net";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { inspectSetup, repository, requireFreePort, validateNode, webPort } from "../../scripts/check-setup.mjs";

test("startup enforces supported Node versions and integer web ports", () => {
  assert.throws(() => validateNode("18.20.0"), /20.9/);
  assert.throws(() => validateNode("20.8.0"), /20.9/);
  validateNode("20.9.0"); validateNode("24.19.0");
  assert.equal(webPort({}), 3000);
  assert.equal(webPort({ FORGEXL_WEB_PORT: "3030" }), 3030);
  for (const value of ["0", "65536", "3.5", "abc", ""]) assert.throws(() => webPort({ FORGEXL_WEB_PORT: value }));
});

test("missing frontend setup has actionable instructions and writes nothing", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "forgexl-setup-test-"));
  try { await assert.rejects(inspectSetup({ root }), /npm ci/); }
  finally { await rm(root, { recursive: true, force: true }); }
});

test("an occupied port is refused without stopping its owner", async () => {
  const server = createServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    await assert.rejects(requireFreePort("127.0.0.1", server.address().port), /unavailable/);
    assert.equal(server.listening, true);
  } finally { await new Promise((resolve) => server.close(resolve)); }
});

test("production preflight refuses exposed backend and mismatched explicit origin", async () => {
  await assert.rejects(inspectSetup({ root: repository,
    environment: { ...process.env, FORGEXL_BACKEND_HOST: "0.0.0.0" } }), /loopback/);
  await assert.rejects(inspectSetup({ root: repository, production: true,
    environment: { ...process.env, FORGEXL_BACKEND_HOST: "127.0.0.1", FORGEXL_BACKEND_PORT: "8000",
      FORGEXL_BACKEND_ORIGIN: "http://[::1]:8000" } }), /does not point/);
});
