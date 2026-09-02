---
status: draft
generated_at: 2026-07-02T08:56:04Z
generator: bootstrap-v1
module: custom_retail_localization
manifest_version: 19.0.1.24.0
---

# the apparel brand Localization (`custom_retail_localization`)

## Purpose
This module implements five specific requirements for the apparel brand tenant: HS Code management on the product master, ensuring receipt quantities do not exceed demand quantities, skipping the inventory journal entry at goods receipt confirmation, generating branded payment vouchers and receipts, and providing a periodic inventory reconciliation tool that realigns GL inventory-asset accounts with actual on-hand stock value.

## Business Flow
1. **HS Code Management**:
   - Delivered as a `product.template` view that inherits `product.product_template_form_view` to surface the native `stock_delivery` `hs_code` field on the General Information tab (`views/product_template_views.xml:9-18`). There is no Python `product` override; the field itself comes from `stock_delivery`.
2. **Receipt Quantity Validation**:
   - On confirming an incoming stock picking, if any line's done quantity exceeds its demand quantity (compared with `float_compare`), a `UserError` is raised listing the offending products.
3. **Inventory Journal at Goods Receipt & Vendor Return (opt-in switch)**:
   - Governed by `ir.config_parameter` `custom_retail_localization.suppress_gr_journal` (default **OFF**). This build has no standard stock input/output interim accounts, so core real-time valuation posts nothing; the module books inventory GL directly via the category pair `property_stock_valuation_account_id` + `account_stock_variation_id` (same pair the Inventory Reconciliation tool uses).
   - Switch OFF (default): on a vendor **goods receipt** (source = supplier) it posts `Dr Stock Valuation / Cr Stock Variation` for `move.value` (ref `GR-VAL:<move id>`); on a vendor **return / RTV** (destination = supplier) it posts the exact reverse `Dr Stock Variation / Cr Stock Valuation` (ref `GR-RET-VAL:<move id>`). Both are idempotent by `ref` and only fire for `real_time` categories with the accounts + a stock journal set.
   - Switch ON (periodic): both receipt and return journals are suppressed; GL is trued up periodically by `retail.inventory.reconciliation`.
4. **Payment Vouchers & Payment Receipts**:
   - Two branded PDF documents are generated for payments on `account.payment`: a *Payment Voucher* for vendor/outbound payments and a *Payment Receipt* for customer/inbound payments. Each renders only for its matching payment direction.
5. **Periodic Inventory Reconciliation**:
   - Because receipts/deliveries do not post inventory journals in this setup, GL inventory-asset accounts drift from real on-hand value. `retail.inventory.reconciliation` computes, per valuation account, the actual stock value (`stock.quant.value`) vs the current GL balance and generates a DRAFT adjustment journal against an inventory-variation account for the accountant to review and post.

## Key Models
- `retail.inventory.reconciliation` — Manages periodic inventory reconciliations, computing differences between GL balances and actual stock values and producing a DRAFT `account.move`.
- `retail.inventory.reconciliation.line` — One line per stock-valuation account, holding the GL balance, stock value, and computed difference.
- `stock.move` — Overrides to skip GL journal entries on vendor goods-receipt moves.
- `stock.picking` — Overrides to validate receipt quantities against demand quantities.

## Important Fields
- **retail.inventory.reconciliation**:
  - `name`: Sequence-generated identifier. Defaults to `"/"` and is replaced in `create()` via the `retail.inventory.reconciliation` `ir.sequence` (prefix `INVREC/%(year)s/`).
  - `company_id`: Company for the reconciliation (defaults to the active company).
  - `date`: Date up to which posted GL balances are considered (default is today).
  - `journal_id`: General account journal used for generating the reconciliation entry.
  - `counterpart_account_id`: Inventory variation account where differences are booked when a category-level Stock Variation account is not set.
  - `line_ids`, `move_id`, `state` (draft/computed/generated), `total_difference` (computed sum of line differences), `currency_id`.

- **retail.inventory.reconciliation.line**:
  - `reconciliation_id`: Parent reconciliation (cascade delete).
  - `company_id`, `currency_id`: Related from the parent.
  - `account_id`: Valuation Account.
  - `counterpart_account_id`: Variation Account.
  - `book_value`: GL Balance.
  - `stock_value`: Actual on-hand stock value.
  - `difference`: Computed and stored, `stock_value − book_value`.

