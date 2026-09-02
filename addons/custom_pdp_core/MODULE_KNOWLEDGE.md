# custom_pdp_core — module knowledge

Producer of **frozen contract 01** (`docs/agents/contracts/01-classification.md`).
Consumers: `custom_pdp_masking`, the Phase 3 CDC loader, dbt.

Verified against `odoo:19.0@sha256:f99ffac9…` — Odoo **19.0-20260817**, Community edition.
No Enterprise dependency.

---

## 1. Schema — `pdp.field.classification`

| Column | Type | Notes |
|---|---|---|
| `model_name` | Char, required, indexed | Odoo model technical name, e.g. `res.partner`. **Free text on purpose** — see §1.1. |
| `field_name` | Char, required, indexed | The **physical Postgres column name**, not an ORM field name — see §2. |
| `pdp_class` | Selection, required, indexed | Exactly one of `public \| internal \| personal \| sensitive \| secret`. |
| `legal_basis` | Char | Article of UU 27/2022 justifying the class. |
| `drop_to_null` | Boolean, default `False` | See §3. |
| `notes` | Text | Why this class was chosen. |
| `active` | Boolean, default `True` | Archiving a row makes the column **unclassified**, which is a loader hard-failure. |

SQL objects (Odoo 19 `models.Constraint`, **not** the removed `_sql_constraints`):

```sql
UNIQUE (model_name, field_name)
CHECK (drop_to_null IS NOT TRUE OR pdp_class = 'sensitive')
```

### 1.1 Why `model_name` is a Char, not a `Many2one` to `ir.model`

The module depends on **`base` only**. The registry is a declarative catalogue that must be
installable into any database, and it must be able to classify `ppob.transaction` before
`custom_ppob` is installed — modules are installed one at a time, in dependency order, and the
loader may be pointed at a database where a given app is absent. A foreign key would make the
seed order a dependency graph problem for no benefit. The cost is that a typo in a model name is
not caught by the database; `check_coverage()` and the module's coverage test catch it instead.

---

## 2. The unit of classification is a Postgres column, not an ORM field

The CDC loader reads Postgres via logical decoding. What it sees is columns. So:

* `one2many` and `many2many` "fields" are **not** classified. They have no column
  (`ir_model_fields.store` is `True` for them, which is why `ir.model.fields` is the wrong source);
  an o2m is a reverse relation and an m2m lives in its own relation table.
* Non-stored computed fields are **not** classified — no column, never decoded.
* The magic columns `id`, `create_uid`, `create_date`, `write_uid`, `write_date` **are** classified
  (all `internal`), because they do land in the warehouse.
* `fields.Image` / `fields.Binary` with `attachment=True` (e.g. `sale.order.signature`,
  `stock.picking.signature`, `res.partner.image_1920`) have **no column on their own table** — the
  bytes live in `ir_attachment`. They are therefore absent from this registry. See §7.

The seed is machine-generated from `information_schema.columns` by
`tools/generate_classification_seed.py`, with the *decisions* hand-written in that file's
`OVERRIDES` map. That generator now **refuses to write** a seed in which a column classified for
hashing has a non-text physical type (`assert_hashable`) - the producer-side mirror of the loader's
startup validation, so the `res.partner.barcode` shape fails in the hand of the person making the
decision instead of in the warehouse hours later. Regenerate with:

```
python3 addons/custom_pdp_core/tools/generate_classification_seed.py \
    --dsn "postgresql://odoo:<password>@127.0.0.1:35432/erp_dev" \
    --out addons/custom_pdp_core/data/pdp.field.classification.csv
```

The tool is **not** imported by any `__init__.py`; Odoo never runs it. Its output is committed.

---

## 3. `drop_to_null` — the declaration surface contract 01 implies

Contract 01 says `sensitive` → "`hmac_sha256`; free-text fields dropped to `NULL`". It does not say
how the loader learns which columns are free text. This boolean is that declaration.

