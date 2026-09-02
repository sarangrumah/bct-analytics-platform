import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { test } from "node:test";

import { GATEWAY, LIVE, PORTAL, SEMANTIC, credentials } from "./live-helpers.ts";

/**
 * Operating Unit scoping, proven with a session that is actually restricted.
 *
 * This file exists because the obvious version of it proves nothing. An `all_ou: true` admin token
 * has no Operating Unit predicate applied at all, so every assertion about OU filtering passes
 * whether or not the filtering works - the same vacuity as pointing a tenant-isolation test at a
 * superuser. So every test here uses `demo.ou1@contoh.invalid`, whose token carries
 * `all_ou: false, allowed_ou: [1]`, and the first test asserts that negatively before anything else
 * is claimed.
 *
 * The second precondition matters just as much: Operating Unit 2 must actually hold rows. Hiding
 * rows that do not exist is not scoping.
 */

const describe = LIVE ? test : test.skip;

async function tokenFor(login: string): Promise<string> {
  const { password } = credentials();
  const response = await fetch(GATEWAY + "/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ db: "bct", login, password }),
  });
  assert.equal(response.status, 200, "gateway refused " + login);
  const body = (await response.json()) as { access_token: string };
  return body.access_token;
}

function claims(token: string): Record<string, unknown> {
  const part = token.split(".")[1] ?? "";
  return JSON.parse(Buffer.from(part, "base64url").toString("utf8")) as Record<string, unknown>;
}

async function revenueByOu(token: string): Promise<Map<number, number>> {
  const response = await fetch(SEMANTIC + "/v1/query", {
    method: "POST",
    headers: { "content-type": "application/json", authorization: "Bearer " + token },
    body: JSON.stringify({
      metric: "revenue_net",
      dimensions: ["operating_unit_id"],
      filters: { date_range: ["2025-09-01", "2026-08-31"] },
    }),
  });
  assert.equal(response.status, 200);
  const body = (await response.json()) as {
    rows: Array<{ operating_unit_id: number; value: number }>;
  };
  return new Map(body.rows.map((row) => [row.operating_unit_id, row.value]));
}

describe("ou precondition: demo.ou1 is genuinely restricted, not an all_ou session", async () => {
  const token = await tokenFor("demo.ou1@contoh.invalid");
  const payload = claims(token);
  assert.equal(
    payload.all_ou,
    false,
    "demo.ou1 holds the bypass; every OU assertion below would pass without the filter doing anything",
  );
  assert.deepEqual(payload.allowed_ou, [1]);
  assert.equal(payload.tenant_id, "bct");
});

describe("ou precondition: operating unit 2 holds rows, so restricting away from it means something", () => {
  const count = execFileSync(
    "docker",
    [
      "exec",
      "odoo19-bct-warehouse-db",
      "psql",
      "-U",
      "warehouse_admin",
      "-d",
      "warehouse",
      "-tAc",
      "select count(*) from marts.mart_revenue_daily where tenant_id = 'bct' and operating_unit_id = 2",
    ],
    { encoding: "utf8" },
  ).trim();
  assert.ok(
    Number.parseInt(count, 10) > 0,
    "operating unit 2 holds no rows; hiding nothing would look identical to scoping",
  );
});

describe("ou scoping: a restricted session sees only its own operating unit", async () => {
  const restricted = await revenueByOu(await tokenFor("demo.ou1@contoh.invalid"));
  const units = [...restricted.keys()].sort((a, b) => a - b);
  assert.deepEqual(units, [1], "demo.ou1 saw operating units " + units.join(", "));
});

describe("ou scoping: the bypass session sees strictly more than the restricted one", async () => {
  const admin = await revenueByOu(await tokenFor("admin"));
  const restricted = await revenueByOu(await tokenFor("demo.ou1@contoh.invalid"));

  assert.ok(admin.size > 1, "the admin session saw one operating unit; there is nothing to restrict");
  assert.ok(admin.size > restricted.size, "the restricted session saw as many units as the bypass one");

  const adminTotal = [...admin.values()].reduce((sum, value) => sum + value, 0);
  const restrictedTotal = [...restricted.values()].reduce((sum, value) => sum + value, 0);
  assert.ok(
    restrictedTotal < adminTotal,
    "restricted total " + restrictedTotal + " is not less than bypass total " + adminTotal,
  );
  assert.ok(restrictedTotal > 0, "the restricted session saw nothing at all, which is a different bug");
});

describe("ou scoping: two restricted sessions see disjoint sets", async () => {
  const one = await revenueByOu(await tokenFor("demo.ou1@contoh.invalid"));
  const two = await revenueByOu(await tokenFor("demo.ou2@contoh.invalid"));
  const overlap = [...one.keys()].filter((unit) => two.has(unit));
  assert.deepEqual(overlap, [], "demo.ou1 and demo.ou2 both saw operating unit(s) " + overlap.join(", "));
  assert.deepEqual([...two.keys()], [2]);
});

describe("ou scoping: the portal states the entitlement and renders only entitled rows", async () => {
  const { password } = credentials();
  const login = await fetch(PORTAL + "/api/auth/login", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ login: "demo.ou1@contoh.invalid", password, next: "" }),
    redirect: "manual",
  });
  const cookie =
    login.headers
      .getSetCookie()
      .find((entry) => entry.startsWith("insight_portal_session="))
      ?.split(";", 1)[0] ?? "";
  assert.notEqual(cookie, "", "demo.ou1 could not log in to the portal");

  const html = await (await fetch(PORTAL + "/t/bct/overview", { headers: { cookie } })).text();
  assert.ok(
    html.includes("Operating Unit 1"),
    "the page does not state the session's entitlement, so an empty panel would look like a broken pipeline",
  );
  assert.equal(
    html.includes("Semua Operating Unit"),
    false,
    "a restricted session was told it holds the bypass",
  );

  // The admin session's OU-2 figure must not appear anywhere on a demo.ou1 page.
  const admin = await revenueByOu(await tokenFor("admin"));
  const unitTwo = admin.get(2);
  assert.notEqual(unitTwo, undefined, "no operating unit 2 figure to look for");
  const formatted = new Intl.NumberFormat("id-ID").format(Math.round(unitTwo ?? 0));
  assert.equal(
    html.includes(formatted),
    false,
    "operating unit 2's figure (" + formatted + ") appeared on a session not entitled to it",
  );
});
