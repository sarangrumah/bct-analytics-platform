# -*- coding: utf-8 -*-
"""Mirrors of ESB master data.

Branches, locations, purposes and document templates are held in dedicated
mirror models rather than being force-fed into ``stock.warehouse`` /
``stock.location``. Auto-creating Odoo warehouses from a cron would generate
sequences, routes and picking types on every sync for entities Odoo does not
actually operate — ESB remains the source of truth for stock, so Odoo only needs
to *know* about them. Where an Odoo counterpart genuinely is required (physical
counting, forecasting), the mirror carries an optional link that an admin maps
or creates deliberately.

Products are the exception: they must exist as ``product.product`` for counting
and forecasting, so the ESB keys live directly on the product (see
``product_product.py``) alongside a ``custom.esb.product.detail`` mirror for the
per-unit ``productDetailID`` values.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EsbBranch(models.Model):
    _name = "custom.esb.branch"
    _description = "ESB Branch (Outlet)"
    _inherit = ["pdp.audited.mixin"]
    _order = "code"
    _rec_names_search = ["name", "code"]

    esb_branch_id = fields.Integer(string="ESB Branch ID", required=True, index=True)
    code = fields.Char(string="Branch Code", index=True)
    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, index=True)
    warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Odoo Warehouse",
        index=True,
        help="Optional. Map this only if Odoo needs to move stock for this outlet; "
        "the connector itself does not require it.",
    )
    location_ids = fields.One2many("custom.esb.location", "branch_id")
    location_count = fields.Integer(compute="_compute_location_count")
    last_synced_at = fields.Datetime(readonly=True)

    _esb_id_uniq = models.Constraint("unique(esb_branch_id, company_id)", "ESB branch ID must be unique per company.")

    @api.depends("location_ids")
    def _compute_location_count(self):
        for rec in self:
            rec.location_count = len(rec.location_ids)

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else (rec.name or "")


class EsbLocation(models.Model):
    _name = "custom.esb.location"
    _description = "ESB Stock Location"
    _inherit = ["pdp.audited.mixin"]
    _order = "branch_id, name"

    esb_location_id = fields.Integer(string="ESB Location ID", required=True, index=True)
    name = fields.Char(required=True)
    branch_id = fields.Many2one("custom.esb.branch", required=True, ondelete="cascade", index=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(related="branch_id.company_id", store=True, index=True)
    location_id = fields.Many2one(
        "stock.location",
        string="Odoo Location",
        index=True,
        help="Optional link, needed only by features that count stock inside Odoo.",
    )
    countable = fields.Boolean(
        default=True,
        help="Item journals only accept warehouse- and kitchen-type locations. "
        "Untick locations ESB will reject as an opname target.",
    )
    last_synced_at = fields.Datetime(readonly=True)

    _esb_id_uniq = models.Constraint("unique(esb_location_id, branch_id)", "ESB location ID must be unique per branch.")

    @api.depends("name", "branch_id.code")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"{rec.branch_id.code or rec.branch_id.name}/{rec.name}" if rec.branch_id else rec.name


class EsbPurpose(models.Model):
    """ESB adjustment reason. Each purpose carries a COA, so the purpose chosen
    on an item journal line is what routes the stock variance in ESB's GL."""

    _name = "custom.esb.purpose"
    _description = "ESB Purpose (Adjustment Reason)"
    _order = "name"

    esb_purpose_id = fields.Integer(string="ESB Purpose ID", required=True, index=True)
    name = fields.Char(required=True)
    account_code = fields.Char(string="Purpose COA")
    applied_to = fields.Char()
    active = fields.Boolean(default=True)
    is_default_gain = fields.Boolean(
        string="Default for Stock Gain", help="Used for positive opname variances when no explicit purpose is set."
    )
    is_default_loss = fields.Boolean(
        string="Default for Stock Loss", help="Used for negative opname variances when no explicit purpose is set."
    )

    _esb_id_uniq = models.Constraint("unique(esb_purpose_id)", "ESB purpose ID must be unique.")


class EsbDocumentTemplate(models.Model):
    _name = "custom.esb.document.template"
    _description = "ESB Document Template"
    _order = "name"

    esb_template_id = fields.Integer(string="ESB Template ID", required=True, index=True)
    name = fields.Char(required=True)
    branch_names = fields.Char()
    active = fields.Boolean(default=True)

    _esb_id_uniq = models.Constraint("unique(esb_template_id)", "ESB template ID must be unique.")


class EsbSupplier(models.Model):
    """ESB supplier. Needed to raise a purchase order, which requires a
    ``supplierID`` and carries the credit term used for the PO due day."""

    _name = "custom.esb.supplier"
    _description = "ESB Supplier"
    _order = "name"
    _rec_names_search = ["name", "code"]

    esb_supplier_id = fields.Integer(string="ESB Supplier ID", required=True, index=True)
    code = fields.Char(string="Supplier Code", index=True)
    name = fields.Char(required=True)
    due_days = fields.Integer(string="Credit Term (Days)")
    category = fields.Char()
    contact_person = fields.Char()
    phone = fields.Char()
    active = fields.Boolean(default=True)
    partner_id = fields.Many2one("res.partner", string="Odoo Partner", index=True)

    _esb_id_uniq = models.Constraint("unique(esb_supplier_id)", "ESB supplier ID must be unique.")

    @api.depends("code", "name")
    def _compute_display_name(self):
        for rec in self:
            rec.display_name = f"[{rec.code}] {rec.name}" if rec.code else (rec.name or "")


class EsbProductDetail(models.Model):
    """One ESB ``productDetail`` — a product in a specific unit of measure.

    ``productDetailID`` (not ``productID``) is the key every transactional ESB
    endpoint wants, and a product has one per unit. The stock unit is the one
    the connector uses by default; the others are kept so purchase and transfer
    documents can be raised in the unit ESB expects.
    """

    _name = "custom.esb.product.detail"
    _description = "ESB Product Detail (per Unit)"
    _order = "product_id, sequence"

    esb_product_detail_id = fields.Integer(string="ESB Product Detail ID", required=True, index=True)
    esb_product_id = fields.Integer(string="ESB Product ID", required=True, index=True)
    product_id = fields.Many2one("product.product", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    unit_name = fields.Char()
    conversion_factor = fields.Float(default=1.0, digits=(20, 4))
    base_price = fields.Float(digits="Product Price")
    sku = fields.Char(index=True)
    is_stock_unit = fields.Boolean(index=True)
    is_purchase_unit = fields.Boolean()
    is_base_unit = fields.Boolean()
    is_transfer_unit = fields.Boolean()
    is_sales_unit = fields.Boolean()

    _esb_id_uniq = models.Constraint("unique(esb_product_detail_id)", "ESB product detail ID must be unique.")

    @api.model
    def _detail_for(self, product, kind="stock"):
        """Return the productDetail to transact this product in.

        Falls back through stock → base → any, because ESB does not guarantee a
        product declares every default unit.
        """
        field = {
            "stock": "is_stock_unit",
            "purchase": "is_purchase_unit",
            "base": "is_base_unit",
            "transfer": "is_transfer_unit",
            "sales": "is_sales_unit",
        }.get(kind, "is_stock_unit")
        details = self.search([("product_id", "=", product.id)])
        if not details:
            raise UserError(
                _("Product %s has no ESB product detail. Run the ESB master sync first.") % product.display_name
            )
        return details.filtered(field)[:1] or details.filtered("is_base_unit")[:1] or details[:1]
