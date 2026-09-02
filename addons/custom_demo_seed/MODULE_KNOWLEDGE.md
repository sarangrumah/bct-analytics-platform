# custom_demo_seed — module knowledge

## THIS IS A FIXTURE MODULE. NEVER PUT IT IN A PRODUCTION INSTALL SET.

It exists for one reason: the Phase 4 performance budget — *"p95 under 2 s with 12 months of
data"* — cannot be measured against an empty database, and neither can a dbt reconciliation test.

### What stops it running in production

| Safeguard | Strength |
|---|---|
| Generates **nothing** at install time. Data appears only when `generate()` is called. | strong — installing it is harmless on its own |
| `auto_install: False`, and **no other module depends on it** | strong — it can never be dragged in |
| `generate()` raises `UserError` for a non-administrator | moderate |
| Every value is obviously synthetic (§4) | moderate — a mistake is visible, not silent |
| Every record carries an `ir.model.data` external ID, so `uninstall` removes exactly the demo rows | moderate — recoverable |

None of that makes it *safe* in production. The rule is simply: **do not install it there.**
`make install-modules` and any production install list must name the four domain modules only.

---

## 1. API

```python
env['demo.seed.generator'].generate(
    seed=20260101,               # RNG seed. Same seed -> same data.
    partners=40,
    products=12,                 # capped at len(PRODUCTS) = 12
    operating_units=2,           # >= 2 enforced; max 4
    months=12,                   # whole months back from today
    sale_orders_per_month=10,
    pos_orders_per_month=8,
    ppob_per_month=30,
    with_pos=True,
    company=None,                # defaults to env.company
) -> dict           # counts of what NOW EXISTS, not of what this call created
```

`env['demo.seed.generator'].summary()` returns the same counts without generating anything.

There is also a wizard (**Settings → Technical → Generate Demo Data**, `base.group_system` only)
that wraps the same call.

---

## 1a. Datasets and shape authority — the parameter-authority rule

Idempotency by external ID has a failure mode worse than the problem it solves, and the first
release shipped it: once records exist, `_ensure` returns them **whatever parameters the caller
asked for**. `generate(partners=4)` on a database seeded with 40 returned 40, silently, with no
error. For Phase 3 that is poison — DWH and QA seed at chosen volumes, and a reconciliation test
that silently ran against the wrong row count is worse than one that fails.

Two mechanisms, both required:

1. **Datasets.** `dataset` namespaces the external IDs and every human-visible reference
   (`DEMO-C-0001` → `DEMO-UNITTEST-C-0001`, `OU-DEMO-JKT` → `OU-DEMO-UNITTEST-JKT`, …). Two
   datasets never share a record. The default dataset keeps the historical reference shapes.
2. **Shape authority.** The full parameter set — including `anchor` (today's month, because the
   12-month window is anchored to today) and `company_id` — is recorded in
   `ir.config_parameter` `custom_demo_seed.shape.<dataset>` when the dataset is first generated.
   A later call for the same dataset with a different shape raises, naming every conflict:

   ```
   Demo dataset 'default' already exists with a different shape, so this call would have
   silently returned data you did not ask for.

     - partners: already seeded as 40, you asked for 4

   Choose one:
     * call generate() again with dataset='<a new name>' ...
   ```

Dataset names are validated **strictly and case-sensitively**: `Default` is not `default`, and `""`
is not a synonym for the default. Silently folding either would be the same class of substitution
this mechanism exists to prevent.

### Why there is no `reset=True`

Considered and rejected. A dataset's documents include posted journal entries and done stock moves,
and Odoo forbids deleting both by design — `account.move._unlink_account_audit_trail_except_once_post`
and `stock.move._unlink_if_draft_or_cancel`. Deleting them means passing `force_delete` to bypass
the audit trail. A fixture that ships an audit-trail bypass is a worse thing to have in the codebase
than the inconvenience it saves, and a reset that silently half-worked would be worse still.
The supported ways to get a different shape are a new `dataset` name, or a fresh database.

### Legacy migration

