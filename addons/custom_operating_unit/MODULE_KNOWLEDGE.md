# custom_operating_unit — module knowledge

**Read this before modelling `dim_operating_unit`.** It is the source of the Operating Unit
dimension and of the `res.users.allowed_operating_unit_ids` field the login-gateway reads for the
`allowed_ou` JWT claim (frozen contract 02).

---

## 1. What an Operating Unit is, and what it is not

An Operating Unit is a **unit of operation inside one company**: a branch, a depot, a POS cluster,
a regional sales desk. The three things it is routinely confused with:

| | `res.company` | `operating.unit` | `account.analytic.account` |
|---|---|---|---|
| Legal entity | yes | no | no |
| Own chart of accounts / currency | yes | no | no |
| Cardinality on a document | 1 | 1 | 0..n |
| Set at | document creation | document creation | line, often after the fact |
| Usable as a dimension key | yes | **yes** | no — a line can carry several |

The last row is the whole reason this module exists. Analytic accounts cannot be a warehouse
dimension key because a line may carry several of them at once; an Operating Unit is a single
attribute of the *document*, so `fct_* → dim_operating_unit` is a plain many-to-one.

---

## 2. Schema — `operating.unit`

| Column | Type | Notes |
|---|---|---|
| `name` | Char, required, indexed | |
| `code` | Char, required, indexed | **Natural key of `dim_operating_unit`.** Unique per company. Must not be recycled between units — reuse would silently re-point history. |
| `complete_name` | Char, stored compute, indexed | `Parent / Child`, recursive |
| `company_id` | Many2one `res.company`, required, indexed | |
| `parent_id` | Many2one `operating.unit`, indexed, `ondelete=restrict` | |
| `parent_path` | Char, indexed | `_parent_store = True` |
| `child_ids` | One2many | reverse of `parent_id` |
| `manager_id` | Many2one `res.users` | |
| `active` | Boolean | |

SQL: `UNIQUE (code, company_id)`.

### Hierarchy

`_parent_store = True`, so `parent_path` is maintained and `child_of` / `parent_of` domains work
and are index-backed. Two invariants are enforced:

1. **No cycles.** Odoo 19's `_parent_store` maintenance raises `UserError("Recursion Detected.")`
   before the model's own `@api.constrains` fires; both guards are present.
2. **A hierarchy never spans two companies.** A child whose `company_id` differs from its parent's
   is a `ValidationError`. So for the warehouse: *the company of a unit is the company of every one
   of its ancestors*, and `dim_operating_unit` can carry `company_id` as a flat attribute without a
   recursive lookup.

Depth is not limited by the model. The seed and every test use two levels
(`Cabang A` → `Depo A1`); do not assume two.

### For `dim_operating_unit`

* Natural key: `(company_id, code)`. `code` alone is unique only within a company.
* `complete_name` is a stored column, so it is available to logical decoding — you do not need to
  rebuild the path in SQL.
* `parent_path` is Odoo's materialised path (`"1/4/9/"`, ids separated by `/`). It is a convenient
  ready-made ancestry column if you want a flattened hierarchy bridge.
* The dimension is Type-1 as shipped: renaming a unit overwrites its name. If the warehouse needs
  Type-2 history, that is a warehouse concern — this module keeps no version history.

---

## 3. The stamped column

`operating_unit_id` is declared once, by `models/operating_unit_mixin.py::operating_unit_field()`,
and reused, so the five occurrences cannot drift apart.

| model | table | declared in |
|---|---|---|
| `sale.order` | `sale_order` | this module |
| `account.move` | `account_move` | this module |
| `stock.picking` | `stock_picking` | this module |
| `pos.order` | `pos_order` | this module |
| `ppob.transaction` | `ppob_transaction` | `custom_ppob`, importing `operating_unit_field` |

Every occurrence: `store=True`, `index=True`, `ondelete="restrict"`, default from
`res.users.default_operating_unit_id`, domain restricted to the document's own company.

Why each matters:

* `store=True` — a non-stored compute has no Postgres column, so logical decoding never sees it and
  `dim_operating_unit` could not be joined at all.
* `index=True` — creates `<table>_operating_unit_id_index`. The marts group by it on every query and
  the Phase 4 budget is p95 under 2 s with 12 months of data.
* `ondelete="restrict"` — deleting a unit that facts still reference would orphan history.

Asserted by `test_operating_unit_id_has_a_real_postgres_index`, which reads `pg_indexes` rather than
trusting the Python declaration.

### `stock.move` carries no `operating_unit_id`