* It is honoured **only** for `pdp_class = 'sensitive'`; a CHECK constraint enforces that. This is
  deliberate — it keeps the taxonomy at five classes rather than sprouting a sixth.
* It is set on two kinds of column:
  1. genuinely free text (`res.partner.comment`, `account.move.narration`,
     `pos.order.internal_note`, `ppob.transaction.failure_reason`) — anything can be typed there,
     including an Art. 4(3) identifier, so nothing may survive;
  2. non-join-bearing personal data where a digest would carry no analytic value
     (`res.partner.partner_latitude` / `partner_longitude`).

---

## 4. JSON-RPC surface — what the CDC loader calls

All four are `@api.model` + `@api.readonly`, callable through
`/jsonrpc` → `object.execute_kw(db, uid, pwd, 'pdp.field.classification', '<method>', [args])`.
Read access is granted to `base.group_user`; write to `custom_pdp_core.group_pdp_officer`.

### `get_classification_map(model_names=None) -> dict`

**This is the startup call.** `model_names` is an optional list; `None` returns everything.

```json
{
  "contract": "01-classification",
  "module_version": "19.0.1.0.0",
  "classes": ["public", "internal", "personal", "sensitive", "secret"],
  "models": {
    "res.partner": {
      "email": {
        "pdp_class": "personal",
        "drop_to_null": false,
        "legal_basis": "UU 27/2022 Art. 4(2) - data pribadi umum",
        "notes": "Direct identifier of a natural person; hashed at load so joins survive."
      }
    }
  }
}
```

Only `active` rows appear.

### `get_classification(model_name, field_name) -> dict | False`

Returns `False` — **not** a default class — when the column is unclassified. Contract 01 forbids a
silent default to `public`; the loader must exit non-zero on `False`.

### `get_unclassified_fields(model_name, field_names) -> list[str]`

The gate. Call it with the columns about to be extracted; a non-empty return is a hard failure.

### `get_extractable_fields(model_name) -> list[str]`

The columns the loader may name in its `SELECT`. `secret` columns are **absent from this list**, so
they are structurally incapable of reaching the warehouse (anti-pattern 7.9). Do not build the
SELECT list any other way.

### `check_coverage(model_names) -> {model: [column, …]}`

Self-check used by the module's own test. Models not installed in the database are skipped.

---

## 5. Groups

Odoo 19 removed `res.groups.category_id`; groups are grouped by a `res.groups.privilege` record,
which itself carries the `ir.module.category`. Both groups below live under the privilege
`custom_pdp_core.res_groups_privilege_pdp`.

| XML ID | Name | Meaning |
|---|---|---|
| `custom_pdp_core.group_pdp_data_viewer` | Data Viewer | Reads `personal`/`sensitive` columns unmasked in the Odoo UI. |
| `custom_pdp_core.group_pdp_officer` | Officer | Curates this registry. Implies Data Viewer. |

**Neither is implied by `base.group_system`.** Being a technical administrator is not by itself a
lawful basis to read personal data, so a fresh install masks for *everyone*, the `admin` user
included. To see cleartext in the UI:

```
Settings → Users → <user> → Personal Data (PDP) → Data Viewer
```

---

## 6. The seeded map

740 rows, 17 models. Counts by class, from the installed database:

| class | rows |
|---|---|
| `public` | 19 |
| `internal` | 664 |
| `personal` | 28 |
| `sensitive` | 22 |
| `secret` | 7 |

`internal` dominates because it is the honest answer for the great majority of columns on a sales
order or a journal entry, and because contract 01's own example places `sale.order.amount_total`
there. `internal` is *not* a fallback that hides a decision: it means "business record, no personal
content", it is neither published nor dropped, and every column that is *not* internal was decided
by hand.

### The non-`internal` columns, in full

**`secret` — dropped at extraction, never selected (7)**