The pre-dataset release wrote unprefixed external IDs (`partner_0001`). `_migrate_legacy_xmlids`
moves them into `default__…` on first run. It must **flush** and then **clear the registry cache**:
`ir.model.data._xmlid_lookup` is `@ormcache('xmlid')` *and* runs a raw `cr.execute` that does not
trigger an ORM flush, so missing either step makes `env.ref()` blind to the renamed rows and
`_ensure` creates a duplicate of every record. Measured on `bct`: 648 legacy IDs → 0.

## 2. Idempotency — how, and why it is done this way

Every record is created through `_ensure(env, xmlid, model, values)` (or tagged afterwards with
`_tag()` when the record must be built by a business method such as `sale.order.create`). Both
register an `ir.model.data` row under module `custom_demo_seed`. A second run looks the external ID
up first and skips.

This is stronger than a "does a record with this name already exist?" check: it survives a rename,
it cannot collide with genuine data that happens to share a name, and it makes
`uninstall custom_demo_seed` delete exactly the demo rows and nothing else.

**Measured**, on a fresh database with the defaults:

All counters are derived from `ir.model.data` membership of the dataset, never from matching a
reference prefix. A name pattern is a heuristic: it mixed datasets together and it silently omitted
the inventory-adjustment moves, which is where the earlier "238 stock moves" understatement came
from. `stock_moves` is now reported split, and `stock_moves_delivery + stock_moves_inventory`
equals the raw `select count(*) from stock_move`.

| counter | run 1 | run 2 |
|---|---|---|
| operating_units | 2 | 2 |
| partners | 40 | 40 |
| products | 12 | 12 |
| uncosted_products | 1 | 1 |
| billers | 6 | 6 |
| sale_orders | 120 | 120 |
| sale_order_lines | 311 | 311 |
| invoices | 120 | 120 |
| stock_moves | 248 | 248 |
| pos_orders | 96 | 96 |
| ppob_transactions | 360 | 360 |
| elapsed_seconds | 127.9 | **0.4** |

Run 2 does no work at all — that 0.4 s is the summary query.

> Note on `_tag` vs `_register`: the helper is called `_tag` because `_register` is a reserved
> `BaseModel` class attribute (a bool), and shadowing it with a method fails at runtime with
> `TypeError: 'bool' object is not callable`. Do not rename it back.

---

## 3. What it generates, and the shape of the result

With the defaults, on a database seeded 2026-08-30:

| table | rows | span |
|---|---|---|
| `operating.unit` | 2 | `OU-DEMO-JKT`, `OU-DEMO-BDG` |
| `res.partner` | 40 | `DEMO-C-0001` … `DEMO-C-0040` |
| `product.template` | 13 | 9 storable + 3 service + **1 storable with no cost** (§3.1) |
| `ppob.biller` | 6 | electricity ×2, water, telco, internet, insurance |
| `sale.order` | 120 | `2025-09-02` → `2026-08-27`, **12 distinct months, 2 OUs** |
| `sale.order.line` | 311 | 1–4 lines per order |
| `account.move` (customer invoices) | 120 | all posted, **0 without an Operating Unit** |
| `stock.picking` | 109 | all done, **0 without an Operating Unit** |
| `stock.move` | 248 | 238 deliveries + 10 inventory adjustments (9 catalogue + 1 no-cost) |
| `pos.order` | 96 | 12 months, 2 OUs |
| `ppob.transaction` | 360 | 328 success / 27 failed / 5 reversed |

The PPOB state mix is deliberate: ~92 % success, ~8 % failure, ~2 % of successes later reversed.
A fact table where every row succeeded is useless for testing an `accepted_values` assertion or an
SLA-breach metric.

Also created: **two internal users**, each entitled to exactly one Operating Unit, so the
cross-unit isolation of `custom_operating_unit` can be demonstrated by logging in rather than only
by a unit test. **No password is set on either** — the accounts cannot be logged into until an
administrator sets one, so a seeded database never ships a known credential.

---

### 3.1 `DEMO-P-NOC-001` — one storable product with no cost, on purpose

