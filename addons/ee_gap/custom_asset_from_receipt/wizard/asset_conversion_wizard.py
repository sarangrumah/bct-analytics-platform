# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AssetConversionWizard(models.TransientModel):
    _name = "custom.asset.conversion.wizard"
    _description = "Convert Received Serial Numbers to Fixed Assets"

    picking_id = fields.Many2one(
        comodel_name="stock.picking",
        string="Receipt",
        required=True,
        readonly=True,
    )
    asset_group_id = fields.Many2one(
        comodel_name="custom.fixed.asset.group",
        string="Override Asset Group",
        help="Leave empty to use the asset group configured on each product.",
    )
    acquisition_date = fields.Date(
        required=True,
        default=fields.Date.context_today,
    )
    line_ids = fields.One2many(
        comodel_name="custom.asset.conversion.line",
        inverse_name="wizard_id",
        string="Lines",
    )

    # ------------------------------------------------------------------
    # Populate lines from picking move_line_ids
    # ------------------------------------------------------------------
    def _populate_lines(self):
        self.ensure_one()
        Asset = self.env["custom.fixed.asset"]
        vals_list = []
        for ml in self.picking_id.move_line_ids:
            product = ml.product_id
            if not product.is_rental_asset:
                continue
            if not ml.lot_id:
                continue
            if ml.quantity <= 0:
                continue
            po_line = ml.move_id.purchase_line_id
            existing = Asset.search([("lot_id", "=", ml.lot_id.id)], limit=1)
            vals_list.append(
                {
                    "wizard_id": self.id,
                    "move_line_id": ml.id,
                    "product_id": product.id,
                    "lot_id": ml.lot_id.id,
                    "purchase_line_id": po_line.id if po_line else False,
                    "unit_cost": po_line.price_unit if po_line else 0.0,
                    "selected": not existing,
                    "create_rental_asset": product.auto_create_rental_asset,
                    "existing_asset_id": existing.id if existing else False,
                }
            )
        self.env["custom.asset.conversion.line"].create(vals_list)
        if not vals_list:
            raise UserError(
                _(
                    "No serial-tracked rental-asset lines found in this receipt. "
                    "Ensure products are flagged 'Is Rental Asset' and have serial numbers assigned."
                )
            )
        if self.picking_id.date_done:
            self.acquisition_date = fields.Date.to_date(self.picking_id.date_done)

    # ------------------------------------------------------------------
    # Header actions
    # ------------------------------------------------------------------
    def action_select_all(self):
        self.ensure_one()
        self.line_ids.filtered(lambda l: not l.existing_asset_id).write({"selected": True})
        return self._reopen()

    def action_deselect_all(self):
        self.ensure_one()
        self.line_ids.write({"selected": False})
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------
    # Confirm conversion
    # ------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()
        Asset = self.env["custom.fixed.asset"]
        Rental = self.env["rental.asset"]
        created = Asset
        rentals = Rental
        lines = self.line_ids.filtered(lambda l: l.selected and not l.existing_asset_id)
        if not lines:
            raise UserError(_("No lines selected for conversion."))
        for line in lines:
            group = self.asset_group_id or line.product_id.product_tmpl_id.asset_group_id
            if not group:
                raise UserError(
                    _('Product "%s" has no Asset Group. Set one on the product or in the wizard override.')
                    % line.product_id.display_name
                )
            asset_vals = {
                "name": "%s / %s" % (line.product_id.display_name, line.lot_id.name),
                "product_id": line.product_id.id,
                "lot_id": line.lot_id.id,
                "purchase_line_id": line.purchase_line_id.id or False,
                "picking_id": self.picking_id.id,
                "group_id": group.id,
                "acquisition_value": line.unit_cost,
                "acquisition_date": self.acquisition_date,
                "useful_life_months": group.default_useful_life_months or 60,
                "asset_account_id": group.default_asset_account_id.id or False,
                "depreciation_account_id": group.default_depreciation_account_id.id or False,
                "expense_account_id": group.default_expense_account_id.id or False,
                "journal_id": group.default_journal_id.id or False,
            }
            asset = Asset.create(asset_vals)
            created |= asset

            if line.create_rental_asset:
                rental = Rental.create(
                    {
                        "name": "%s %s" % (line.product_id.display_name, line.lot_id.name),
                        "code": "RA/%s" % line.lot_id.name,
                        "product_id": line.product_id.id,
                        "serial_number": line.lot_id.name,
                        "fixed_asset_id": asset.id,
                    }
                )
                rentals |= rental

        # Return action showing created assets
        return {
            "type": "ir.actions.act_window",
            "name": _("Created Fixed Assets"),
            "res_model": "custom.fixed.asset",
            "view_mode": "list,form",
            "domain": [("id", "in", created.ids)],
        }


class AssetConversionLine(models.TransientModel):
    _name = "custom.asset.conversion.line"
    _description = "Asset Conversion Wizard Line"
    _order = "product_id, lot_id"

    wizard_id = fields.Many2one(
        comodel_name="custom.asset.conversion.wizard",
        required=True,
        ondelete="cascade",
    )
    move_line_id = fields.Many2one(
        comodel_name="stock.move.line",
        required=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        comodel_name="product.product",
        required=True,
        readonly=True,
    )
    lot_id = fields.Many2one(
        comodel_name="stock.lot",
        string="Serial/Lot",
        required=True,
        readonly=True,
    )
    serial_number = fields.Char(
        related="lot_id.name",
        readonly=True,
    )
    purchase_line_id = fields.Many2one(
        comodel_name="purchase.order.line",
        readonly=True,
    )
    unit_cost = fields.Monetary(
        currency_field="currency_id",
        required=True,
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda s: s.env.company.currency_id,
    )
    selected = fields.Boolean(default=True)
    create_rental_asset = fields.Boolean(default=True)
    existing_asset_id = fields.Many2one(
        comodel_name="custom.fixed.asset",
        readonly=True,
        string="Already Converted",
    )
    status = fields.Char(compute="_compute_status")

    @api.depends("existing_asset_id")
    def _compute_status(self):
        for line in self:
            line.status = _("Already converted") if line.existing_asset_id else _("New")