| column | why |
|---|---|
| `res.users.password` | credential |
| `res.users.totp_secret` | second-factor seed |
| `sale.order.access_token` | portal share token |
| `account.move.access_token` | portal share token |
| `pos.order.access_token` | portal receipt token |
| `pos.order.ticket_code` | portal receipt code |
| `account.move.inalterable_hash` | audit-trail chain value; publishing it would let the chain be replayed |

**`sensitive` — UU 27/2022 Art. 4(3) (21).** `[NULL]` marks `drop_to_null`.

| column | why |
|---|---|
| `res.partner.vat` | Indonesian NPWP, and NIK for a *perorangan* under Coretax |
| `res.partner.barcode` `[NULL]` | `company_dependent`, so stored as a per-company **jsonb map** — see below |
| `ppob.transaction.customer_ref` | subscriber/meter number: identifies a household and its consumption |
| `res.partner.comment` `[NULL]` | free text |
| `res.partner.picking_warn_msg` `[NULL]` | free text |
| `res.partner.sale_warn_msg` `[NULL]` | free text |
| `res.partner.properties` `[NULL]` | user-defined JSON, arbitrary content |
| `res.partner.partner_latitude` `[NULL]` | precise geolocation, Art. 4(3) huruf g |
| `res.partner.partner_longitude` `[NULL]` | precise geolocation, Art. 4(3) huruf g |
| `res.users.signature` `[NULL]` | free rich text |
| `res.users.out_of_office_message` `[NULL]` | free text |
| `sale.order.note` `[NULL]` | free text |
| `account.move.narration` `[NULL]` | free text |
| `stock.picking.note` `[NULL]` | free text |
| `stock.move.description_picking_manual` `[NULL]` | free text |
| `pos.order.general_customer_note` `[NULL]` | free text |
| `pos.order.internal_note` `[NULL]` | free text |
| `pos.order.line.customer_note` `[NULL]` | free text |
| `pos.order.line.note` `[NULL]` | free text |
| `pos.order.line.notice` `[NULL]` | free text |
| `ppob.transaction.failure_reason` `[NULL]` | biller free text, may echo subscriber details |
| `account.account.note` `[NULL]` | free text on a ledger account - see §6.1 |

**`personal` — UU 27/2022 Art. 4(2), hashed so joins survive (28)**

`res.partner`: `name`, `complete_name`, `commercial_company_name`, `company_name`, `email`,
`email_normalized`, `phone`, `phone_sanitized`, `street`, `street2`, `city`, `zip`, `function`,
`ref`, `website`, `company_registry`, `global_location_number`, `peppol_endpoint`,
`signup_type`
· `res.users`: `login`
· `sale.order`: `client_order_ref`, `signed_by`
· `account.move`: `invoice_source_email`, `invoice_partner_display_name`
· `pos.order`: `email`, `mobile`, `floating_order_name`
· `ppob.transaction`: `customer_name`

**`public` — publishable (19)**

`res.company`: `name`, `email`, `phone`, `company_details`, `report_header`, `report_footer`,
`invoice_terms`, `invoice_terms_html`
· `product.template`: `name`, `default_code`, `description`, `description_sale`,
`public_description`, `list_price`
· `product.product`: `default_code`, `barcode`
· `ppob.biller`: `name`, `code`, `category`

### 6.1 `account.account` — the chart of accounts, added for the warehouse

Added on the Data Warehouse agent's request: `fct_account_move_line` needs `account_type`. Before
these rows existed, `warehouse_ctl.py sync-policy` hard-failed **exit 2 on all 16 columns**, which
is contract 01 working exactly as designed — unclassified is a hard failure, never a silent
`public`.

`account.account` is a **configuration** table: a catalogue of ledger accounts authored by an
accountant. It is not a record about a natural person, so 15 of 16 columns are `internal`. Two
required Security sign-off before the replicated set could change; **Security ruled on 2026-08-31
and approved both as written.**