Requested by the Data Warehouse agent. `mart_stock_position` carries `has_unit_cost`, and its
**false branch had never executed**: DWH measured 27 position rows, 27 valued, 0 unvalued. The only
two products in the database without a `standard_price` were **Tips** and **Down Payment**, created
by `point_of_sale` and `sale`, and both are non-storable — so they produce no stock move and never
reach a position row at all. DWH recorded that as NOT VERIFIED **inside `dim_product_cost.sql`**,
not merely in a report.

The correlation is structural rather than accidental: a product with no cost is usually a service.
The one shape that breaks it is **storable, with stock moves, and no `standard_price`.**

| property | value | why it is load-bearing |
|---|---|---|
| `is_storable` | `True` | a non-storable product produces no `stock.move`, so no position row |
| `standard_price` | **absent from the create values** | writing `0.0` would materialise `{"1": 0.0}` in the `company_dependent` jsonb map, `dim_product_cost` would emit a row, the LEFT join would match and `has_unit_cost` would come back **true**. *Absent is not zero* — the distinction `mart_stock_position` documents |
| `list_price` | `0.0` | a sales price on a deliberately unvalued item invites `coalesce(unit_cost, list_price)`, which is the "plausible column wrong by a large factor" `dim_product_cost` measured at 1.46×. It also leaves that measurement untouched whichever way the join is written |
| `sale_ok`, `purchase_ok`, `available_in_pos` | `False` | it must reach a position row through the inventory adjustment **only**; a sale would change the revenue marts |
| stock | one inventory adjustment, 250 units | the same flow `_ensure_stock` uses, so the move has the shape a real adjustment produces |

**It is not governed by the `products` parameter and is not in `SHAPE_KEYS`.** It is not a shape
choice a caller makes; it is part of what this fixture *is*. That is also what makes it appear on a
**re-run of a dataset that already exists** — every dataset on this host was seeded by a release
without it. Reusing `_ensure_stock`'s `stock_seeded` marker would have made "data already exists"
mean "skip", which is precisely the defect the shape-authority mechanism was built for, so it
carries its own `uncostedstockseeded` marker.

Measured on `bct`, re-running `generate()` with the recorded shape:

```
BEFORE  products 12  uncosted_products 0  stock_moves 247  stock_moves_inventory  9
AFTER   products 12  uncosted_products 1  stock_moves 248  stock_moves_inventory 10   (0.7 s)
```

`products` still equals the `products` **parameter** — the counter is narrowed to the catalogue
xmlid family, and the no-cost product is reported separately as `uncosted_products`. A counter that
silently absorbed it would make `summary()` disagree with the recorded shape.

And, through the pipeline (CDC → dbt) on the same database:

```
marts.mart_stock_position   bct  28 rows | 27 valued | 1 UNVALUED
                            the unvalued row: product 15 DEMO-P-NOC-001,
                            net_qty 250, unit_cost NULL, stock_valuation NULL
marts.dim_product_cost      0 rows for product 15 (24 total, unchanged)
DWH's list-vs-cost figure   2 645 000 / 1 814 000 = 1.46x, UNCHANGED
```

**Self-repairing postcondition.** After creating the product the fixture reads the raw
`product_product.standard_price` jsonb and, if a cost has been materialised for the company, removes
that key and logs a warning. If a future Odoo release — or a product category configured for
AVCO/automated valuation instead of this database's `standard` — ever writes a cost here, the branch
silently stops being exercised while every count still looks right. That is the "check that cannot
fail" shape, so it is checked rather than trusted;
`test_a_materialised_cost_on_the_uncosted_product_is_repaired` establishes the broken condition and
watches the repair fire.

---

## 4. Why the synthetic data is shaped the way it is

The brief forbids inventing data that resembles a real person's real identifiers. Every generated
value is therefore unmistakable:

