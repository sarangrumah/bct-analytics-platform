import assert from "node:assert/strict";
import { test } from "node:test";

import { formatDimension, formatMeasure, formatSla } from "../src/lib/format.ts";

/**
 * Rendering rules that carry meaning, tested where the live data cannot exercise them.
 *
 * `is_profit_and_loss` NULL is the case in point: Backend confirmed this seed contains zero section
 * or note lines, so no live assertion can reach that branch. A NULL-free result today is not
 * evidence that NULL cannot occur, so the label is proven here instead of assumed.
 */

test("a NULL profit-and-loss flag is labelled, not shown as a missing value", () => {
  assert.equal(formatDimension("is_profit_and_loss", null), "Bukan keduanya (NULL)");
  assert.equal(formatDimension("is_profit_and_loss", true), "Ya");
  assert.equal(formatDimension("is_profit_and_loss", false), "Tidak");
});

test("an unassigned operating unit is a member, not a missing value", () => {
  assert.equal(formatDimension("operating_unit_id", -1), "Tanpa Operating Unit");
  assert.equal(formatDimension("operating_unit_id", 1), "1");
});

test("a product with no unit cost is labelled rather than blank", () => {
  assert.equal(formatDimension("has_unit_cost", false), "Tidak");
  assert.equal(formatDimension("has_unit_cost", true), "Ya");
});

test("a 60 second SLA reads as seconds, not as one minute", () => {
  assert.equal(formatSla(60), "60 detik");
  assert.equal(formatSla(300), "5 menit");
  assert.equal(formatSla(900), "15 menit");
  assert.equal(formatSla(3600), "1 jam");
});

test("a null measure is an em dash, never a zero", () => {
  assert.equal(formatMeasure(null, { unit: "IDR", type: "decimal" }), "—");
  assert.equal(formatMeasure(null, { unit: null, type: "percent" }), "—");
});

test("a percent metric is rendered as a percentage, not as a bare fraction", () => {
  assert.equal(formatMeasure(0.9836, { unit: null, type: "percent" }), "98,4%");
  assert.match(formatMeasure(0.12, { unit: null, type: "percent" }, { signed: true }), /^\+12/);
  assert.match(formatMeasure(-0.249, { unit: null, type: "percent" }, { signed: true }), /^-24,9/);
});
