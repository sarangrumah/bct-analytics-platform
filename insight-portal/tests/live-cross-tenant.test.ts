import assert from "node:assert/strict";
import { test } from "node:test";

import { LIVE, PORTAL, SEMANTIC, gatewayToken, martRowCount, portalSession } from "./live-helpers.ts";

/**
 * Cross-tenant access returns 403 - the §6 requirement, proven against the running stack.
 *
 * The structure of this file is the point. A 403 is easy to obtain for the wrong reason: a request
 * that never reached the database, a tenant that does not exist, a tenant that exists but holds
 * nothing. So the refusal is asserted only AFTER two facts are established as tests in their own
 * right:
 *
 *   1. the session really is scoped to `bct` and really can read `bct` rows - so the machinery
 *      works at all;
 *   2. `bct_t2` really holds rows in the warehouse - so the refusal is a refusal and not an
 *      absence.
 *
 * `bct_t2` is a warehouse-only fixture tenant loaded by `warehouse_ctl.py load-fixture`; there is
 * no second Odoo database, which is exactly why (2) has to be measured rather than assumed.
 *
 * How the 403 tests were made to go red: the tenant comparison in `src/middleware.ts` was inverted
 * to `match[1] === session.tenant_id`. The portal tests failed (200 instead of 403) while the
 * semantic-API test kept passing, which is the useful part - it shows the two guards are
 * independent and that the portal test is not silently riding on the API's.
 */

const describe = LIVE ? test : test.skip;

describe("cross-tenant precondition: the portal is serving", async () => {
  const response = await fetch(PORTAL + "/healthz");
  assert.equal(response.status, 200, "portal is not answering on " + PORTAL);
});

describe("cross-tenant precondition: a bct session can read bct rows", async () => {
  const token = await gatewayToken("bct");
  const response = await fetch(SEMANTIC + "/v1/query", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer " + token },
    body: JSON.stringify({
      metric: "revenue_net",
      dimensions: ["date_month"],
      filters: { date_range: ["2025-09-01", "2026-08-31"] },
    }),
  });
  assert.equal(response.status, 200);
  const body = (await response.json()) as { rows: unknown[]; meta: { tenant_id: string } };
  assert.equal(body.meta.tenant_id, "bct");
  assert.ok(
    body.rows.length > 0,
    "the session returned no rows at all; a 403 test on top of this would prove nothing",
  );
});

describe("cross-tenant precondition: tenant bct_t2 holds rows, so refusing it is a refusal", () => {
  const other = martRowCount("bct_t2");
  const own = martRowCount("bct");
  assert.ok(other > 0, "bct_t2 holds " + other + " rows; a 403 against an empty tenant is vacuous");
  assert.ok(own > 0, "bct holds " + own + " rows");
});

describe("cross-tenant: the semantic API refuses a bct session asking for bct_t2 with 403", async () => {
  const token = await gatewayToken("bct");
  const response = await fetch(SEMANTIC + "/v1/query", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer " + token },
    body: JSON.stringify({
      metric: "revenue_net",
      dimensions: ["date_month"],
      filters: { date_range: ["2025-09-01", "2026-08-31"], tenant_id: "bct_t2" },
    }),
  });
  assert.equal(response.status, 403);
  const body = await response.json();
  assert.deepEqual(body, {
    error: "tenant_scope_violation",
    detail: "Session is not scoped to the requested tenant.",
  });
});

describe("cross-tenant: the portal refuses /t/bct_t2 for a bct session with 403", async () => {
  const cookie = await portalSession();
  const response = await fetch(PORTAL + "/t/bct_t2/overview", {
    headers: { cookie, accept: "text/html" },
    redirect: "manual",
  });
  assert.equal(response.status, 403, "a mis-aimed tenant URL must be refused, not redirected");
  const html = await response.text();
  assert.match(html, /tenant_scope_violation/);
  assert.equal(
    /44170500|53738500/.test(html),
    false,
    "the refusal page must contain no warehouse figures",
  );
});

describe("cross-tenant: the 403 body is verbatim for a non-HTML caller", async () => {
  const cookie = await portalSession();
  const response = await fetch(PORTAL + "/t/bct_t2/overview", {
    headers: { cookie, accept: "application/json" },
    redirect: "manual",
  });
  assert.equal(response.status, 403);
  assert.deepEqual(await response.json(), {
    error: "tenant_scope_violation",
    detail: "Session is not scoped to the requested tenant.",
  });
});

describe("cross-tenant: the refusal reveals nothing about a tenant that does not exist", async () => {
  const cookie = await portalSession();
  const real = await fetch(PORTAL + "/t/bct_t2/overview", {
    headers: { cookie, accept: "application/json" },
    redirect: "manual",
  });
  const imaginary = await fetch(PORTAL + "/t/no-such-tenant/overview", {
    headers: { cookie, accept: "application/json" },
    redirect: "manual",
  });
  assert.equal(real.status, imaginary.status, "an existing tenant must not be distinguishable");
  assert.deepEqual(await real.json(), await imaginary.json());
});

describe("cross-tenant: an X-Tenant-Id header changes nothing", async () => {
  const token = await gatewayToken("bct");
  const response = await fetch(SEMANTIC + "/v1/query", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      authorization: "Bearer " + token,
      "x-tenant-id": "bct_t2",
    },
    body: JSON.stringify({
      metric: "revenue_net",
      dimensions: [],
      filters: { date_range: ["2025-09-01", "2026-08-31"] },
    }),
  });
  assert.equal(response.status, 200, "the header is ignored, not honoured and not an error");
  const body = (await response.json()) as { meta: { tenant_id: string } };
  assert.equal(body.meta.tenant_id, "bct");
});

describe("cross-tenant: the portal ignores an X-Tenant-Id header on its own routes", async () => {
  const cookie = await portalSession();
  const plain = await fetch(PORTAL + "/t/bct/overview", { headers: { cookie } });
  const spoofed = await fetch(PORTAL + "/t/bct/overview", {
    headers: { cookie, "x-tenant-id": "bct_t2" },
  });
  assert.equal(plain.status, 200);
  assert.equal(spoofed.status, 200);
  const a = await plain.text();
  const b = await spoofed.text();
  assert.equal(
    a.includes("tenant bct_t2"),
    false,
    "the page must never name another tenant as the session tenant",
  );
  assert.equal(b.includes("tenant bct_t2"), false);
});

describe("no bearer token or upstream URL reaches the browser", async () => {
  const cookie = await portalSession();
  const response = await fetch(PORTAL + "/t/bct/overview", { headers: { cookie } });
  const html = await response.text();
  assert.equal(html.includes("eyJhbGciOiJSUzI1NiI"), false, "a JWT must never be in the HTML");
  assert.equal(html.includes("Bearer "), false);
  assert.equal(html.includes("127.0.0.1:38200"), false, "the semantic API URL must stay server-side");
  assert.equal(html.includes("127.0.0.1:38120"), false, "the gateway URL must stay server-side");
  assert.equal(html.includes("postgresql://"), false);
});
