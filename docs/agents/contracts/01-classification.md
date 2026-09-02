# Frozen contract 1 — PDP field classification (Security → DWH)

Status: **FROZEN at GATE 0.** Producer: `addons/custom_pdp_core`. Consumers: `custom_pdp_masking`,
the CDC loader, dbt. Changing a class or its masking means re-briefing every consumer.

Legal basis: UU 27/2022 (PDP). Art. 4(2) = *data pribadi umum*, Art. 4(3) = *data pribadi spesifik*.

## The taxonomy — exactly five classes, no others

| Class | Meaning | Examples on Odoo models |
|---|---|---|
| `public` | Non-personal, publishable | `product.template.name`, `res.company.name` |
| `internal` | Business data, not personal | `sale.order.amount_total`, `stock.move.product_qty` |
| `personal` | UU PDP Art. 4(2) general personal data | `res.partner.name`, `.email`, `.phone`, `.street`, `.city` |
| `sensitive` | UU PDP Art. 4(3) specific personal data | NIK/KTP, NPWP, health, biometric, religion, bank account |
| `secret` | Credentials and key material | `res.users.password`, API tokens, webhook secrets |

## Masking applied **during load**, before the row lands in the warehouse

Master prompt §3.2 and anti-pattern §7.5: no unmasked personal data ever reaches `raw_`. Masking is
applied by the CDC loader, not by dbt and never by the BI layer.

| Class | Transform at load | Rationale |
|---|---|---|
| `public` | none | — |
| `internal` | none | — |
| `personal` | `hmac_sha256(value, per_tenant_salt)` → 64-char hex, **deterministic** | Preserves joins and distinct-counts; destroys readability. Same partner hashes identically within a tenant, differently across tenants. |
| `sensitive` | `hmac_sha256(value, per_tenant_salt)`; free-text fields dropped to `NULL` | No reveal of any kind. A hash of a NIK is still not a NIK. |
| `secret` | **dropped at extraction** — the column is never selected | Structurally cannot land. Anti-pattern §7.9. |

Per-tenant salt lives in SOPS (`WAREHOUSE_MASK_SALT_<TENANT>`), never in a file, never in git,
`changeme` in `.env.example`. Rotating a salt invalidates historical joins — treat as a migration.

## Declaration surface

`custom_pdp_core` exposes model `pdp.field.classification` with columns
`(model_name, field_name, pdp_class, legal_basis, notes)` and a JSON-RPC-reachable read method.
The CDC loader reads this table at startup and refuses to start if a column it is about to extract
carries **no** classification. Unclassified is a hard failure, never a silent default to `public`.

## Acceptance

- A test asserts `res.partner.name` is unreadable in `raw_res_partner` and in every mart.
- A test asserts a `secret`-class column does not exist as a warehouse column at all.
- A test asserts the loader exits non-zero when a classification row is missing.

## Two different controls — do not confuse them (added at GATE 1)

This contract governs **warehouse masking at load**. `custom_pdp_masking` also implements an
**in-Odoo UI mask**. They are different controls with different reach, and overstating the second is
what makes it dangerous.

| Control | Where | What it stops | What it does NOT stop |
|---|---|---|---|
| Warehouse masking (this contract) | CDC loader, before the row lands in `raw` | Any reader of the warehouse, including the dashboard, exports and a stolen warehouse backup | Nothing downstream — there is no unmasking path, by construction |
| In-Odoo UI mask | `read()` override on the mixin | The list/form/kanban UI and RPC reads for users lacking `group_pdp_data_viewer` | A Settings admin, a `sudo()` server action, direct database access — and **any read funnel that does not route through `read()`** |

**`read()` is not the only funnel.** Confirmed in the pinned image at GATE 1:
`odoo/orm/models.py:806` inside `_export_rows` reads via `value = record[name]`, which is
`__getitem__` → ORM cache → `_read`, so it never calls the public `read()`. The CSV/XLSX export path
therefore bypassed the UI mask entirely until it was closed.

Two consequences that bind every future change:

