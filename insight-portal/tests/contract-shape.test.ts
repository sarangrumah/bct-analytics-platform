import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

import { isCatalogue, isQueryResponse } from "../src/lib/types.ts";

/**
 * The fixture rule from contract 03 and the brief, enforced rather than promised.
 *
 * Every file Backend generates into `analytics/semantic-api/metrics/fixtures/` is parsed through
 * the SAME type guards the running application uses on live responses. Hand-writing a fixture shape
 * is a brief violation; this makes the reverse true as well - if Backend changes the envelope, this
 * project finds out here rather than at render time.
 *
 * How it was made to go red: `revenue_net.json` was temporarily edited to rename `value` to
 * `amount` in one row. `isQueryResponse` rejected it and the test failed naming the file. Restored,
 * it passes. The test also fails when the directory is empty, so a fixture set that quietly
 * disappears cannot look like a pass.
 */

const here = dirname(fileURLToPath(import.meta.url));
const fixtures = join(here, "..", "..", "analytics", "semantic-api", "metrics", "fixtures");

function fixtureFiles(): string[] {
  return readdirSync(fixtures).filter((name) => name.endsWith(".json"));
}

test("the generated fixture directory is not empty", () => {
  const files = fixtureFiles();
  assert.ok(
    files.length > 0,
    "no fixtures found at " + fixtures + " - a green run here would prove nothing",
  );
  assert.ok(files.includes("_catalogue.json"), "the catalogue fixture must be present");
});

test("every metric fixture matches the contract 06 query envelope", () => {
  const failures: string[] = [];
  for (const name of fixtureFiles()) {
    if (name === "_catalogue.json") continue;
    const parsed: unknown = JSON.parse(readFileSync(join(fixtures, name), "utf8"));
    if (!isQueryResponse(parsed)) failures.push(name);
  }
  assert.deepEqual(failures, [], "these fixtures do not match the QueryResponse guard");
});

test("the catalogue fixture matches the contract 06 metrics envelope", () => {
  const parsed: unknown = JSON.parse(readFileSync(join(fixtures, "_catalogue.json"), "utf8"));
  assert.ok(isCatalogue(parsed), "_catalogue.json does not match the Catalogue guard");
});

test("every fixture row keys its measure as value", () => {
  for (const name of fixtureFiles()) {
    if (name === "_catalogue.json") continue;
    const parsed: unknown = JSON.parse(readFileSync(join(fixtures, name), "utf8"));
    assert.ok(isQueryResponse(parsed));
    if (!isQueryResponse(parsed)) continue;
    for (const row of parsed.rows) {
      assert.ok(
        Object.prototype.hasOwnProperty.call(row, "value"),
        name + ": a row has no `value` key; every chart in this app binds to that one key",
      );
    }
  }
});

test("the guard rejects a row whose measure is a string", () => {
  const bad = {
    metric: "revenue_net",
    dimensions: ["date_month"],
    rows: [{ date_month: "2026-01-01", value: "44170500.0" }],
    meta: {
      tenant_id: "bct",
      row_count: 1,
      last_refreshed_at: "2026-08-31T01:13:57.493101+00:00",
      is_stale: false,
      refresh_sla_seconds: 900,
      source_model: "mart_revenue_daily",
      unit: "IDR",
      type: "decimal",
      query_duration_ms: 59.2,
    },
  };
  assert.equal(isQueryResponse(bad), false, "a stringified measure must not pass the guard");
});

test("the guard accepts a null measure, because growth metrics return one", () => {
  const growth = {
    metric: "revenue_mom_growth",
    dimensions: ["date_month"],
    rows: [
      { date_month: "2025-09-01", value: null },
      { date_month: "2025-10-01", value: 0.0018 },
    ],
    meta: {
      tenant_id: "bct",
      row_count: 2,
      last_refreshed_at: "2026-08-31T05:49:21.580172+00:00",
      is_stale: false,
      refresh_sla_seconds: 900,
      source_model: "mart_revenue_daily",
      unit: null,
      type: "percent",
      query_duration_ms: 55.7,
    },
  };
  assert.equal(isQueryResponse(growth), true);
});

test("the guard rejects a meta block with no freshness fields", () => {
  const noFreshness = {
    metric: "revenue_net",
    dimensions: [],
    rows: [{ value: 1 }],
    meta: { tenant_id: "bct", row_count: 1 },
  };
  assert.equal(
    isQueryResponse(noFreshness),
    false,
    "a response without last_refreshed_at/is_stale must not render: freshness is not optional",
  );
});