- **stock.move**:
  - `_is_retail_goods_receipt()`: True when the move enters from a supplier location (`location_id.usage == 'supplier'`).
  - `_is_retail_vendor_return()`: True when the move leaves to a supplier location (`location_dest_id.usage == 'supplier'`) — a vendor return / RTV.
  - `_retail_suppress_gr_journal()`: Reads the `suppress_gr_journal` config switch (default OFF).
  - `_should_create_account_move()`: Returns `False` for vendor receipts only when the suppress switch is ON; otherwise defers to core.
  - `_action_done()`: After super, calls `_retail_post_gr_journal()` (receipts) and `_retail_post_return_journal()` (vendor returns).
  - `_retail_book_valuation_entry(ref, label, incoming)`: Shared idempotent poster — `incoming=True` → Dr Valuation/Cr Variation; `incoming=False` → the reverse. No-op if already posted for `ref`, non-real-time category, missing accounts/journal, or zero `move.value`.

- **stock.picking**:
  - `button_validate()`: Validates the done quantity against demand quantities on incoming stock pickings (via `float_compare`). Raises an error if any line exceeds its demand.

## Public Methods
- **retail.inventory.reconciliation**:
  - `action_compute()`: Rebuilds lines, computing differences between GL balances and actual stock values.
  - `action_generate_move()`: Generates a DRAFT journal entry for the reconciliation and opens it.
  - `action_view_move()`: Returns an act_window action opening the generated `account.move`.
  - `_cron_generate_drafts()`: Creates one computed reconciliation per company and generates a DRAFT entry when a difference exists. Never posts automatically. Bound to an inactive monthly cron.

## Integration Points
- **Depends on**: `product`, `stock`, `stock_account`, `stock_delivery`, `purchase`, `account`.
- **Inherits from**: `stock.move` and `stock.picking`.
- **ir.sequence**: `seq_retail_inventory_reconciliation` (code `retail.inventory.reconciliation`, prefix `INVREC/%(year)s/`, padding 4).
- **ir.cron**: `cron_retail_inventory_reconciliation` runs `model._cron_generate_drafts()` monthly, shipped with `active=False`.
- **Reports**: Two `ir.actions.report` bound to `account.payment` (`action_report_payment_voucher`, `action_report_payment_receipt`) rendering QWeb templates `report_payment_voucher` / `report_payment_receipt`, using paperformat `paperformat_retail_payment` (`reports/paperformat.xml`).
- **Extended by**: None.
- **External calls**: None.
- **Cross-vertical**: The manifest names `prd_retail` / `rnd_retail` / `demo_retail` as the intended target databases, but nothing in the code enforces tenant scoping.

## Feature 9 — Trade/Non-Trade split + Operating Unit
- **Models**: `purchase.order` (`l10n_purchase_type`, numbering, `_prepare_invoice`),
  `purchase.order.line` (`_compute_analytic_distribution` merges the store OU),
  `account.move` (`l10n_purchase_type`), `account.move.line` (`_compute_account_id`
  remaps the payable per stream AND fills a non-trade product line that resolved to
  no account with `mapping.expense_account_id` — only when otherwise empty),
  `stock.picking` (`l10n_purchase_type`),
  `stock.move` (GR/IR routing + OU analytic on the GR journal),
  `stock.warehouse` (`l10n_ou_analytic_id`, `l10n_purchase_journal_id`),
  `retail.purchase.account.map` (config: payable + GR/IR + expense per company/type).
- **Numbering**: `data/po_sequences.xml` defines `purchase.order.retail.trade`
  (`PO/T/EBR/%(year)s/%(month)s/`) and `.nontrade` (`PO/NT/EBR/...`), both
  `use_date_range` + `no_gap`. Native date ranges are yearly, so
  `PurchaseOrder._retail_next_po_number` ensures a MONTHLY `ir.sequence.date_range`
  before drawing the counter → per-month reset. Absent sequences (non-the apparel brand DB)
  fall back to core `P` numbering.
- **Seeding**: `models/setup.py::seed_trade_ou(env)` — idempotent; creates the
  "Operating Unit" analytic plan + one analytic account & one purchase journal per
  warehouse (guarded by the two `stock.warehouse` link fields), and the mapping
  rows by account CODE (`with_company`, company-dependent). Run via
  `post_init_hook` (install) or `scripts/tenants/retail/40_setup_trade_ou.py`
  (existing DBs, since `-u` does not re-run post-init).
