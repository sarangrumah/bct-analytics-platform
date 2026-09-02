---
status: active
generated_at: 2026-07-21
generator: manual
module: custom_fnb_stock_ops
manifest_version: 19.0.0.1.0
---

# custom_fnb_stock_ops

## Purpose

Stock Opname, Demand Forecasting and Auto Replenishment for F&B outlets running on **ESB Core**,
built on [`custom_esb_connector`](../../ee_gap/custom_esb_connector/MODULE_KNOWLEDGE.md).
ESB stays the source of truth for stock; Odoo runs the intelligence and pushes the outcome back
as native ESB documents.

## Business flow

```
Opname:        cycle count session (ESB branch+location)
                 → seed expected from custom.esb.stock.snapshot
                 → count + supervisor approve (custom_wms_cycle_count)
                 → close → ONE ESB Item Journal via the outbox

Forecast:      ESB OMS daily material usage → custom.fnb.demand.history
                 → custom.fnb.demand.forecast (seasonal_dow | weighted_ma | moving_average)

Replenish:     rule × forecast × snapshot → DRAFT proposal in Odoo
                 → human Approve  ← the gate
                 → ESB Purchase Request / Goods Transfer Request / Purchase Order
```

## Key models

| Model | Role |
|---|---|
| `custom.cycle.count.session` *(inherit)* | Adds `esb_branch_id`/`esb_location_id`, seeding from the snapshot, and item-journal emission on close. |
| `custom.cycle.count.adjustment` *(inherit)* | Suppresses the Odoo `stock.move` for ESB-backed counts. |
| `custom.esb.location` *(inherit)* | `action_create_odoo_location()` — maps an ESB location to a scratch Odoo location so count lines have an anchor. |
| `custom.fnb.demand.history` | Daily material consumption per (branch, product). |
| `custom.fnb.demand.forecast` | Daily rate, stdev, MAPE, safety stock per (branch, product). |
| `custom.fnb.replenishment.rule` | Policy: cover, service level, rounding, output document. |
| `custom.fnb.replenishment.proposal` (+ `.line`) | draft → to_approve → approved → pushed → done. |

## Gotchas

1. **`itemJournalDetails[].qty` is the signed variance (`counted − expected`), never the counted
   quantity.** Sending the counted quantity would post the entire stock balance as an adjustment.
   Covered by `test_item_journal_carries_the_signed_delta_not_the_counted_qty`.
2. **`qty_for()` returning `None` means unknown, not zero.** A product ESB reported no movement
   for has no snapshot row. Replenishment *skips* the line (`unknown_on_hand`) rather than
   ordering a full cover for stock the outlet may already hold. A genuinely reported `0.0` is
   honoured normally.
3. **No Odoo `stock.move` for ESB-backed counts.** The adjustment record and approval trail are
   kept, but the move is skipped — Odoo does not own this stock, and a move would fabricate a
   movement (and, on a valued product, a journal entry).
4. **One item journal per session**, not per line. Zero-variance, skipped and unapproved lines
   are excluded.
5. **Purposes drive the GL.** Each ESB purpose carries a COA, so one must be flagged
   `is_default_gain` and one `is_default_loss` under ESB → Master Data → Purposes, or closing a
   session raises a clear error rather than guessing the account.
6. **Approval is the push.** `action_approve()` is what creates the ESB document. Nothing reaches
   ESB from the cron alone.
7. **`on_order` only nets Odoo-raised documents.** ESB's index endpoints return document totals,
   not line quantities, so POs raised by humans directly in ESB are not netted. Keep
   `review_period_days` no shorter than the outlet's real ordering rhythm.
8. **`_backtest` returns `None`, not `0.0`, when the error is unmeasurable** — a perfect forecast
   legitimately scores 0.0, and conflating the two made method comparison discard the best method.
9. **Snapshot staleness**: `esb_stale_snapshot` warns on the session form. Use *Refresh Expected
   from ESB* before approving, which re-reads `/product/stock-location` per counted line.
10. **Odoo 19 API drift** hit three times here: `stock.move.name` and `stock.location.comment` no
    longer exist, `res.users.groups_id` is now `group_ids`, and a **callable** field selection
    cannot be passed to `dict()`.

## Configuration

`ir.config_parameter`, all off by default:

| Key | Effect |
|---|---|
| `fnb.demand_sync_enabled` | Daily OMS material-usage pull |
| `fnb.forecast_enabled` | Nightly forecast recompute |
| `fnb.replenishment_enabled` | Proposal generation cron |
| `fnb.demand_backfill_days` | Backfill depth for a new outlet (default 90) |
| `fnb.esb_currency_id` | ESB `currencyID` used on purchase orders (default 1) |

Plus everything in `custom_esb_connector`, notably `esb.push_enabled`, which gates every write.
All four crons ship `active=False`.

## Fixes made to other modules

- `custom_wms_cycle_count/models/cycle_count_adjustment.py` — creating a `stock.move` with `name`
  raised `ValueError: Invalid field 'name' in 'stock.move'` on Odoo 19. Now uses
  `reference` + `description_picking`. This was a pre-existing bug: **posting any cycle-count
  adjustment was broken**, ESB or not.
- `data/ir_sequence_data.xml` here supplies the `custom.cycle.count.session` sequence that
  `custom_wms_cycle_count` ships only as a placeholder, so sessions no longer all get the
  literal name `CC/NEW` — that name lands in the ESB item journal's `additionalInfo`.

## Tests

86 tests, all against fixtures via the connector's `MockEsbTransport`. No ESB credentials needed.

```
odoo -d <db> -u custom_fnb_stock_ops --test-enable --test-tags /custom_fnb_stock_ops --stop-after-init
```

Note: pushes go through `queue_job`, so tests that assert on the actual HTTP call drive
`outbox.action_push_now()` explicitly.
