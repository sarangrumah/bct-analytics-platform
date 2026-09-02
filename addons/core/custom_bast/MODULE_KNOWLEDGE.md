---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_bast
manifest_version: 19.0.0.2.0
---

# custom_bast

## Purpose
Reusable abstraction for **Berita Acara Serah Terima** (Indonesian-style handover documents). Provides a generic two-party handover record with optional reference to any source document (sale order, transfer, rental order, work order, etc.), dual signatures with timestamps + signer names, optional GPS coordinates, optional witness, itemized lines with per-line condition + photo + optional product/lot, an audit-logged state machine, and a QWeb PDF report. Designed to be the canonical BAST building block for rental, field service, delivery, manufacturing, and any vertical that needs signed handover artifacts.

## Business Flow
- A user creates a `custom.bast.document` (state `draft`), picks `kind` (pickup/return/delivery/installation/handover), parties (`party_from_id`, `party_to_id`), optional `reference` (Reference field to `stock.picking`/`sale.order`/`purchase.order`/`fsm.work.order`/`rental.order` — filtered by `_get_referenceable_models()` to models present in env). Sequence `custom.bast.document` assigns `name`.
- User adds `custom.bast.line` rows: item_description, qty, uom, optional product/lot, `condition` (good/damaged/partial), optional photo + note.
- `action_open_sign_wizard()` opens `custom.bast.sign.wizard` — user picks `party` (from/to), draws/uploads `signature` (Binary), optionally fills `signed_by` + `gps_latitude`/`gps_longitude`. Wizard calls `action_sign_from()` or `action_sign_to()` on the document.
- `action_sign_from`/`action_sign_to` raise `UserError` if state ∈ {completed, voided}, else write signature + `_signed_at = now()` + `_signed_by = signed_by or env.user.name`, optionally GPS, then call `_recompute_state()` which transitions: both signed → `completed`; one signed → `signed_one_side`; none → `draft`.
- `action_void(reason)` writes state=`voided`; from `completed` requires group `custom_bast.group_bast_manager`. Posts `Voided: <reason>` to chatter if reason given.
- `_check_parties_distinct` constraint blocks `party_from_id == party_to_id`.
- QWeb PDF report renders the BAST as the printable artifact.

## Key Models
- `custom.bast.document` — The handover record. Inherits `pdp.audited.mixin`, `mail.thread`, `mail.activity.mixin`. Holds parties, kind, location (M2o stock.location OR free-text), dual signatures, state, optional GPS, optional witness, optional source reference.
- `custom.bast.line` — Itemized line per BAST; condition + photo + optional product/lot.
- `custom.bast.sign.wizard` — TransientModel; signature capture UI dispatching to `action_sign_from`/`action_sign_to`.