Deliberate, per the brief's list of five targets. A `stock.move` reaches its unit through
`picking_id → stock_picking.operating_unit_id`. **Consequence for the warehouse:** moves with no
picking — inventory adjustments, scrap, some manufacturing consumption — have no Operating Unit at
all. Decide explicitly whether those rows belong in a unit-grained mart or in an "unassigned"
bucket; do not let the join silently drop them.

---

## 4. User entitlement — contract 02's source

```python
res.users.allowed_operating_unit_ids   # Many2many -> operating.unit  (relation table: operating_unit_res_users_rel)
res.users.default_operating_unit_id    # Many2one  -> operating.unit
```

**`allowed_operating_unit_ids` is the field name the login-gateway reads to populate the
`allowed_ou` claim.** Renaming it is a breaking change to contract 02.

A stable accessor is provided so the gateway does not have to care whether the entitlement is stored
or derived:

```python
user._pdp_allowed_operating_unit_ids()  -> list[int]
```

Both fields are added to `SELF_READABLE_FIELDS`, so a user can read their own entitlement without
Settings access. A `default_operating_unit_id` outside the allowed list is a `ValidationError`.

---

## 5. Record rules — and why they fail closed

Odoo rule algebra, restated because it decides whether this is secure: rules attached to **different
groups are OR-ed** for a user holding several of them; a rule with **no group is global and is
AND-ed** with everything.

Two rules per stamped model:

| rule | group | domain |
|---|---|---|
| `rule_<model>_operating_unit` | `base.group_user` (PPOB: `custom_ppob.group_ppob_user`) | `['|', ('operating_unit_id','=',False), ('operating_unit_id','in', user.allowed_operating_unit_ids.ids)]` |
| `rule_<model>_operating_unit_admin` | `custom_operating_unit.group_operating_unit_all` | `[(1,'=',1)]` |

So: a plain internal user sees their own units plus unit-less documents; a holder of **All Operating
Units** sees everything.

**A user with an empty allowed list sees only unit-less documents — not everything.** This is
fail-closed and it is a choice: the tempting alternative ("unassigned means unrestricted") turns
every newly created user into an accidental super-reader. Granting access is an explicit act.
`test_rules_fail_closed_for_a_user_with_no_units` pins it.

`base.user_root` and `base.user_admin` are granted `group_operating_unit_all` **once, at install**,
so a fresh database is administrable. **A real deployment revokes it from day-to-day accounts** —
otherwise the rules protect nothing for anyone with Settings access.

The grant is applied by `post_init_hook` in `hooks.py`, **not** by a `user_ids` field on the group
record, and that detail matters. `odoo/modules/loading.py` invokes `post_init_hook` only when the
update operation is `install`; on `upgrade` the branch is not taken. With the grant expressed as
`<field name="user_ids">` inside the `noupdate="0"` block, every `odoo -u custom_operating_unit`
re-applied it — silently re-granting the bypass to an operator who had deliberately revoked it,
during routine maintenance, with no message. A control that un-revokes itself is worse than one
never applied, because the operator believes the revocation holds and stops checking.

Moving the whole group record into `noupdate="1"` would also stop the re-grant, but `noupdate` is a
single flag on one `ir.model.data` row per XML ID — there is no per-field granularity — so it would
freeze `name`, `comment` and `implied_ids` against future updates too. The hook keeps the record
fully updatable and makes the membership one-shot.

Verified end to end: revoke admin from the group, run `odoo -d bct -u custom_operating_unit`
(exit 0), and admin is **still** out of the group afterwards.

Note that `group_operating_unit_manager` (may create and edit units) does **not** lift the record
rules. Administering the dimension is not the same entitlement as reading every unit's documents.

`operating.unit` itself carries a global multi-company rule
(`company_id = False OR company_id in company_ids`).

---

## 6. Groups

Odoo 19 removed `res.groups.category_id`; both groups hang off the `res.groups.privilege`
`custom_operating_unit.res_groups_privilege_operating_unit`.

| XML ID | Name | Meaning |
|---|---|---|
| `custom_operating_unit.group_operating_unit_manager` | Operating Unit Manager | creates and edits units |
| `custom_operating_unit.group_operating_unit_all` | All Operating Units | bypasses the per-unit rules; implies Manager |

---

## 7. Dependencies

`base, sale, account, stock, point_of_sale` — one per stamped model. `point_of_sale` is a heavy
dependency for one column; it is accepted because the brief names `pos.order` as a target and
because POS is a real revenue channel in the metric contract.

No dependency on `custom_pdp_core`: the classification rows for `operating.unit` and for the four
injected `operating_unit_id` columns are seeded by `custom_pdp_core` itself, which keys its registry
by model-name string precisely so it needs no dependency on the modules it classifies.
