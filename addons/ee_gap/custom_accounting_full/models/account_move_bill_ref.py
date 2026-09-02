# -*- coding: utf-8 -*-
"""Hard-block duplicate vendor bill references.

Odoo CE only *warns* about a re-used Bill Reference (via the native
``duplicated_ref_ids`` banner) — the bill still saves and posts. Group policy
here requires vendor bill references to be unique per vendor, so we block a
duplicate on **save** (``@api.constrains``, drafts included) and, as a second
line of defence, on **confirm** (``UserError`` inside ``_post``).

The match rule mirrors the native detection: same reference + same
commercial partner + same company + same vendor-document type, ignoring
the record itself and cancelled moves. Empty references are never treated
as duplicates. The check runs on confirm (not while editing the draft),
so users can freely fix the reference before posting.
"""

from __future__ import annotations

from odoo import _, api, models
from odoo.exceptions import UserError, ValidationError

VENDOR_BILL_TYPES = ("in_invoice", "in_refund")


class AccountMoveBillRef(models.Model):
    _inherit = "account.move"

    @api.constrains("ref", "partner_id", "move_type", "company_id", "state")
    def _check_unique_bill_ref(self):
        """Block *saving* a vendor bill whose Bill Reference is already used by
        the same vendor (draft included). Stricter than the confirm-time guard:
        the duplicate cannot even be stored. A cancelled move is exempt (it may
        legitimately keep the old reference)."""
        for move in self:
            if move.state == "cancel":
                continue
            duplicates = move._custom_find_duplicate_bill_ref()
            if duplicates:
                raise ValidationError(
                    _(
                        'Bill Reference "%(ref)s" is already used by %(dup)s for '
                        "vendor %(vendor)s. The reference must be unique — change "
                        "it before saving.",
                        ref=move.ref,
                        dup=", ".join(duplicates.mapped("display_name")),
                        vendor=move.commercial_partner_id.display_name,
                    )
                )

    def _custom_find_duplicate_bill_ref(self):
        """Return other non-cancelled vendor bills sharing this Bill Reference."""
        self.ensure_one()
        if self.move_type not in VENDOR_BILL_TYPES:
            return self.browse()
        ref = (self.ref or "").strip()
        if not ref or not self.commercial_partner_id:
            return self.browse()
        # sudo(): a duplicate may live in a record the current user cannot
        # see (multi-company / partner rules) — uniqueness must still hold.
        return self.sudo().search(
            [
                ("id", "!=", self.id),
                ("move_type", "=", self.move_type),
                ("company_id", "=", self.company_id.id),
                ("commercial_partner_id", "=", self.commercial_partner_id.id),
                ("ref", "=", ref),
                ("state", "!=", "cancel"),
            ]
        )

    def _post(self, soft=True):
        for move in self:
            duplicates = move._custom_find_duplicate_bill_ref()
            if duplicates:
                raise UserError(
                    _(
                        'Bill Reference "%(ref)s" is already used by %(dup)s for '
                        "vendor %(vendor)s. The reference must be unique — change "
                        "it or cancel the existing bill before posting.",
                        ref=move.ref,
                        # display_name (not name): a still-draft duplicate has
                        # name=False, which would crash str.join.
                        dup=", ".join(duplicates.mapped("display_name")),
                        vendor=move.commercial_partner_id.display_name,
                    )
                )
        return super()._post(soft=soft)
