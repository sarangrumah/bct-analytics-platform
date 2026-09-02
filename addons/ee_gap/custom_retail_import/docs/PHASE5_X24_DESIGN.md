# Phase-5 X24 — Design: POS Sales Posting (X24DN → pos.order)

Status: **INCREMENT 1 IMPLEMENTED behind flag** (2026-07-05). Target module:
`custom_retail_import` (runtime `/opt/.../models/retail_import_executor.py::_load_x24`).

## Implementation status
- Flag **`retail_import.x24_post_enabled`** (default `0`/off → legacy staging preserved).
- `_load_x24` branches to `_post_x24` (on) or `_stage_x24` (off). New helpers:
  `_x24_resolve_tax`, `_x24_tender_index` (reads latest staged X70D lines),
  `_x24_group_orders`, `_x24_seed_payment_methods`.
- Validated in rnd_retail (fixture: store 80129→pos.config 2, X70D staged): **6 balanced
  paid pos.orders** created from a 15-txn subset; rows without a resolvable product
  parked as errors; **idempotent** re-run created 0; flag OFF confirmed staging. Test
  artifacts cleaned up afterwards. Seeded methods AMEX/OVO/SODEXO left in rnd_retail only.
- Key mechanics learned & encoded: force `price_subtotal`/`price_subtotal_incl` +
  `amount_*` (compute doesn't run on server create); force `pos.session.state='opened'`
  (cash_control); pay via `order.add_payment()` then `action_pos_order_paid()`;
  per-order `cr.savepoint()` so one bad order doesn't roll back the batch; line state
  is `ok`/`skipped`/`error` (not "created").

### Decision A — DONE (2026-07-05)
- `_x24_map_stores_to_configs()` + `_norm_store_name()` + `_x24_stores_from_staged()`:
  match X24 `store_name` to `pos.config.name` on the bare mall name (strip
  `OLS SES -`/`OLS SCU -`/`OLS` prefix). Two-tier: unique exact, then unique
  containment ('fuzzy'), else unmatched/ambiguous. Writes `posconfig_<code>` xids.
- Ran (commit) on all 5 retail DBs from the X24 file's 21 stores: **19 exact + 2 fuzzy,
  0 unmatched/ambiguous** each → 21 xids/DB. Fuzzy = Galaxy Mall 3→cfg, Gandaria City→cfg
  (both verified correct). `resolve_config` spot-checked OK in rnd_retail + prd_retail.

### Decision B + session close — DONE (2026-07-05)
- **No separate IMPORT config needed.** Empirically: the OLS store configs are scaffolding
  (0 sessions / 0 pos.orders all-time) and the apparel brand products are **non-storable (`consu`)**, so
  POS session close creates the accounting journal but **zero stock moves / zero on-hand
  change** — inventory stays owned by X20/X29. Verified with storable-seed + close: 0 moves.
- `_post_x24` close block (gated by `retail_import.x24_close_sessions`, default off) now adds a
  **fail-safe guard**: after close it asserts `session.order_ids.picking_ids.move_ids` is empty
  and logs/records an error if any stock move ever appears (e.g. if products become storable).
- **Accounting validated** (2 orders, CASH+VISA, closed): balanced journal —
  Trade Receivables Dr 333,000 / VAT Out Cr 33,000 (11%) / Gross Sales-textile Cr 300,000.
- Follow-up (not blocking): all tenders currently land in one **Trade Receivables** account;
  to split cash vs card GL, set each `pos.payment.method`'s receivable/journal (part of D setup).

### NOT yet done (increment 2+)
- Seed methods across all retail DBs; tax/`account.tax` id confirmation (**E**).
- Code lives in `/opt` runtime only; **not ported to the `/home` git module** (which still
  has the older pre-Phase-5 executor) — reconcile before committing.

Today `_load_x24` is a *staging* loader: it parses rows, computes a `store|date|sku`
`aggregate_key` on each `retail.import.line`, and writes **0** business records
("Phase-5 gated — pos.config + payment methods not yet confirmed"). This document
specifies how to turn X24 into real `pos.order` posting.

---

## 1. Goal & scope

**In scope:** create financial POS orders from X24DN sales detail, with payments
sourced from **X70D** tender detail, one order per real POS transaction, idempotent
and backtrackable, **without moving stock** (inventory is owned by X20/X29).

**Out of scope (separate phases):** X70T settlement reconciliation, X31 discount
accruals, X26/X29/X48/X53 flows. Stock valuation. Live tax recomputation.

## 2. Source data & join keys

| File | Grain | Key columns (1-based cols in profile) | Role |
|------|-------|----------------------------------------|------|
| **X24DN** | one row per sold SKU line | store_code(1), trans_date(4), register(5), transnum(6), item_code(14), ean(22), net_qty(25), net_discount(26), net_amount(27), tax_rate(28), tax_amount(29), total_amount(30), retail_price(23), gross_price(24) | order **lines** |
| **X70D** | one row per tender on a txn | store_code(1), trans_date(4), register(5), transnum(6), tender_type(9), tender_amount(10), auth(11), voucher(12) | order **payments** |
| **X70** | one row per store/day | store_code, trans_date, total_amount, tender_amount | settlement **cross-check** only |

**Order identity (join key): `(store_code, trans_date, register, transnum)`**.
`_group_x24()` already groups X24 by exactly this key — reuse it. X70D shares the
same key, so tenders attach per order. This is why X70D is "gated on X24 enablement".

## 3. Target Odoo objects & field mapping

One transaction →
- 1 `pos.order` (in a `pos.session` under the store's `pos.config`)
- N `pos.order.line` (one per X24 row in the txn)
- M `pos.payment` (one per X70D row in the txn)

`pos.order.line` mapping (financial, amounts forced from source — do **not** let Odoo
recompute from pricelist):
| pos.order.line | Source |
|---|---|
| product_id | resolve ean→barcode, else item_code→default_code (as `_load_x20`) |
| qty | net_qty |
| price_unit | gross_price (pre-discount unit) |
| discount (%) | derive: `net_discount / (gross_price*qty)` * 100, or 0 and fold into price_subtotal |
| tax_ids | product's sale tax **filtered to the txn tax_rate**; amounts forced |
| price_subtotal / price_subtotal_incl | net_amount / total_amount (forced) |

`pos.payment` mapping:
| pos.payment | Source |
|---|---|
| payment_method_id | **map(tender_type → pos.payment.method for this config)** — see §6 |
| amount | tender_amount |
| pos_order_id | the order |

`pos.order` header: company_id + session_id from config; `date_order` = trans_date;
`amount_total`/`amount_paid`/`amount_tax` forced from summed lines; `pos_reference`
= `f"{store}-{register}-{transnum}"`; `state='paid'`→`'done'` (invoiced=False).

## 4. Processing algorithm

```
_load_x24(profile, file_b64, log):
  records = profile.read_records(file_b64)["records"]
  persist_lines(log, records)                       # existing
  guard_already_imported(profile, log)              # as _load_x20 §5
  x70d = load_companion_x70d(store,date scope)      # from staged X70D lines or re-read file
  orders = _group_x24(...)                          # {(store,date,reg,txn): [rows]}
  for key, rows in orders:
     cfg   = resolve_pos_config(store)              # §6 prerequisite
     if not cfg: mark rows error "no pos.config"; continue
     sess  = get_or_open_daily_session(cfg, date)   # §6 session strategy
     lines = [build_line(r) for r in rows]          # resolve product; skip row on miss
     pays  = [build_pay(t) for t in x70d[key]]      # map tender→method
     if abs(sum(line.total) - sum(pay.amount)) > tol: flag/park (do not post)
     order = create_pos_order(cfg, sess, date, key, lines, pays)
     xid_set(ns, f"posorder_{safe(key)}", "pos.order", order.id)   # idempotency
     link lines: target_model='pos.order', target_res_id=order.id
  close/settle sessions (batch); log.records_created = orders_posted
```

Batch `cr.commit()` every ~200 orders (as existing loaders). Errors collected via
`log.set_errors(...)`, per-row `state='error'` + message for backtracking.

## 5. Idempotency & backtracking (follow existing conventions)

- **External ID per order**: `ir.model.data` name `posorder_<STORE>_<DATE>_<REG>_<TXN>`
  in `profile.namespace`. Before create, `_xid_get(...)` → skip if present.
- **Whole-file guard**: refuse re-run if a prior `imported` log exists for this profile
  covering the same period (mirror `_load_x20` lines 531-540), unless prior archived.
- **Line linkage**: set `target_model='pos.order'`, `target_res_id`, keep `aggregate_key`
  so a bad order can be traced to its source rows and reversed.
- **Reversal path**: to undo, browse lines by log → orders by target_res_id → refund/cancel.

## 6. Key technical challenges + proposed resolution

1. **Store → pos.config** *(prerequisite, currently missing)*. X24 store_code is numeric
   (e.g. 80434); the 23 `pos.config` are named by mall, not by code. Need a resolver like
   X20's `wh_<code>` xid: add `posconfig_<store>` external IDs (extend the Store Master
   loader) **or** a `store_code` field on `pos.config`. **Decision A.**
2. **No stock movement.** `pos.order` normally creates pickings on session close.
   Options: (a) use a dedicated import `pos.config` whose `picking_type_id` route makes no
   moves / `ship_later=False` and post orders as financial-only; (b) create orders then
   cancel the generated pickings; (c) set order lines' products to a non-stockable variant
   context. **Recommend (a) — a ring-fenced "IMPORT" pos.config per company.** **Decision B.**
3. **Session strategy.** One `pos.session` per (config, trans_date): open, add all that
   day's orders, close → generates the day's accounting summary. Keeps books daily-clean
   and matches X70/X70T settlement grain. Alternative: one long "import" session (simpler,
   but coarse accounting). **Recommend daily session.** **Decision C.**
4. **Tender_type → pos.payment.method.** X70D `tender_type` is a raw string; methods are
   duplicated per company (many "CASH"). Build a mapping table keyed by (company/config,
   tender_type) → method id. Seed from the 30 existing methods (CASH, OFFLINE_VISA,
   OFFLINE_MASTERCARD, OFFLINE_JCB, …). **Decision D — confirm the tender_type vocabulary
   and its target method per store.**
5. **Tax handling.** Force `price_subtotal`/`amount_tax` from source (tax_amount col) and
   attach a tax whose rate matches `tax_rate`; do **not** recompute (source is source of
   truth). Requires a rate→account.tax lookup (reuse CoA/tax mapping). **Decision E: which
   tax records represent each X24 tax_rate.**
6. **Order↔payment balance.** If `Σ line.total != Σ tender` beyond tolerance, **park** the
   order (row state='error', not posted) rather than post an unbalanced order. Surfaced in
   the log for manual review. Cross-check daily totals against **X70** settlement.
7. **Performance.** 3,186 orders per 5k-row sample; production files larger. Pre-cache
   product/ config/ method lookups (dict caches like `_load_x20`), batch commits, and
   prefer `create([...])` multi.

## 7. Prerequisites checklist (must exist before enabling)

- [ ] Products loaded (X101) — resolvable by barcode/default_code.
- [ ] Store Master extended to emit `posconfig_<store>` (and `wh_<store>`) xids for **all**
      stores present in X24 (rnd_retail currently has only 1 `wh_` xid).
- [ ] A ring-fenced import `pos.config` per company (Decision B).
- [ ] tender_type → payment.method map seeded (Decision D).
- [ ] tax_rate → account.tax map seeded (Decision E).
- [ ] X70D staged/available for the same period as X24.

## 8. Decisions needed from you (blockers)

| # | Decision | Options |
|---|----------|---------|
| A | Store→pos.config resolution | xid `posconfig_<store>` via Store Master **(rec)** / new field on pos.config |
| B | Stock suppression | dedicated IMPORT pos.config, no moves **(rec)** / cancel pickings / other |
| C | Session granularity | one session per store/day **(rec)** / single import session |
| D | tender_type vocabulary → method | need the list of X70D tender_type strings + target method per store |
| E | tax_rate → account.tax | need which tax record maps to each X24 `tax_rate` value |
| F | Order grain | per-transaction (transnum) **(rec, enables tender match)** / per store-day aggregate |

## 9. Rollout & test plan

1. Implement behind a config flag `retail_import.x24_post_enabled` (default off) so the
   staging behaviour is preserved until validated.
2. Dry-run in **rnd_retail** (test DB) on one store/day: assert #orders == #distinct
   (store,date,reg,txn), Σ order totals == X70 settlement total, payments balanced.
3. Verify backtracking: each `retail.import.line` links to its `pos.order`; reversal works.
4. Validate accounting: close a daily session, inspect the POS journal entries vs expected
   revenue/tax/tender split.
5. Roll to prd_* only after sign-off; keep the per-file idempotency guard.

## 10. Effort (rough)

- Core `_load_x24` posting + `_group_x24`/x70d wiring: ~1.5–2 dev-days.
- Store→config + tender + tax mapping seeders: ~1 day (pending Decisions A/D/E data).
- Session open/close + no-stock config + reconciliation guard: ~1 day.
- Test harness + rnd validation: ~1 day.
- **Total ≈ 4–5 dev-days** after Decisions A–F are answered.

---

## Appendix — data pulled from rnd_retail archived samples (2026-07-05)

Source: `X24DN` (12,842 rows) and `X70D` (4,901 rows) parsed via their profiles.

### Decision E — X24 `tax_rate` (near single-rate)
| tax_rate | rows | → proposed `account.tax` |
|---|---|---|
| `0.11` | 12,667 (98.6%) | **id 28 "12% (Non-Luxury Good)", amount=11.0, sale** |
| `None` | 174 | no tax (non-taxable / void) — post without tax |
| `` (empty) | 1 | summary/total artifact row → **skip** |

Residual choice: use plain **id 28** vs the WAPU variant **id 31 "12% Pemungut PPN
(Non-Luxury Good)"** (also amount=11.0). Default assumption: **id 28**.

### Decision D — X70D `tender_type` → `pos.payment.method`
Method names were seeded to match tender strings. 8/12 auto-map by exact name:

| tender_type | rows | method match |
|---|---|---|
| CASH | 403 | ✅ (23 — one per config; resolve within order's config) |
| OFFLINE_DOMESTIC_CARD | 1408 | ✅ (1) |
| OFFLINE_OTHER_CREDITCARD | 1027 | ✅ (1) |
| OFFLINE_VISA | 850 | ✅ (1) |
| OFFLINE_CREDIT_CARD | 839 | ✅ (1) |
| OFFLINE_MASTERCARD | 239 | ✅ (1) |
| OFFLINE_BRI_CREDIT_CARD | 114 | ✅ (1) |
| OFFLINE_JCB | 6 | ✅ (1) |
| **OFFLINE_AMEX** | 9 | ❌ no method — create or fold into OTHER_CREDITCARD |
| **SODEXO** | 2 | ❌ no method — create (voucher) or fold |
| **OFFLINE_OVO** | 2 | ❌ no method — create (e-wallet) or fold |
| **OFFLINE_OTHER_CARD** | 1 | ❌ no method — fold into OTHER_CREDITCARD |
| `` (empty) | 1 (amount 6.97e9) | summary/total artifact → **skip** |

Residual choice: for the 4 unmapped tenders, **create dedicated methods** (AMEX, SODEXO,
OVO, OTHER_CARD) or **fold** them into `OFFLINE_OTHER_CREDITCARD`. Default assumption:
create AMEX + OVO + SODEXO, fold OTHER_CARD → OTHER_CREDITCARD.

**Loader must skip rows where tender_type/tax_rate is empty** (the single huge-amount
empty-tender row is a file total, not a payment).

Note: parsed X24 here = 12,842 rows vs the earlier staged log #16 = 5,097 lines — the
archived sample differs from the first import; confirm which X24DN extract is canonical
before a production run.