1. **Adding a new read path is a security change.** Before shipping one, ask whether it routes
   through the overridden funnel. `export_data` did not.
2. **Odoo Settings access is effectively root.** The in-Odoo mask is a surface control, not a
   containment boundary. Tenant isolation and personal-data containment for analytics rest on the
   warehouse side — load-time masking plus RLS — not on the Odoo UI mask.

## Ruling — company-dependent (jsonb) columns are never HMAC'd as a blob (GATE 3)

Raised by the DWH agent: `res.partner.barcode` was classified `personal` → `hmac_sha256`, but its
physical type is `jsonb`, and the HMAC spec takes `str` and raises `TypeError` otherwise.

Verified by the Lead against the live database rather than taken on report:

- `res_partner.barcode` is `udt_name = jsonb` and **`company_dependent = t`**. Odoo stores a
  company-dependent field as a jsonb map keyed by company id — `{"1": "BC123", "2": "BC456"}`.
- It is the **only** non-text column classified `personal`. `res.partner.properties` is also jsonb but
  is `sensitive` + `drop_to_null`, so it goes to NULL and was never affected.
- It is referenced by **no** metric in contract 03 and by no mart requirement in Phase 3.
- 0 of 48 seeded partners populate it, which is why nothing is broken today.

**Ruling: `res.partner.barcode` is reclassified `sensitive` + `drop_to_null`. It does not land.**

Hashing the blob was rejected on the merits, not for convenience. A digest of a company-keyed map is
not a stable identifier for a person: it changes when *any* single company's value changes, it is
useless as a join key, and its very presence leaks how many companies hold a value for that partner.
That is false precision — a number that looks like a pseudonymous identifier while behaving like
none. Dropping a personal identifier that no metric asks for is the honest default.

**General rule, binding on every agent** (generalised by Security from "jsonb" to the property that
actually matters, so nobody reads this as a *type* rule and is then caught by a `company_dependent`
column that happens to be text):

> A value that is a **map keyed by anything other than the data subject** is never HMAC'd as a whole.
> If analytics ever needs one, it is hashed **per value, with the key preserved**, as a deliberate
> contract change.

The decisive property is not `jsonb`. It is that hashing such a map conflates several companies'
values for one person into a single digest, so the result is a pseudonym of nothing: it neither
identifies the person nor survives as a join key. That is why "pick a canonical JSON rendering and
hash it" would have been the wrong fix even though it is technically available — it produces a
stable digest of a thing that is not an identifier. The cardinality leak is a second, independent
reason: the mere presence of a value discloses how many companies hold one for that partner.

**Loader consequence — a startup validation, not a per-row check.** The "hard-fail on unclassified"
rule is not sufficient on its own. At startup, over the **whole** classification map, for every
column to be extracted:

| Condition | Action |
|---|---|
| no classification row | refuse to start *(existing)* |
| `transform = hmac_sha256` and column type is not `text` / `varchar` / `bpchar` / `name` | refuse to start *(new)* |

It must be a startup pass rather than a per-row guard: per-row would fail partway through a load and
leave a half-populated `raw_` table that looks like a transient error rather than a contract
violation. Startup means the operator learns before anything lands.

**Text types only — not "anything castable to text".** A bigint identifier *is* hashable if you cast
it, but the choice of canonical rendering is precisely the ambiguity that produced this defect. Any
non-text type requires an explicit contract decision, not an implicit cast.

### The map rule is a transform rule, not a drop rule (clarified 2026-08-31, `account.account`)

Ruled by Security, whose veto governs this file's classifications. Recorded by the Lead because
`docs/agents/contracts/**` is the Lead's path.

The barcode ruling above has two separable limbs, and **only the first generalises**:

- **Limb A — the rule.** "A value that is a map keyed by anything other than the data subject is
  never HMAC'd as a whole." This constrains the **transform**. It is satisfied by any class whose
  transform is not a hash — `public` and `internal` satisfy it *by construction*.
- **Limb B — the barcode remedy.** Reclassify to `sensitive` + `drop_to_null`. This followed from
  `res.partner.barcode` being **personal data of a natural person** that no metric required. It does
  **not** follow from the storage shape.

