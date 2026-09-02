---
status: draft
generated_at: 2026-07-22T02:30:00Z
generator: claude-code-bootstrap-v1
module: custom_wms_inbound_qc
manifest_version: 19.0.0.1.0
---

# custom_wms_inbound_qc

## Purpose
Makes inbound quarantine real. Plain Odoo 19 CE will happily reserve stock that
has only just landed on the dock, so a two-step reception is a *routing* device,
not a *hold*. This module turns the inbound area into an actual gate: quants in
a flagged location are invisible to outbound reservation, a receipt cannot be
released until an inspector passes it, and a barcode nobody recognises becomes a
reviewable registration record instead of a blocked receiving operator.

## Business Flow
- Admin flags the inbound / QC location (`wms_is_qc_area`, which implies
  `wms_block_reservation`). The block is inherited by every child bin, so
  flagging `WH/Input` quarantines the whole area.
- Admin sets `wms_qc_required` (and optionally `wms_qc_location_id`) on the
  incoming `stock.picking.type`.
- A receipt of that type is created with `wms_qc_state = 'pending'` and its
  destination forced to the QC area.
- Goods are received. They are physically on hand but `_get_available_quantity`
  returns 0 for them, so no delivery, MO or transfer can reserve them.
- An inspector (`group_wms_qc_inspector`) calls `action_wms_qc_pass`. The module
  adopts the route's existing `Input -> Stock` transfer when a multi-step
  reception already created one, otherwise it builds one; either way the
  transfer is stamped `wms_qc_release_ok` and reserved with the bypass context.
  `custom_wms_putaway` then slots each released line into a real bin.
- `action_wms_qc_fail` leaves the goods quarantined, records who failed it, and
  opens a `quality.alert` when the quality module happens to be installed.
- A scan that matches no product goes to `custom.wms.product.registration.capture`,
  which accumulates re-scans on one open row; approval creates the product.

## Key Models
- `stock.location` (inherited) — `wms_is_qc_area`, `wms_block_reservation`, plus
  the cached `_wms_blocked_location_ids()` resolver.
- `stock.quant` (inherited) — `_get_gather_domain` override; the single choke
  point for the whole reservation stack.
- `stock.picking.type` (inherited) — `wms_qc_required`, `wms_qc_location_id`.
- `stock.picking` (inherited) — QC state machine, release-transfer builder, and
  an outbound guard on `button_validate`.
- `stock.move` (inherited) — `_action_assign` split so only an authorised
  release transfer may reserve out of quarantine.
- `stock.move.line` (inherited) — redefines `_is_incoming()` for the putaway
  engine.
- `custom.wms.product.registration` — unknown-item capture and approval.

## Important Fields
- `stock.location.wms_block_reservation` (Boolean, indexed) — the actual switch.
- `stock.location.wms_is_qc_area` (Boolean) — intent flag; its onchange sets the
  switch above.
- `stock.picking.wms_qc_state` (Selection not_required/pending/passed/failed).
- `stock.picking.wms_qc_release_ok` (Boolean) — marks the ONE internal transfer
  allowed to draw from quarantine.
- `stock.picking.wms_qc_release_picking_id` (M2o) — the adopted or created leg.
- `custom.wms.product.registration.state` (draft/submitted/approved/rejected).
- `custom.wms.product.registration.barcode` (Char, indexed, required).

## Public Methods
- `stock.location._wms_blocked_location_ids()` (`@api.model`) — cached id list of
  every quarantined bin including children.
- `stock.quant._get_gather_domain(...)` — appends `location_id not in blocked`
  unless the context carries `wms_allow_blocked_locations`.
- `stock.picking.action_wms_qc_pass()` / `action_wms_qc_fail()` — the gate.
- `stock.picking._wms_existing_release_transfer()` — finds the route's own
  chained internal picking so a two-step reception is not doubled.
- `stock.picking._wms_create_release_transfer()` — adopt-or-build.
- `custom.wms.product.registration.capture(barcode, picking, description, quantity)`
  (`@api.model`) — idempotent unknown-barcode capture.
- `custom.wms.product.registration.action_submit/action_approve/action_reject`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_wms_putaway`,
  `stock`, `product`, `mail`.
- **Inherits from:** `stock.location`, `stock.quant`, `stock.picking`,
  `stock.picking.type`, `stock.move`, `stock.move.line`; the registration model
  inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- **Optional peer:** `quality.alert` is looked up via `env.get(...)` — the
  quality module is never a hard dependency.
- **External calls:** none.

## Gotchas
- **The bypass context is load-bearing.** `wms_allow_blocked_locations` lifts the
  filter completely. Anything that sets it can see and reserve quarantined
  stock; only `_wms_create_release_transfer` and the `_action_assign` split are
  supposed to.
- **`wms_qc_release_ok` is what makes the gate real.** Without it, the second leg
  of a two-step reception reserves the goods the instant they land, because that
  move genuinely is "internal, quarantine -> stock". Do not relax that check.
- **A QC-pending receipt is deliberately NOT a putaway event.** `_is_incoming()`
  returns False while `wms_qc_state == 'pending'`; otherwise the putaway engine
  auto-applies a storage bin as the move-line destination and the goods bypass
  quarantine entirely. This was a live bug, caught by smoke test, and
  `test_pending_receipt_is_not_a_putaway_event` guards it.
- **`_wms_blocked_location_ids` is cached on the cursor** (`env.cr.cache`), which
  Postgres clears on commit/rollback. Writes to the flags, the parent link or
  `active` invalidate it explicitly; a raw SQL update would not.
- **`action_wms_qc_pass` requires a *validated* receipt.** Before validation there
  is nothing in quarantine to release, and the release transfer would be empty.
- **`__system__` (uid 1) is not `base.user_admin` (uid 2)**, so the group granted
  in `security.xml` does not apply to shell scripts or tests — grant
  `group_wms_qc_manager` explicitly there.
- **The outbound guard on `button_validate` is a backstop**, not the primary
  control. Reservation should already have made those move lines impossible; the
  check catches force-assigned or manually written lines.

## Out of Scope
- Sampling plans, AQL tables, measurement capture — that is `custom_quality_full`
  territory; this module only opens an alert when it is installed.
- Partial pass / partial reject of one receipt: QC is all-or-nothing per picking.
- Vendor scorecards or rejection-to-supplier returns (`custom_po_return` covers
  the return leg).
- Blocking *internal* consumption of quarantined stock — only reservation is
  filtered; an inventory adjustment can still touch the quants.
