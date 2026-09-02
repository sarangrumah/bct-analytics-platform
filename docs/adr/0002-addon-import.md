# ADR 0002 — Importing the `odoo-platform` addon suite

- **Status**: Accepted
- **Date**: 2026-09-01
- **Supersedes**: the addon clause of the 2026-08-31 deviation recorded in `docs/agents/PLAN.md`

## Context

`docs/agents/PLAN.md` records that on 2026-08-31 the operator overrode the master
prompt's "existing 162-addon platform" premise and chose greenfield with a five-addon
domain set. Two anti-patterns were kept in force by that decision, one of which reads:

> Anti-patterns 7.1 ("second Odoo compose stack") and 7.2 ("copying addons out")
> remain in force — there is exactly one Odoo stack in this repo and no addon is
> copied from anywhere.

On 2026-09-01 the operator reversed the addon half of that decision: the suite in
`github.com/sarangrumah/odoo-platform` — 154 modules, 239,321 lines, built largely to
close Odoo CE's gaps against Enterprise — is to be made ready to use in this
deployment. The operator further directed that the modules be inventoried and grouped
first, that duplicates be removed, and that customer-specific modules be renamed and
generalised for multi-tenant reuse.

This ADR records that reversal explicitly, so the repository does not tell two
contradictory stories. Anti-pattern 7.1 is untouched: there is still exactly one Odoo
stack here.

## Decision

**Anti-pattern 7.2 is withdrawn.** Addons may be imported from `odoo-platform`, under
the following constraints.

1. **Import in waves, ordered by dependency layer.** `custom_core` has 103 dependents
   and `custom_pdp_audit` 77; this is not a pick-and-choose menu. Wave order follows
   the platform's own `addons_path`: `_vendor → core → control_plane → compliance →
   ee_gap → operations → verticals → _tenants`.

2. **The classification seed is regenerated after every wave.** 38 of the 154 modules
   add 238 columns to tables this deployment replicates. `analytics/cdc/bct_cdc/policy.py`
   hard-fails on an unclassified column and never defaults one to `public`; that
   behaviour is deliberate and is not relaxed to accommodate the import. A wave is not
   complete until the loader runs clean.

3. **The five addons already here win every collision.** They are original work, not
   copies, and `custom_pdp_masking`'s `pdp_hmac_sha256` is pinned by a known-answer
   test shared with the CDC loader. Concretely:
   - Platform `custom_pdp_core` is imported as **`custom_pdp_taxonomy`**. It provides
     `pdp.classification` (a taxonomy of classification codes) and the
     `ir.model.fields.x_pdp_classification_id` tag, which 20 modules depend on. This
     repo's `custom_pdp_core` provides `pdp.field.classification`, the per-column
     policy the CDC loader reads. The two are complementary, and only the directory
     name collided.
   - Platform `custom_pdp_masking` is **not imported**. Nothing depends on it, and it
     defines `pdp.masked.mixin` a second time with different semantics — two
     definitions cannot coexist in one database. Its unique parts
     (`custom.pdp.field.registry`, the field-discovery wizard) may be ported later on
     top of this repo's mixin.
   - Platform's PPOB suite keeps its `custom.ppob.*` namespace and does not disturb
     this repo's `ppob.*` warehouse fixture.

4. **Customer identity does not enter the tree.** Modules carrying a customer's name
   are renamed on import and their customer-specific seed data separated from the
   engine, per the platform's own rule that a shared engine belongs in `ee_gap`/`core`
   and never in `_tenants`.

5. **Four modules are not imported at all**: `base_rest` (needs an OCA dependency that
   was never vendored; `installable=False`; unreferenced), `partner_firstname`
   (unreferenced, and the only AGPL-3 module in the suite),
   `custom_stock_delivery_report_fix` (retired by its own manifest — its xpath no
   longer matches Odoo 19 and it breaks fresh installs), and `custom_vertical_example`
   (a scaffold template that advertises itself as an installable application).

6. **Two imported modules are present but not installed here.** Both are recorded
   with their reason in `docs/module-catalog.md` section 12:
   - `custom_arka_aim_seed` — its own manifest restricts it to one tenant database,
     and its post-init hook changes the company currency, which cannot succeed on a
     database that already has journal entries.
   - `custom_storefront_api` — it redefines `res.partner.phone`, `street`, `street2`
     and `zip` as non-stored computed fields backed by encrypted columns. Logical
     replication then carries NULL for all four, silently, because a column that is
     no longer stored is no longer written. The warehouse already protects those
     values: the policy classifies them `personal`, so they are HMAC-digested during
     load and never land as plaintext. Double-encrypting at the ORM layer bought
     nothing here and cost the analytics path its data. Zero modules depend on it.

   The general rule this second case establishes: **an addon that turns a stored
   field into a computed one silently breaks replication of that column.** Nothing
   raises. `tests/test_01_live_sync.py` catches it only because it asserts the
   landed digest equals the source value, rather than asserting a row arrived.

## Consequences

- `addons_path` gains the eight group directories. The five existing modules stay at
  the root of `addons/`, so no existing path moves.
- `server_wide_modules` gains `queue_job`: nine modules need the runner loaded at
  server level, not merely installed.
- `addons/` still has no delivery path outside the dev overlay. Until one exists, the
  imported suite runs in development only. This is tracked separately and is a
  prerequisite for any production use.
- CI has no addon install gate today. One is added with the import; without it, 154
  modules land with nothing verifying they install.
- The suite is LGPL-3 throughout, matching this repository. The one AGPL-3 module is
  among those not imported, so no licence question follows the code in.

## Alternatives considered

**Remediate in the platform repo first, import clean.** Rejected by the operator on
2026-09-01 in favour of importing first and remediating here, accepting that modules
land before their duplicate fields and missing search views are fixed.

**Import everything in one lift.** Rejected: it stops the CDC loader on the first
unclassified column, and 38 modules introduce such columns.
