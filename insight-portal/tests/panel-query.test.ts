import assert from "node:assert/strict";
import { test } from "node:test";

import { decodePanelQuery, encodePanelQuery, exportHref } from "../src/lib/panel.ts";

/**
 * The export link carries a query, and the point of these tests is what it CANNOT carry.
 *
 * A tenant in the encoded blob would be inert - `query()` resolves the tenant from the verified
 * session and `PanelQuery` has no tenant field - but the decoder is asserted to drop unknown keys
 * anyway, so a future reader cannot mistake the blob for a place where scope is decided.
 */

test("a panel query round trips through the encoding", () => {
  const query = {
    metric: "revenue_net",
    dimensions: ["date_month"],
    filters: { date_range: ["2025-09-01", "2026-08-31"] as [string, string] },
    order_by: "-value",
    limit: 500,
  };
  assert.deepEqual(decodePanelQuery(encodePanelQuery(query)), query);
});

test("the encoding is URL safe", () => {
  const encoded = encodePanelQuery({
    metric: "account_balance",
    dimensions: ["account_id", "date_month"],
    filters: { date_range: ["2025-09-01", "2026-08-31"] as [string, string] },
  });
  assert.match(encoded, /^[A-Za-z0-9_-]+$/);
});

test("a decoded query never carries a tenant, whatever was encoded into it", () => {
  const smuggled = { metric: "revenue_net", dimensions: [], filters: {}, tenant_id: "bct_t2" };
  const blob = Buffer.from(JSON.stringify(smuggled), "utf8")
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
  const decoded = decodePanelQuery(blob);
  assert.notEqual(decoded, null);
  assert.equal(
    Object.prototype.hasOwnProperty.call(decoded, "tenant_id"),
    false,
    "the decoder must drop anything that is not one of the five contract 06 fields",
  );
});

test("junk decodes to null rather than to a partial query", () => {
  for (const junk of ["", "!!!!", "e30", "bm90LWpzb24"]) {
    const decoded = decodePanelQuery(junk);
    assert.ok(decoded === null || typeof decoded.metric === "string");
  }
  assert.equal(decodePanelQuery("e30"), null, "an empty object has no metric and must be rejected");
});

test("a non-integer limit is dropped rather than passed through", () => {
  const blob = Buffer.from(
    JSON.stringify({ metric: "revenue_net", dimensions: [], filters: {}, limit: 1.5 }),
    "utf8",
  )
    .toString("base64url");
  const decoded = decodePanelQuery(blob);
  assert.equal(Object.prototype.hasOwnProperty.call(decoded, "limit"), false);
});

test("the export href names a format and a file, and nothing about a tenant", () => {
  const href = exportHref(
    { metric: "revenue_net", dimensions: [], filters: {} },
    "csv",
    "pendapatan",
  );
  assert.match(href, /^\/api\/export\?/);
  assert.match(href, /format=csv/);
  assert.equal(href.includes("tenant"), false);
});
