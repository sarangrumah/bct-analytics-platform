# custom_pdp_masking — module knowledge

Implements the masking half of **frozen contract 01**.
Consumers: the Phase 3 CDC loader (must reproduce §2 byte-for-byte), dbt, Security at every gate.

---

## 1. The policy table — `pdp.masking.rule`

Five rows, one per PDP class. Loaded with `noupdate="0"`, so `-u custom_pdp_masking` restores them
if someone edits them by hand: the contract is authoritative, not the database.

| `pdp_class` | `transform` | `ui_masked` | Meaning at load |
|---|---|---|---|
| `public` | `none` | no | copied verbatim |
| `internal` | `none` | no | copied verbatim |
| `personal` | `hmac_sha256` | yes | deterministic digest, joins preserved |
| `sensitive` | `hmac_sha256_or_null` | yes | digest, **except** `drop_to_null` columns → `NULL` |
| `secret` | `drop` | no | never named in the extraction `SELECT` |

`get_masking_plan(model_names=None)` resolves this table against `pdp.field.classification` and
returns the per-column plan the loader executes:

```json
{"res.partner": {
   "email":   {"pdp_class": "personal",  "transform": "hmac_sha256"},
   "comment": {"pdp_class": "sensitive", "transform": "null"},
   "id":      {"pdp_class": "internal",  "transform": "none"}}}
```

`secret` columns are **omitted from the plan entirely** rather than given a `drop` transform — the
loader must not be able to name them at all.

---

## 2. The HMAC construction — the cross-language contract

Reference implementation: `models/pdp_hash.py::pdp_hmac_sha256`.
Machine-readable form: `pdp.masking.rule.get_digest_spec()` (call it at loader startup and assert).

Every degree of freedom, pinned:

| # | Decision | Value |
|---|---|---|
| 1 | Primitive | **HMAC** (RFC 2104), not a plain hash and not a salt-concatenation |
| 2 | Digest | **SHA-256** |
| 3 | Salt position | the salt is the HMAC **key**; the value is the **message**. `HMAC(key=salt, msg=value)` |
| 4 | Key encoding | UTF-8 bytes of the salt string |
| 5 | Message encoding | UTF-8 bytes of the value string |
| 6 | Normalisation | **none** — no trim, no case fold, no Unicode NFC/NFD normalisation |
| 7 | Output | `hexdigest()` — exactly 64 characters, **lowercase** `[0-9a-f]` |
| 8 | `NULL` input | returns `NULL`. NULL is preserved, never hashed to a constant |
| 9 | `""` input | returns `NULL`. Hashing the empty string would give every empty cell one shared non-NULL digest, i.e. a fabricated join key |
| 10 | Non-`str` input | **`TypeError`**. There is no cross-language-safe implicit conversion (`str(1.0)` is `'1.0'` in Python and something else elsewhere). No numeric column is classified `personal`; the personal-ish numerics (coordinates) carry `drop_to_null` |
| 11 | Empty/absent salt | **`ValueError`** / `UserError`. Never degrade to an unkeyed hash |

Canonical implementation, copy this into the loader verbatim:

```python
import hashlib, hmac

def pdp_hmac_sha256(value: str | None, salt: str) -> str | None:
    if not isinstance(salt, str):
        raise TypeError("PDP salt must be a str")
    if not salt:
        raise ValueError("PDP salt is empty")
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("PDP digest input must be str or None")
    if value == "":
        return None
    return hmac.new(salt.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()
```

### Known-answer vectors

Asserted by `tests/test_pdp_masking.py::TestPdpHash::test_known_answer_vectors`. The loader's own
test suite must assert the same four. If any of them changes, every digest already in the warehouse
is invalid and the change is a **migration**, not a bug fix.