- **EBR account codes**: trade payable `2103100001`, non-trade payable
  `2103300001`, non-trade GR/IR `2103300008`. Trade GR/IR stays per product
  category (`account_stock_variation_id`, e.g. `2103109121` textile).

## Feature 8 — Admin fees (and card MDR) on payment registration
- **Models**: `retail.payment.register.fee` (TransientModel, one fee line on the
  wizard) and `account.payment.register` (`_inherit`), in
  `models/account_payment_register.py`.
- Each fee line carries its own COA, label and amount; the wizard total
  (`amount`) is recomputed as `<batch residual> + Σ fees` by
  `_onchange_admin_fee_line_ids`, so amounts never accumulate and clearing the
  lines restores the plain residual. The fees ride Odoo's native
  `write_off_line_vals` channel (`_prepare_admin_fee_write_off_vals`), so the
  counterpart still equals the bill residual and the bill reconciles in full.
- A **negative** amount nets the fee off an inbound receipt — that is how the
  `is_mdr` lines produced by `_onchange_x_card_bin` book card MDR, resolved
  against `retail.mdr.bin`. MDR lines are read-only in the list.
- **Several bills at once**: core leaves `group_payment` False for a multi-bill
  selection (one payment per bill), and a single fee cannot be split across
  them. Adding a fee line therefore ticks *Group Payments*, so one payment
  settles every selected bill with the fee charged once — the case that matters,
  since batching bills into one transfer is what saves the fee. Unticking
  *Group Payments* while fees exist raises in
  `_create_payment_vals_from_batch` rather than splitting the fee. Bills from
  **different partners** build more than one batch, so `can_edit_wizard` is
  False and the whole section stays hidden; register per partner.
- `_assert_admin_fee_balance` rejects a hand-edited `Amount` that no longer
  equals residual + fees, which would otherwise mis-reconcile the bill silently.
- **Tenant-neutral twin**: `custom_payment_admin_fee` (ee_gap) is the same
  feature without MDR/BIN and Operating Unit. Never install both on one
  database — both inherit the wizard and inject an "Admin Fees" group, so the
  section renders twice and the two onchange handlers fight over `amount`.

## Feature 13 — Indonesian bank master data (Kode BI)
- **Field**: `res.bank.l10n_id_bi_code` ("Kode BI") — the 7-digit Bank Indonesia
  clearing/RTGS participant code, a.k.a. *sandi bank* (`models/res_bank.py`).
  Surfaced on the core bank form/list and made searchable
  (`views/res_bank_views.xml`, inheriting `base.view_res_bank_form`,
  `base.view_res_bank_tree`, `base.res_bank_view_search`).
- **Seeding**: `data/res.bank.csv` ships 181 banks (name + SWIFT/BIC + Kode BI),
  external IDs `res_bank_<kodeBI>`, so re-running `-u` updates rather than
  duplicates. Loaded on install/upgrade; no separate script.
- **Kode BI is the unique key, not BIC.** Roughly 46 Bank Indonesia branch offices
  all share the BIC `INDOIDJA`, so BIC cannot identify a row. A `models.Constraint`
  enforces `unique(l10n_id_bi_code)`; NULL is allowed many times over, which is why
  the stock "Reserve" placeholder bank (no Kode BI) does not collide.
- **Consumer**: `retail.mdr.bin.acquirer_bank_id` — the Card BIN / MDR mapping's
  *Acquiring Bank* dropdown, which was effectively empty before this seed.
- **Data provenance**: 180 rows come from the customer's (EBR) list; its Kode BI
  column had lost leading zeros to Excel, so 29 are re-padded to 7 digits and
  `CITIBANK, NA` (`00310305`, 8 digits) is corrected to `0310305`. Bank Central
  Asia (`CENAIDJA` / `0140397`) was absent from that list and added separately —
  **its two values are not verified against the official BI participant list.**

## Feature 14 — Product-catalogue indexes (large variant counts)

The apparel brand catalogues carry ~32k templates / ~348k variants (Size × Inseam is
`create_variant='always'`, so Odoo materialises the full matrix). Two hot paths
were unindexed. `models/product_product.py` fixes both.

- **Product Variants list.** `product.product_product_tree_view` declares
  `default_order="is_favorite desc, default_code, name, id"`. `name` is an
  `_inherits` field on `product_template`, so ordering by it forced a LEFT JOIN
  plus a top-N sort over the whole catalogue (~280 ms for one 80-row page).
  `views/product_product_views.xml` drops `name` from the order, and
  `_retail_list_order_index` covers what remains. Page 1 is now sub-millisecond.