| column | udt | class | why |
|---|---|---|---|
| `account_type` | varchar | `internal` | The column DWH needs. `internal` carries no transform, so it lands readable. |
| `code_store` | **jsonb**, `company_dependent` | `internal` | See below. |
| `note` | text | `sensitive` `[NULL]` | Odoo's "Internal Notes"; free text on a ledger account. |
| `name`, `description` | **jsonb**, `translate` | `internal` | Language-keyed maps; account labels, not personal data. |
| the other 11 | — | `internal` | booleans, m2o ids and the five magic columns. |

**`code_store` is the `res.partner.barcode` shape and does NOT get the barcode remedy.** Security's
ruling, worth restating because the generalised rule is easy to over-read: contract 01's barcode
ruling has two limbs.

* **Limb A — the rule.** *"A value that is a map keyed by anything other than the data subject is
  never HMAC'd as a whole."* This is a **transform** rule and it **does** govern `code_store`, which
  is a jsonb map keyed by company id. `internal` satisfies it *by construction*: the internal
  transform is `none`, so nothing is hashed and the map lands verbatim.
* **Limb B — the barcode *remedy*** (reclassify to `sensitive` + `drop_to_null`) **does not
  follow.** That remedy protects a natural person whose identifier no metric needed. A GL account
  code identifies a ledger account. There is no *subjek data*, neither Art. 4(2) nor Art. 4(3) is
  engaged, and there is nothing to drop.

The question that precedes both limbs is *"is this personal data at all?"*, and for a chart of
accounts the answer is no. **`company_dependent` jsonb does not imply `drop`** — it is not a type
rule and not a storage-shape rule.

Forward constraint from the same ruling: because the transform is `none`, the raw map lands verbatim
in `raw.account_account.code_store`. Any future model wanting "the account code" must extract per
company key (`code_store ->> <company_id>`) and **never cast the blob to text** — limb A's positive
form ("hashed per value, with the key preserved") applied to reading instead of masking.

**`note` is `sensitive` + `drop_to_null`, and the class is doing real work.** Its physical type is
`text`, so the loader's non-text guard would *not* have caught a `personal` classification: it would
pass startup validation and land a clean 64-char digest of every note. That digest is a pseudonym of
nothing — prose is not a join key — while looking exactly like working masking. Same false-precision
failure as hashing the barcode blob, arriving through a type the automated guard cannot flag.
`account.account.note` is the **sixth** member of the registry's free-text set
(`res.partner.comment`, `account.move.narration`, `sale.order.note`, `stock.picking.note`,
`pos.order.line.note`), not a new decision. 0 of 52 rows are populated today, which is exactly the
barcode situation: nothing is broken either way right now, so no test would catch it being wrong.

### `company_dependent` columns are never hashed — an enforced invariant

A `company_dependent` field is not a scalar. Odoo stores it as a **jsonb map keyed by company id**:

    res_partner.barcode = {"1": "BC123", "2": "BC456"}

Hashing that map yields a digest of a composite. It identifies nobody, it joins to nothing, and its
mere presence still discloses how many companies hold a value for that person — strictly worse than
either keeping or dropping the value. So any `company_dependent` column that would otherwise be
`personal` or `sensitive` is forced to `sensitive` + `drop_to_null`.

`res.partner.barcode` is the only column in the current schema this affects; every other
`company_dependent` stored column is `internal`, where the transform is a no-op and the jsonb shape
is harmless. `generate_classification_seed.py::enforce_company_dependent` applies the rule, and
`test_company_dependent_columns_are_never_hashed` asserts it **against the live database**, so a
future Odoo release that makes another column `company_dependent` fails the build instead of
silently shipping a meaningless digest.

### The broader invariant — no NON-TEXT column is classified for hashing

`company_dependent` is one way to get a jsonb column; `translate=True` is another
(`account.account.name` and `.description` are language-keyed jsonb maps), and a numeric column
classified `personal` would be a third. Contract 01's loader-side startup validation covers all of
them: `transform = hmac_sha256` over a non-`text`/`varchar`/`bpchar`/`name` column is a refusal to
start. Two mirrors of it now live on the producer side, so the defect cannot leave this module:

