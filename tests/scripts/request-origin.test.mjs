import assert from "node:assert/strict";
import { test } from "node:test";
import { sameOriginWrite } from "../../src/lib/request-origin.js";

test("browser writes use the visible ForgeXL origin, including a LAN host", () => {
  function request(headers) { return new Request("http://127.0.0.1:3000/forge-api/api/monthly/validate", { method: "POST", headers }); }
  assert.equal(sameOriginWrite(request({})), true);
  assert.equal(sameOriginWrite(request({ origin: "http://127.0.0.1:3000" })), true);
  assert.equal(sameOriginWrite(request({ origin: "http://192.168.1.50:3000", host: "192.168.1.50:3000" })), true);
  assert.equal(sameOriginWrite(request({ origin: "https://untrusted.example" })), false);
  assert.equal(sameOriginWrite(request({ origin: "null" })), false);
  assert.equal(sameOriginWrite(request({ "sec-fetch-site": "cross-site" })), false);
  assert.equal(sameOriginWrite(request({ origin: "http://127.0.0.1:3001" })), false);
});