- **Valuation.** Core `product.value` (`stock_account`, Odoo 19's replacement for
  `stock.valuation.layer`) ships with a primary key and no other index, while
  `product.product._compute_value` → `_get_last_product_value` filters on
  `product_id / company_id / move_id / lot_id` and sorts by `date DESC`. Two
  indexes are created from `product_product.init()` — see the gotcha below for
  why they are NOT declared on a `product.value` model.

## Gotchas
- **Never `_inherit "product.value"` from this module.** Doing so pulls
  `product.value` into the module's `init_models()` pass, and
  `registry.check_foreign_keys()` then re-creates any *missing* core foreign key
  (`if spec is None: add_foreign_key(...)`). `prd_retail_begbal` and `rnd_retail`
  have lost `product_value_product_id_fkey` and still hold ~187k rows whose
  `product_id` no longer resolves, so the `ALTER TABLE` raises
  `ForeignKeyViolation` and rolls the whole upgrade back. The `product.value`
  indexes are therefore created with raw `CREATE INDEX IF NOT EXISTS` DDL inside
  `ProductProduct.init()`, which keeps that model out of the pass entirely.
  The same crash still awaits `-u stock_account` on those two databases until the
  dangling rows are removed.
- **Odoo renders `is_favorite desc` as `COALESCE("is_favorite", FALSE) DESC`.**
  A plain btree on `is_favorite` is therefore unusable for that sort; the index
  must be on the same expression. Odoo only emits `NULLS FIRST/LAST` when the
  order string spells it out (`odoo/orm/models.py:~2178`), so Postgres' defaults
  (DESC → NULLS FIRST, ASC → NULLS LAST) already match.
- **Odoo 19 silently ignores `_sql_constraints`.** The classic list-of-tuples form
  produces only a `WARNING ... no longer supported` line at upgrade and creates NO
  constraint. Use the `models.Constraint` class attribute instead (as
  `res_bank.py` does) and verify with
  `SELECT conname FROM pg_constraint WHERE conrelid='<table>'::regclass;` — a clean
  upgrade log proves nothing. `retail_mdr_bin.py` still uses the deprecated form, so
  its `bin_range_order` CHECK is **not enforced in the database**.
- **AP account type coercion**: an AP control account used on a bill's
  payment-term line MUST be `account_type = liability_payable` (core
  `account.move.line._check_payable_receivable` on purchase documents:
  `payment_term XOR liability_payable` must be False). The EBR CoA designates the
  non-trade payable as payable, but demo_updated_retail imported `2103300001` as
  `liability_current`, which broke non-trade bill posting. `seed_trade_ou`
  therefore coerces every mapped payable account to `liability_payable` +
  `reconcile=True` (logged). The GR/IR accounts are only used on `move_type=entry`
  journals, which that constraint does NOT check, so they need no coercion.
- Overriding a computed field's method re-declares `@api.depends` and REPLACES the
  inherited deps: `purchase.order.line._compute_analytic_distribution` restates
  the base deps (`product_id`, `order_id.partner_id`) plus `order_id.picking_type_id`.
  `account.move.line._compute_account_id` has NO base `@api.depends` (precompute-at-
  create), so the override adds none either and relies on `l10n_purchase_type` being
  set on the move at create time (via `_prepare_invoice`).
- Tenant scoping is a deployment convention only — the manifest documents the apparel brand databases as intended targets, but there is no runtime check preventing installation elsewhere.
- The `_cron_generate_drafts` cron is shipped with `active=False` (`data/inventory_reconciliation_data.xml:20`) and is monthly, so it does NOT run automatically unless a tenant enables it. When enabled it only creates DRAFT reconciliations/entries; it never posts.
- The payment reports are direction-guarded: the Payment Voucher renders only for outbound payments and the Payment Receipt only for inbound payments.

## Out of Scope
- This module does not cover inventory adjustments, backorders, or handling of internal transfers and manufacturing receipts. These functionalities are left to the core Odoo stock management processes.
- The GL-skip behavior focuses solely on vendor goods receipts; customer returns and outbound shipments keep posting normally.
- The payment vouchers and receipts are limited to vendor and customer payments; other types of financial transactions (e.g., intercompany) are not covered.
