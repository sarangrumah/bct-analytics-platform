# custom_ppob — module knowledge

Source table for the PPOB fact named in master prompt §3.1. Read this before modelling
`fct_ppob_transaction` or `dim_biller`.

---

## 1. `ppob.biller` — the `dim_biller` source

| Column | Type | Notes |
|---|---|---|
| `name` | Char, required, indexed | |
| `code` | Char, required, indexed, **UNIQUE** | Natural key of `dim_biller`. Must not be recycled. |
| `category` | Selection, required, indexed | `electricity \| water \| telco \| internet \| insurance \| multifinance \| tax \| other` |
| `sla_target_seconds` | Integer, default 30 | `CHECK (> 0)`. The denominator of every SLA metric. |
| `company_id` | Many2one `res.company`, indexed | may be empty = shared across companies |
| `active` | Boolean | |

`display_name` renders as `[CODE] Name`.

---

## 2. `ppob.transaction` — the fact

One row is one bill payment or top-up forwarded to a biller.

| Column | Type | Notes |
|---|---|---|
| `name` | Char, required, **UNIQUE**, readonly | `PPOB/<year>/<6 digits>` from `ir.sequence` code `ppob.transaction` |
| `partner_id` | Many2one `res.partner`, indexed | optional — counter sales are often anonymous |
| `biller_id` | Many2one `ppob.biller`, **required**, indexed, restrict | |
| `product_id` | Many2one `product.product`, indexed, restrict | the denomination/bill type, so PPOB joins `dim_product` like any other revenue line |
| `operating_unit_id` | Many2one `operating.unit`, stored, **indexed**, restrict | same declaration as the four stock models — see `custom_operating_unit/MODULE_KNOWLEDGE.md` §3 |
| `company_id` | Many2one `res.company`, required, indexed | |
| `currency_id` | Many2one `res.currency`, required | |
| `amount` | Monetary | what the biller is owed; **excludes** the admin fee |
| `admin_fee` | Monetary | charged to the customer on top |
| `commission` | Monetary | the share of `admin_fee` retained as revenue |
| `total_amount` | Monetary, **stored compute** | `amount + admin_fee` = what the customer paid |
| `customer_ref` | Char, indexed | subscriber / meter / policy number. **`sensitive`** |
| `customer_name` | Char | name from the biller inquiry. **`personal`** |
| `state` | Selection, required, indexed | see §3 |
| `requested_at` | Datetime, required, indexed | |
| `settled_at` | Datetime, readonly, indexed | |
| `sla_seconds` | Integer, **stored compute**, indexed | see §4 |
| `sla_breached` | Boolean, stored compute | `sla_seconds > biller.sla_target_seconds` |
| `failure_reason` | Text | **`sensitive` + `drop_to_null`** |
| `biller_reference` | Char | the biller's own settlement id, for reconciliation |

SQL constraints:

```sql
CHECK (amount >= 0 AND admin_fee >= 0 AND commission >= 0)
CHECK (commission <= admin_fee)
UNIQUE (name)
```

### Revenue semantics — get this right in the mart

`amount` is **pass-through**: it is collected from the customer and owed to the biller, and it is
not revenue. The revenue of a PPOB transaction is `commission`. `admin_fee - commission` is the
share that belongs to the biller or the channel.

A mart that sums `amount` or `total_amount` and calls it revenue will overstate it by roughly
40×, since a typical row here is `amount` 100 000 IDR against `commission` 1 500 IDR.

---

## 3. The state machine — dbt asserts `accepted_values` on it

```
draft ──submit──> pending ──succeed──> success ──reverse──> reversed
                      │
                      └────fail─────> failed
```

`failed` and `reversed` are terminal. Those are the **only five transitions**:

```python
PPOB_TRANSITIONS = {
    "draft":    {"pending"},
    "pending":  {"success", "failed"},
    "success":  {"reversed"},
    "failed":   set(),
    "reversed": set(),
}
```

Enforcement is in `write()`, not only in the action methods, because a warehouse that trusts
`state` has to be able to trust that nothing wrote a value straight into the column.
`create()` is guarded too: creating directly into anything past `draft` raises.
`test_every_illegal_transition_is_refused` walks all 5×5 pairs.

Two further invariants:

* `action_submit()` refuses a transaction with no `customer_ref` — you cannot ask a biller to settle
  a bill without saying whose bill it is.
* `amount`, `admin_fee` and `commission` are **frozen** once the state is terminal. A settled
  transaction's financial figures cannot be edited; the way to undo one is `reversed`.

For dbt: `accepted_values(state) = ['draft','pending','success','failed','reversed']`, and
`settled_at is not null` may be assumed **only** for `state in ('success','failed')` — a `reversed`
row keeps the `settled_at` of the settlement it reverses, and `draft`/`pending` rows have none.

---

## 4. The SLA clock

`sla_seconds` is a **stored** compute: whole seconds between `requested_at` and `settled_at`, `0`
while the transaction is still open. Stored because the warehouse needs a real Postgres column;
computed because it must never disagree with the timestamps it is derived from.

`sla_breached` is `sla_seconds > biller_id.sla_target_seconds`. Note it depends on the *current*
target: raising a biller's target retroactively un-breaches history. If the warehouse needs the
target as of the transaction date, snapshot `sla_target_seconds` into the fact at load time — this
module does not keep target history.

`settled_at < requested_at` is a `ValidationError`, so `sla_seconds` is never negative.

---

## 5. PDP

Classified in `custom_pdp_core`'s seed (the registry keys by model-name string, so it classifies
this model without depending on this module):

| column | class | at load |
|---|---|---|
| `customer_ref` | `sensitive` | HMAC digest — still supports repeat-customer counts |
| `customer_name` | `personal` | HMAC digest |
| `failure_reason` | `sensitive`, `drop_to_null` | `NULL` |
| everything else | `internal` / `public` | verbatim |

`ppob.transaction` inherits `pdp.masked.mixin`, so `customer_ref`, `customer_name` and
`failure_reason` are masked in the Odoo UI for users outside `PDP / Data Viewer`. `name` is on the
exclusion list — it is a system-generated sequence, not a person, and masking it would make the UI
unusable.

Why `customer_ref` is `sensitive` rather than `personal`: a subscriber or meter number identifies a
specific household and, joined to `amount` over time, discloses its consumption pattern. That is
closer to Art. 4(3) *data pribadi spesifik* than to a plain contact detail, and the cost of the
stricter class is nil — the transform is the same digest either way.

---

## 6. Groups and rules

| XML ID | Name | Meaning |
|---|---|---|
| `custom_ppob.group_ppob_user` | User | records and settles transactions; implies `base.group_user` |
| `custom_ppob.group_ppob_manager` | Administrator | manages billers, may reverse a settled transaction; implies User |

Record rules mirror `custom_operating_unit`: a PPOB user sees their own Operating Units plus
unit-less transactions; a holder of `custom_operating_unit.group_operating_unit_all` sees
everything; a global multi-company rule is AND-ed on top. Fail-closed for a user with no units.

Only a manager may press **Reverse**, and the button carries a confirmation — reversal is final.

---

## 7. Dependencies

`base, product, custom_pdp_core, custom_pdp_masking, custom_operating_unit`.

`custom_operating_unit` is a hard dependency: `models/ppob_transaction.py` imports
`operating_unit_field` from it so that the sixth occurrence of `operating_unit_id` cannot drift
from the other five. Install order is therefore
`custom_pdp_core → custom_pdp_masking → custom_operating_unit → custom_ppob`.