## Important Fields
- `custom.bast.document.name` (Char, unique, sequence-driven default `"New"`) — BAST number from `custom.bast.document` ir.sequence.
- `custom.bast.document.kind` (Selection pickup/return/delivery/installation/handover, default handover, tracking) — drives report headers and downstream filtering.
- `custom.bast.document.reference` (Reference, dynamic via `_selection_reference_models`) — link to source business document. Selection list auto-filtered to models present in env (so installing without `rental` doesn't break it).
- `custom.bast.document.state` (Selection draft/signed_one_side/completed/voided, indexed, tracking) — computed by `_recompute_state` from signature presence; only manual transition is `action_void`.
- `custom.bast.document.party_from_id` / `party_to_id` (M2o res.partner, required, tracking) — `_check_parties_distinct` enforces ≠.
- `custom.bast.document.party_from_signature` / `party_to_signature` (Binary, attachment=True) — actual signature image.
- `custom.bast.document.party_from_signed_at` / `party_to_signed_at` (Datetime, readonly) — stamped automatically by sign actions.
- `custom.bast.document.party_from_signed_by` / `party_to_signed_by` (Char) — captured name string; defaults to `env.user.name` if not supplied.
- `custom.bast.document.witness_id` (M2o res.users) — optional internal witness.
- `custom.bast.document.gps_latitude` / `gps_longitude` (Float, 10,7) — captured by mobile signer.
- `custom.bast.document.date_handover` (Datetime, required, default now, tracking) — official handover moment.
- `custom.bast.document.location_id` (M2o stock.location) + `location_text` (Char) — structured OR free-text location.
- `custom.bast.line.condition` (Selection good/damaged/partial, default good) — per-line condition assessment.
- `custom.bast.line.photo` (Binary, attachment=True) — per-line evidence photo.
- `custom.bast.line.lot_id` (M2o stock.lot) — optional serial/lot binding.

## Public Methods
- `custom.bast.document.action_sign_from(signature, signed_by=None, gps=None)` — write `from` signature + recompute state.
- `custom.bast.document.action_sign_to(signature, signed_by=None, gps=None)` — write `to` signature + recompute state.
- `custom.bast.document.action_void(reason=None)` — transition to voided; manager-group-gated when current state is completed.
- `custom.bast.document.action_open_reference()` — open the linked source document.
- `custom.bast.document.action_open_sign_wizard()` — open the sign wizard.
- `custom.bast.document._recompute_state()` — internal: drives `state` from `(has_from, has_to)`.
- `custom.bast.document._get_referenceable_models()` (`@api.model`) — extension hook; subclasses can append more source-document types.
- `custom.bast.sign.wizard.action_apply()` — dispatch to `action_sign_from`/`action_sign_to`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mail`.
- **Inherits from:** `pdp.audited.mixin`, `mail.thread`, `mail.activity.mixin`.
- **Extended by:** `custom_rental` (links via `bast_pickup_id` / `bast_return_id`), `custom_hht_bridge` (handover scans), and any vertical needing a signed handover artifact.
- **External calls:** none.
- **Cross-vertical:** generic.

## Gotchas
- **`party_from_signed_at` / `party_to_signed_at` are stamped server-side** as `Datetime.now()` — UTC by default; client-side wall clock is not honored.
- **`signature` Binary on the wizard has `attachment=False`** while the document fields have `attachment=True` — wizard captures raw inline base64 then writes to attachment-backed field. No size limit enforced.
- **`_get_referenceable_models()` static list** must be extended via Python override; XML data-only extensions cannot add to the Reference selection.
- **`_recompute_state` does not unset `completed`** — if you clear a signature after both were set, state stays `completed` until you manually `action_void`. (The state computation is only invoked by sign actions, not as a `@api.depends` compute.)
- **`action_void` from completed needs `custom_bast.group_bast_manager`** — regular users cannot void a fully signed BAST. From any other state any user with write access can void.
- **`_check_parties_distinct` allows null on either side temporarily** during create — but both are `required=True`, so this only matters during transient wizard flows.
- **No `kind`-specific workflow** — a `pickup` and a `delivery` BAST follow exactly the same state machine. Kind is metadata only.
- **GPS fields are Float(10,7)** — about ±1 cm precision; no validation that they're set together or in valid lat/lon ranges.
- **Sequence `custom.bast.document`** must be loaded from `data/ir_sequence_data.xml`; if missing, `name` stays as the literal `"New"` and the unique constraint on `name` will collide on the second create.
- **This module's groups sit on its own `custom_bast.res_groups_privilege_bast` privilege** ("BAST") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Automatic BAST creation** — downstream modules (e.g. rental) must call `create()` themselves; this module does not subscribe to events on referenced docs.
- **Multi-party (>2) handovers** — exactly two parties.
- **E-signature legal compliance (UETA, eIDAS)** — captures signature image + timestamp + signer name; no certificate-chain trust, no tamper-evident hash chain.
- **PDF e-stamp / digital seal** — printable PDF only; no `materai`/QR/digital cert integration.
- **OCR / verification of the photo evidence** — `condition` is set manually.
