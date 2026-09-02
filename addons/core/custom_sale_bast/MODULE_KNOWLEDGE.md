---
status: draft
generated_at: 2026-06-02T00:00:00Z
generator: claude-code-hand-authored-v1
module: custom_sale_bast
manifest_version: 19.0.0.1.0
---

# custom_sale_bast

## Purpose
Thin bridge between standard Sales and `custom_bast`. Lets users generate and open a BAST (Berita Acara Serah Terima — handover document) directly from a Sales Order: a **BAST** smart button shows the count of linked handover documents, and a **Generate BAST** header button creates a `delivery` BAST pre-filled from the order. Carries no models of its own beyond a `sale.order` inheritance; all BAST data lives in `custom_bast`.

## Business Flow
- On a Sales Order form, `bast_count` (computed) renders a smart button counting `custom.bast.document` rows whose `reference` equals `"sale.order,<id>"` (`_bast_reference()`).
- **Generate BAST** (`action_generate_bast`): validates `custom_bast` is installed and a customer is set, then `sudo`-creates a `custom.bast.document` with `kind="delivery"`, `party_from_id = company partner` (hands the goods over), `party_to_id = customer`, `company_id`, `reference = "sale.order,<id>"`, and one BAST line per **real** order line (sections / notes — `display_type` set — are skipped). The document `name` is assigned by `custom_bast`'s ir.sequence, not set here. Returns a form action opening the new document.
- **View BAST** (`action_view_bast`): opens the `list,form` of `custom.bast.document` filtered by `_bast_domain()` (`reference = "sale.order,<id>"`), with `default_*` context so a doc created from that view is pre-linked back to the order.

## Key Models
- `sale.order` (inherited) — adds the `bast_count` computed field and the `action_generate_bast` / `action_view_bast` buttons plus helpers. No new model is defined.

## Important Fields
- `sale.order.bast_count` (Integer, compute=`_compute_bast_count`, non-stored) — count of linked `custom.bast.document` records; `0` for unsaved orders.

## Public Methods
- `sale.order.action_generate_bast()` — create a `delivery` BAST from the order and open it; raises `UserError` if `custom_bast` is absent or no customer is set.
- `sale.order.action_view_bast()` — open the list/form of BAST docs linked to this order.
- `sale.order._bast_reference()` — canonical link string `"sale.order,<id>"` stored on `custom.bast.document.reference`.
- `sale.order._bast_domain()` — search domain matching this order's BAST docs.
- `sale.order._compute_bast_count()` — populates `bast_count` via `search_count`.
- `sale.order._bast_lines_vals()` — builds `line_ids` create-tuples (one per non-`display_type` order line; maps `item_description`/`product_id`/`qty`/`uom_id`).
- `sale.order._ensure_bast_module()` — guard raising `UserError` when `custom.bast.document` is not in the registry.

## Integration Points
- **Depends on:** `sale`, `custom_bast`.
- **Inherits from:** `sale.order`.
- **External calls:** none.
- **Model access:** owned entirely by `custom_bast` — users need the *Custom BAST / User* group to open generated documents; this module declares no `ir.model.access` of its own.
- **capability_tags:** `audit-trail`, `sales`.
- **auto_install:** `False` — explicit install.

## Gotchas
- **Link is by string `reference`, not a relational field** — `"sale.order,%d" % id`. Mirrors `custom_rental`'s convention; any consumer matching BAST docs to orders must use the same exact format.
- **Document `name` is NOT set here** — it relies on `custom_bast` having a real `custom.bast.document` ir.sequence (added alongside this module); without it the create would fail / mis-name.
- **Creation runs under `sudo()`** — access control is delegated to `custom_bast`'s groups; this bridge does not re-check write rights before creating.
- **Sections / notes are skipped** — only order lines without `display_type` become BAST lines; a BAST generated from an order that is all sections would have zero lines.
- **`bast_count` is non-stored** — it re-queries on every form load and is not searchable/groupable.

## Out of Scope
- The BAST document model, its sequence, lifecycle, signing, and PDF — all owned by `custom_bast`.
- Auto-generating a BAST on order confirmation/delivery — generation is a manual button only.
- Linking BAST back to the delivery `stock.picking` — the reference points at the `sale.order`, not the picking.