| value | salt | digest |
|---|---|---|
| `budi.santoso@contoh.invalid` | `bct-demo-salt` | `57890775652c2e05536c54638d280c1f2cde752d0fc52bf42ac3a76d53ddbd5e` |
| `budi.santoso@contoh.invalid` | `other-tenant-salt` | `c24c6fc738f518543fe3b5cfb2e8e0bafd0464371333e91188552317f9b4f738` |
| `Budi Santoso` | `bct-demo-salt` | `a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b` |
| `Ir. Sri Wahyuni, S.T.` | `bct-demo-salt` | `9a5f1b855c3e59c66e701fb93f6411627790d573b9d48cda9c7b74cf1a1e6b3b` |

Rows 1 and 2 are the cross-tenant separation property. `bct-demo-salt` and `other-tenant-salt` are
test fixtures; the real salt never appears in a tracked file.

Negative vectors also asserted: `sha256(salt + value)` must **not** equal the digest (guards a
loader that reimplements this as concatenation); ` Budi Santoso ` and `budi santoso` must both
differ from `Budi Santoso` (guards a loader that trims or lowercases).

### Salt resolution

First hit wins:

1. `WAREHOUSE_MASK_SALT_<TENANT>` in the environment. `<TENANT>` is the **database name**,
   upper-cased, with every non-alphanumeric character replaced by `_`. Database `erp_dev` →
   `WAREHOUSE_MASK_SALT_ERP_DEV`.
2. `WAREHOUSE_MASK_SALT_DEFAULT` in the environment.
3. `ir.config_parameter` `pdp.mask_salt` — for tests and single-tenant dev boxes.

Nothing found → `UserError`. Per contract 01 the salt lives in SOPS, is `changeme` in
`.env.example`, and is never committed. **Rotating a salt invalidates every historical join built
on its digests — treat it as a warehouse migration.**

---

## 3. In-Odoo enforcement — `pdp.masked.mixin`

A dashboard that hides a customer's name is worth nothing if the same user can open the record in
Odoo. The mixin closes that.

**Mechanism.** It overrides `read()`. In Odoo 19 the web client funnels everything through it:
`web_read()` calls `self.read(fields, load=None)` and `web_search_read()` goes through `web_read()`
(verified in `odoo/addons/web/models/models.py`). So list views, form views, and the `display_name`
of a many2one pointing at a masked model are all covered.

**What it deliberately does not touch.** Internal ORM paths — `record.email`, `mapped()`, `search()`
domains, `_compute` methods — read through the cache, not `read()`. Business logic keeps operating
on cleartext, and a non-viewer can still *search* for a customer by e-mail. This is a presentation
control, not an access control; the access control is the CDC loader never receiving the cleartext
in the first place.

**Bypass.** `self.env.su` returns raw — module installation, data loading, cron and the demo seeder
run as superuser and masking them would corrupt data rather than protect it. Otherwise the check is
`user.has_group('custom_pdp_core.group_pdp_data_viewer')`.

**What a non-viewer sees.**

| classification | rendered as |
|---|---|
| `personal`, or `sensitive` without `drop_to_null` | `***` + 8 hex characters |
| `sensitive` with `drop_to_null` | `*** redacted (PDP) ***` |

Only `char`/`text`/`html` columns are masked; masking a boolean or a foreign key would break the
client without protecting anything the text columns do not already protect.

**The UI token is not the warehouse digest.** It is keyed on `ir.config_parameter database.uuid`,
not the tenant salt, for two reasons: the warehouse salt must never appear in an HTTP response, and
a UI token must never be mistaken for a warehouse digest and used as a join key. It is stable within
a database (two reads of the same value give the same token, so lists stay navigable and two
partners stay distinguishable) and worthless outside it. `test_ui_token_is_not_the_warehouse_digest`
asserts the two are different.

### The export funnel

`read()` is not the only way values leave Odoo, and the second path was a real hole, found by
Security and confirmed independently by the Lead and by me before fixing:

    read()        -> {'name': '***8a2b1f58', 'email': '***49f22484'}
    export_data() -> [['Budi Santoso (Demo 001)', 'budi.santoso.001@contoh.invalid', ...]]

`export_data()` calls `_export_rows()`, which reads each value with `record[name]` -
`__getitem__` straight out of the ORM cache (`odoo/orm/models.py:806`). The public `read()` is never
called, so a user without **PDP / Data Viewer** holding the ordinary `base.group_allow_export`
right could export cleartext to CSV/XLSX. An export is a bulk copy of personal data leaving the
system, which is the event UU 27/2022 is most concerned with.

