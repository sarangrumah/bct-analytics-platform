# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    is_rental_asset = fields.Boolean(
        string="Is Rental Asset",
        help="When set, units received for this product can be bulk-converted "
        "to custom.fixed.asset records (and rental.asset records) via the "
        "'Convert to Assets' wizard on the receipt picking.",
    )
    asset_group_id = fields.Many2one(
        comodel_name="custom.fixed.asset.group",
        string="Asset Group",
        help="Default depreciation template applied to assets created from this product.",
    )
    auto_create_rental_asset = fields.Boolean(
        string="Also Create Rental Asset",
        default=True,
        help="When converting received units to fixed assets, also create a rental.asset record per serial number.",
    )

    @api.constrains("is_rental_asset", "asset_group_id", "tracking")
    def _check_rental_asset_config(self):
        for tmpl in self:
            if not tmpl.is_rental_asset:
                continue
            if not tmpl.asset_group_id:
                raise ValidationError(
                    _('Product "%s" is flagged as rental asset but has no Asset Group set.') % tmpl.display_name
                )
            if tmpl.tracking not in ("lot", "serial"):
                raise ValidationError(
                    _(
                        'Product "%s" is flagged as rental asset; tracking must be '
                        'set to "By Lots" or "By Unique Serial Number".'
                    )
                    % tmpl.display_name
                )
