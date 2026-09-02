# -*- coding: utf-8 -*-
"""custom.wms.putaway.rule — the ``sap_storage_search`` kind.

The kind is added with ``selection_add`` so ``custom_wms_putaway`` stays
untouched; it is a shared addon mounted across every tenant database, and a
field added there would force a restart plus ``-u`` everywhere.

``ondelete="set default"`` is required by Odoo for an added selection value.
The consequence is worth knowing: uninstalling this module silently rewrites any
SAP rule to ``fixed_location``, which disarms the strategy rather than deleting
it. Uninstall is therefore a config change, not a no-op.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class WmsPutawayRule(models.Model):
    _inherit = "custom.wms.putaway.rule"

    kind = fields.Selection(
        selection_add=[("sap_storage_search", "SAP 2D Storage Search")],
        ondelete={"sap_storage_search": "set default"},
    )

    sap_search_order = fields.Selection(
        [
            ("type_first", "Storage Type outer, Section inner"),
            ("section_first", "Storage Section outer, Type inner"),
        ],
        string="Search Order",
        default="type_first",
        help="SAP determines the storage type first and searches sections "
        "within it, which is the 'type_first' default. Reverse it only for a "
        "warehouse that would rather keep a sport together than keep goods on "
        "the right kind of shelf.",
    )
    sap_type_penalty = fields.Integer(
        string="Storage Type Penalty",
        default=12,
        help="Score cost of each fallback step down the storage type sequence. "
        "With the default of 12 and a section penalty of 1, staying in the "
        "correct storage type always scores at least 91 -- above the standard "
        "auto-apply threshold of 90 -- while any type fallback drops to 87 or "
        "below and so surfaces a suggestion for operator review.",
    )
    sap_section_penalty = fields.Integer(
        string="Storage Section Penalty",
        default=1,
        help="Score cost of each fallback step down the storage section sequence.",
    )
    sap_default_type_id = fields.Many2one(
        "custom.wms.storage.type",
        string="Default Storage Type",
        help="Used when the product carries no storage type of its own.",
    )
    sap_default_section_id = fields.Many2one(
        "custom.wms.storage.section",
        string="Default Storage Section",
        help="Used when the product carries no storage section of its own.",
    )
    sap_fail_action = fields.Selection(
        [
            ("none", "No proposal (fall through to the next tier)"),
            ("overflow", "Propose the overflow location"),
        ],
        string="When Search Is Exhausted",
        default="overflow",
    )
    sap_overflow_location_id = fields.Many2one(
        "stock.location",
        string="Overflow Location",
        check_company=True,
        help="Where goods go when every bin in both search sequences is full. "
        "Proposed at a deliberately low score so it always reaches an operator.",
    )
    sap_consolidate = fields.Boolean(
        string="Consolidate Same Product",
        default=True,
        help="Prefer a bin that already holds this product over an empty one of "
        "equal fit, so a SKU stays in as few bins as possible.",
    )

    @api.constrains("sap_type_penalty", "sap_section_penalty")
    def _check_sap_penalties(self):
        for rec in self:
            if rec.kind != "sap_storage_search":
                continue
            if rec.sap_type_penalty < 0 or rec.sap_section_penalty < 0:
                raise ValidationError(_("Storage search penalties cannot be negative."))

    @api.constrains("kind", "sap_fail_action", "sap_overflow_location_id")
    def _check_sap_overflow(self):
        for rec in self:
            if rec.kind != "sap_storage_search":
                continue
            if rec.sap_fail_action == "overflow" and not rec.sap_overflow_location_id:
                raise ValidationError(_("Rule %s proposes an overflow location but none is set.") % rec.display_name)