* `generate_classification_seed.py::assert_hashable` — **refuses to write the CSV**;
* `test_no_non_text_column_is_classified_for_hashing` — asserts it against the live database, over
  every classified column and not only the `company_dependent` ones.

That test asserts **two** things, because a query whose passing state is an empty result is
indistinguishable from a query that examined nothing (PLAN.md instance 12): the offender list is
empty, **and** the population it searched was not.

The type check is a guard, not the decision. It cannot catch a `text` column that is wrongly
classified `personal` — `account.account.note` is exactly that case, and only the class choice
protects it. Verified by restoring the broken condition:

```
account.account.code_store -> personal   (jsonb)  ->  exit 3, UnhashableColumn
account.account.name       -> personal   (jsonb)  ->  exit 3, UnhashableColumn
account.account.note       -> personal   (text)   ->  exit 0, ACCEPTED, hashed
```


### Judgement calls worth challenging at a gate

* **`res.partner` is treated as personal regardless of `is_company`.** One table holds both natural
  persons and legal entities and no column distinguishes them reliably at load time. Classifying by
  column, the safe answer is `personal`.
* **`res.company` contact details are `public`.** A company is a legal entity, not a *subjek data*
  under UU 27/2022, and these values are printed on every invoice.
* **Amounts stay `internal`.** Contract 01's own example puts `sale.order.amount_total` at
  `internal`, so transaction values, `res.partner.credit_limit` and the partner aggregates follow.
  A stricter reading would call an individual's invoice total *data keuangan pribadi* under
  Art. 4(3); the contract settles it the other way and the contract wins.
* **Document line labels (`account.move.line.name`, `sale.order.line.name`) stay `internal`.** They
  default from the product name; the warehouse needs them for line-level reporting. The genuinely
  narrative fields above are the ones dropped.
* **`res.partner.lang` and `.tz` stay `internal`.** Weakly identifying, but they are dimension keys
  and not Art. 4(2) identifiers on their own.

---

## 7. Scope boundaries — stated, not hidden

* **`ir_attachment` is not classified and must not be extracted.** Binary and Image fields with
  `attachment=True` store their bytes there, and an attachment can be anything at all — a scanned
  KTP included. The CDC loader must not read `ir_attachment`; there is no classification that would
  make it safe.
* **Coverage is relative to a module set.** The seed covers the physical columns present with
  `base, sale, sale_management, sale_stock, account, stock, point_of_sale, product` installed.
  Installing a further module that extends one of the 13 stock models adds columns the registry
  does not know, and `test_no_installed_column_is_unclassified` will fail — that failure is the
  feature. Regenerate the seed (§2) when the platform's module set changes.
* **`res.users` is classified here but is deliberately exempt from *UI* masking.** See
  `custom_pdp_masking/MODULE_KNOWLEDGE.md`.

---

## 8. Odoo 19 gotchas encountered while building this

Recorded because the next agent to touch an addon will hit them.

| Odoo ≤ 18 | Odoo 19 |
|---|---|
| `res.partner.mobile` | **removed** — only `phone` remains. (`pos.order.mobile` still exists.) |
| `_sql_constraints = [(...)]` | **ignored**, with a log warning. Use `models.Constraint(...)` / `models.UniqueIndex(...)` as class attributes named with a leading `_`. |
| `res.groups.category_id` | **removed**. Use `privilege_id` → `res.groups.privilege` → `category_id`. |
| `res.users.groups_id` | `group_ids`. |
| `<tree>` | `<list>`. |
| search-view `<group expand="0" string="Group By">` | `<group>` with no attributes; the RNG rejects `expand` and `string`. |
| `fields.Char(..., unaccent=False)` on `parent_path` | `unaccent` is not a valid parameter; drop it. |
| `odoo/models.py` | package moved to `odoo/orm/models.py`, re-exported via `odoo.models`. |