`models/pdp_export.py` closes it, and it extends **`base`**, not the mixin. That is deliberate: an
export path may *reach* personal data on another model - exporting `sale.order` with the column
`partner_id/email` returns a partner's e-mail while never calling anything on `res.partner`. Each
column is resolved through its `a/b/c` path to the model and field it terminates on, then looked up
in that model's mask plan. `id` / `.id` columns are never masked.

**The export surface is therefore masked more broadly than the UI read surface.**
`sale.order.note` is `sensitive`, so it is blanked in an export while the form view still shows it.
Asymmetric on purpose: a value on screen is read by one person; a value in a spreadsheet is a copy
that outlives the session. If column resolution ever raises, every column is blanked rather than
exported - a masking bug must not become a data leak.

### Models covered

| model | covered | note |
|---|---|---|
| `res.partner` | yes | `ref` excluded — it is the code operators type into the search box |
| `ppob.transaction` | yes (declared in `custom_ppob`) | `name` excluded — a system sequence, not a person |
| `res.users` | **no** | see below |

(The `read()` masking scope below applies to the UI; the export masking above applies everywhere.)

**`res.users` is deliberately exempt from UI masking.** `login` is classified `personal` and *is*
masked in the warehouse, but the Odoo administration screens, the login form and the session widgets
are built around it; masking it turns user administration into guesswork while protecting nothing
extra — the user's name, e-mail, phone and address are `res.partner` columns reached through
`partner_id`, and those *are* masked. This is a recorded scope decision, not an oversight.

To extend coverage to another model:

```python
class MyModel(models.Model):
    _name = "my.model"
    _inherit = ["my.model", "pdp.masked.mixin"]
    _pdp_ui_mask_exclude = ("some_operational_code",)
```

---

## 4. Where masking is *not* applied

* **dbt does not mask.** By the time a dbt model runs, the data is already masked. Contract 01,
  §"Masking applied during load".
* **`semantic-api` does not mask and cannot.** Contract 03 rule 4.
* **This module does not mask the warehouse.** It defines the transform and enforces it inside
  Odoo. The loader applies it. Two implementations of one specification is exactly why §2 is
  written the way it is.

---

## 5. Test suite

`odoo -d <db> -u custom_pdp_masking --test-enable` runs 22 tests:

* `TestPdpHash` — the four known-answer vectors, output shape, determinism within a tenant,
  separation across tenants, NULL/empty handling, the two negative vectors, `TypeError` on non-text,
  `ValueError` on empty salt.
* `TestPdpMaskingRule` — the transform table equals contract 01 exactly; one rule per class;
  salt resolution; `UserError` when no salt is configured; the published digest spec; per-column
  plan resolution including `secret` omission.
* `TestPdpUiMasking` — a non-viewer sees masked values; tokens are stable and distinct;
  `display_name` is masked too; the UI token differs from the warehouse digest; a Data Viewer sees
  cleartext; ORM attribute access and `search()` are unaffected.
* `TestPdpExportMasking` — a non-viewer's export of `res.partner` contains **no cleartext e-mail**;
  free text is blanked; an excluded column (`ref`) still exports; a Data Viewer's export is
  cleartext; export and `read()` agree on the same record; a relational path
  (`sale.order` → `partner_id/email`) is masked; `id` columns are not.

### What this control is not

It is a **UI-and-RPC-surface** control. It does not, and cannot, stop:

* a Settings administrator, who can grant themselves `group_pdp_data_viewer`;
* server-side code calling `sudo()` or running as `env.su`;
* anyone with direct database or filestore access;
* `ir_attachment` contents, which are not classified at all (see `custom_pdp_core`
  MODULE_KNOWLEDGE.md §7).

Stating the boundary is what makes the control trustworthy. The control that actually keeps personal
data out of the warehouse is the CDC loader never selecting it — contract 01, applied at load.