| field | pattern | why it cannot be real |
|---|---|---|
| `res.partner.name` | `Budi Santoso (Demo 001)` | the `(Demo NNN)` suffix |
| `res.partner.email` | `budi.santoso.001@contoh.invalid` | `.invalid` is reserved by RFC 2606 and can never resolve |
| `res.partner.phone` | `+62-800-555-0001` | `800` is not an assignable Indonesian mobile prefix; `555` is the fiction convention |
| `res.partner.ref` | `DEMO-C-0001` | prefix |
| `ppob.transaction.customer_ref` | `DEMO-00000012345` | prefix; a real PLN meter number is 11–12 bare digits |
| `product.template.default_code` | `DEMO-P-…` | prefix |
| `operating.unit.code` | `OU-DEMO-…` | prefix |
| `ppob.biller.code` | `DEMO-…` | prefix |
| user logins | `demo.ou1@contoh.invalid` | `.invalid` |

**`res.partner.vat` is left EMPTY on purpose.** Odoo's `base_vat` validates the Indonesian NPWP
checksum, so any value that survived validation would *by construction* be checksum-valid — which
is to say, indistinguishable from a real NPWP. That is precisely the failure the brief forbids.
The masking of `res.partner.vat` is demonstrated by `custom_pdp_masking`'s unit tests instead, which
need no persisted value. If the warehouse team needs a populated `vat` column to exercise the
hashing path, say so and it can be filled with a deliberately checksum-*invalid* literal — but that
is a decision to take consciously, not a default.

---

## 5. Fidelity gaps — what the fixture does *not* reproduce

Stated so nobody builds a conclusion on top of one of them.

* **POS sessions do not have realistic boundaries.** Odoo allows only one open `pos.session` per
  `pos.config`, and closing a session posts accounting entries. Twelve monthly close cycles per
  unit would make the fixture slow and fragile for no analytic gain, so the fixture opens **one
  long-lived session per Operating Unit** and spreads `pos.order.date_order` across the months
  instead. The warehouse reads `date_order`, which is faithful; anything keyed on `session_id` or
  on session open/close timestamps is not.
* **POS orders carry no tax.** `tax_ids` is emptied and `price_subtotal == price_subtotal_incl`.
  A tax-aware POS mart cannot be validated against this data.
* **No purchases, no manufacturing, no payments.** Invoices are posted but not paid, so
  `payment_state` is uniformly `not_paid` and any DSO/ageing metric will look wrong.
* **Stock is topped up once**, via a single inventory adjustment of 100 000 units per storable
  product, before any month is seeded. So `stock.move` contains one adjustment per storable product
  (9 with the default parameters), one more for the no-cost product of §3.1 at 250 units, plus the
  deliveries; it is not a realistic replenishment pattern.
* **Everything is in one company** (`env.company`). Multi-company behaviour is exercised by the
  unit tests, not by the fixture.
* **`date_order` is re-pinned after `action_confirm()`**, because confirmation moves it to "now" in
  some flows. The pickings and moves are re-dated to match. Journal entries created by POS session
  closing (if a human ever closes one) would carry today's date, not the order's.

---

## 6. Operating Unit propagation — found by this fixture

The first full run produced 4 sale orders carrying an Operating Unit, and 4 posted invoices and 4
pickings carrying **none** — because Odoo's `_prepare_invoice()` and its procurement machinery know
nothing about the field. A `mart_revenue_daily` built on `account_move` would have lost every row.

The fix lives in `custom_operating_unit/models/propagation.py`, not here:
`sale.order._prepare_invoice()`, `sale.order._action_confirm()` (→ pickings),
`stock.picking.create()` (→ backorders), `pos.order._prepare_invoice_vals()`. Credit notes inherit
it for free because `_reverse_moves()` uses `copy()`.

`test_operating_unit_propagates_to_invoices_and_pickings` in this module's suite is the regression
guard, and it is here rather than in `custom_operating_unit` because only the fixture builds a
document chain long enough to catch it.

---

## 7. Runtime

~128 s for the defaults (576 documents) on the reference dev box. Almost all of it is
`action_confirm` → deliver → `_create_invoices` → `action_post`, which is real Odoo business logic
and cannot be short-cut without making the data unrepresentative. Scale with the per-month
parameters; a second run costs nothing.
