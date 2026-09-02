import assert from "node:assert/strict";
import { test } from "node:test";

import {
  defaultFilters,
  parseFilters,
  priorYear,
  serialiseFilters,
  toQueryFilters,
} from "../src/lib/filters.ts";

/**
 * Filter persistence and the shape sent to `/v1/query`.
 *
 * The important assertion is the last one: `stock_net_quantity` reads `mart_stock_position`, which
 * is a POSITION and carries no date column, so the metric declares no `date_range` filter and
 * sending one is a 400. A filter bar that sends the same payload to every metric would break that
 * view and nothing else, which is exactly the kind of failure that reaches a demo.
 */

test("a round trip through the cookie preserves the range and the operating units", () => {
  const filters = { from: "2025-09-01", to: "2026-08-31", ou: [1, 4] };
  assert.deepEqual(parseFilters(serialiseFilters(filters)), filters);
});

test("filters persist across views because they live in one serialised cookie", () => {
  // Same cookie value, read on two different views: the parse is pure and view-independent.
  const cookie = serialiseFilters({ from: "2026-01-01", to: "2026-03-31", ou: [2] });
  const onSales = parseFilters(cookie);
  const onInventory = parseFilters(cookie);
  assert.deepEqual(onSales, onInventory);
  assert.equal(onSales.from, "2026-01-01");
});

test("a malformed cookie falls back to the default rather than throwing", () => {
  const fallback = defaultFilters(new Date("2026-08-31T00:00:00Z"));
  assert.deepEqual(parseFilters("garbage", new Date("2026-08-31T00:00:00Z")), fallback);
  assert.deepEqual(
    parseFilters("from=nonsense&to=2026-01-01", new Date("2026-08-31T00:00:00Z")),
    fallback,
  );
});

test("a reversed range is rejected in favour of the default", () => {
  const now = new Date("2026-08-31T00:00:00Z");
  const parsed = parseFilters("from=2026-08-31&to=2026-01-01", now);
  assert.deepEqual(parsed, defaultFilters(now));
});

test("a non-date that matches the pattern is still rejected", () => {
  const now = new Date("2026-08-31T00:00:00Z");
  assert.deepEqual(parseFilters("from=2026-02-31&to=2026-03-01", now), defaultFilters(now));
});

test("the default range is the trailing twelve months", () => {
  const filters = defaultFilters(new Date("2026-08-31T12:00:00Z"));
  assert.equal(filters.to, "2026-08-31");
  assert.equal(filters.from, "2025-09-01");
});

test("date_range is omitted for a metric that does not declare one", () => {
  const filters = { from: "2025-09-01", to: "2026-08-31", ou: [3] };
  const withDate = toQueryFilters(filters);
  assert.deepEqual(withDate.date_range, ["2025-09-01", "2026-08-31"]);

  const position = toQueryFilters(filters, { dateRange: false });
  assert.equal(
    Object.prototype.hasOwnProperty.call(position, "date_range"),
    false,
    "mart_stock_position has no date column; sending date_range would be a 400",
  );
  assert.deepEqual(position.operating_unit_id, [3]);
});

test("an empty operating unit selection sends no operating_unit_id filter at all", () => {
  const filters = { from: "2025-09-01", to: "2026-08-31", ou: [] };
  const query = toQueryFilters(filters);
  assert.equal(
    Object.prototype.hasOwnProperty.call(query, "operating_unit_id"),
    false,
    "an empty array is rejected by the compiler; omitting the filter lets entitlement decide",
  );
});

test("no filter payload ever carries a tenant", () => {
  const query = toQueryFilters({ from: "2025-09-01", to: "2026-08-31", ou: [1] });
  assert.equal(Object.prototype.hasOwnProperty.call(query, "tenant_id"), false);
});

test("the prior-year window is exactly one year back", () => {
  assert.deepEqual(priorYear({ from: "2025-09-01", to: "2026-08-31", ou: [] }), {
    from: "2024-09-01",
    to: "2025-08-31",
  });
});