The question that precedes both is *"is this personal data at all?"*. `account.account.code_store`
is a company-keyed jsonb map (`{"1": "101000"}`, `company_dependent = t`) with the same physical
shape as barcode, and it identifies a **ledger account, not a person**. Neither Art. 4(2) nor
Art. 4(3) is engaged, so limb A is satisfied and limb B does not apply: `internal` / `none`, and the
map lands verbatim. The same holds for `account.account.name` and `.description`, which are
`translate = standard` and therefore jsonb maps keyed by **language** — also `internal`.

Positive form of limb A, for readers rather than maskers: a consumer that wants a value out of such
a map extracts it **per key** (`code_store ->> <company_id>`) and never casts the blob to text.

**Free text is the mirror-image case, and the automated guard does not cover it.**
`account.account.note` is physical type `text`, so the `TEXT_TYPES` startup check in
`analytics/cdc/bct_cdc/policy.py:42` would **not** flag a `personal` classification: it would pass
validation and land a clean 64-character digest for every note. That digest is **a pseudonym of
nothing** — prose is not a join key, distinct notes stay distinguishable, and the value looks
exactly like a working hash. Free text is therefore `sensitive` + `drop_to_null` on its **content
risk, not its type**, joining `res.partner.comment`, `account.move.narration`, `sale.order.note`,
`stock.picking.note` and `pos.order.line.note`.

**The type guard catches unhashable columns; it cannot catch pointlessly-hashable ones. That
judgement stays with the classifier.**

#### Why this clarification was needed

The GATE 3 barcode section, as written, let a careful reader derive "company-dependent map =>
`drop_to_null`" for **any** such column. That is wrong, and it is what made Platform-Addons stop and
escalate rather than decide — correctly, since the text supported the wrong reading. The Lead
generalised limb A without marking limb B as fact-specific; this paragraph repairs that.

#### Gate evidence for this ruling

Per the process rule below, and stated in the **positive** form the empty-result rule requires:

```sql
select count(*) from pdp_field_classification where model_name = 'account.account';   -- MUST be 16
select count(*) from warehouse.column_policy where source_table = 'account_account';  -- MUST be 16
```

**Assert the count is 16 — not that no bad row appeared.** Both queries would return empty after a
botched upgrade exactly as they do before the work starts, and the two outcomes are
indistinguishable.

**The two queries become due at different times, and conflating them will make a correct state look
broken.** Corrected by the Lead 2026-08-31 after checking, because the original wording implied they
land together:

| # | Query | Owner | Due when | Status 2026-08-31 |
|---|---|---|---|---|
| 1 | `pdp_field_classification` = 16 | Platform-Addons | on module upgrade | **16 — PASSED** |
| 2 | `warehouse.column_policy` = 16 | Data Warehouse | only after `account_account` joins the replicated set | **0 — NOT YET DUE** |

`warehouse.column_policy` covers **replicated tables only**: it holds 698 rows across exactly the 15
tables present in `raw.*`, and `account_account` is not among them. A `0` there today is the correct
reading of a correct state, not a propagation failure. It becomes a real failure the moment DWH adds
the table to the replicated set and regenerates `raw.*` — which DWH deferred precisely until these
classification rows existed.

Verified in the live database: `account_type` -> `internal` / `drop_to_null=f`, `code_store` ->
`internal` / `f`, `note` -> `sensitive` / `t`, matching Security's ruling exactly. **Limb 1 of the
ruling is IN FORCE. Limb 2 is pending Data Warehouse.**

## Process rule — an amendment is not in force until it reaches its producer

This ruling was written into this document while
`addons/custom_pdp_core/data/pdp.field.classification.csv:453` and the live
`pdp_field_classification` table still said `personal` / `drop_to_null=f`. The CDC loader reads the
**table**, not this prose, so for a period the amendment had no effect while appearing settled.

Binding on the Lead: a contract amendment is not complete until the producer is changed and the
change is observable in the running system. **Gate evidence for a classification change is a query
against the database, never a diff of this file.**
