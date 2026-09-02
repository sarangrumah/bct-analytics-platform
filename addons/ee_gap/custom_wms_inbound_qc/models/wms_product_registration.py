# -*- coding: utf-8 -*-
"""Unknown-item registration captured at goods receipt.

A receiving operator who scans a barcode the system does not know must not be
blocked: the pallet is physically on the dock either way. The scan is captured
here as a draft registration, receiving continues, and a data owner turns the
record into a real ``product.product`` afterwards.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError

STATES = [
    ("draft", "Draft"),
    ("submitted", "Submitted"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
]


class WmsProductRegistration(models.Model):
    _name = "custom.wms.product.registration"
    _description = "WMS Unknown-Item Registration"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, copy=False, readonly=True, default=lambda s: s.env._("New"))
    state = fields.Selection(STATES, default="draft", tracking=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)

    # -- what was scanned --------------------------------------------------
    barcode = fields.Char(required=True, index=True, tracking=True)
    scanned_description = fields.Char(string="Description as Scanned")
    picking_id = fields.Many2one("stock.picking", string="Received On", index=True)
    partner_id = fields.Many2one("res.partner", string="Supplier")
    scanned_by_id = fields.Many2one("res.users", default=lambda self: self.env.user, readonly=True)
    quantity = fields.Float(default=1.0)

    # -- proposed product master ------------------------------------------
    proposed_name = fields.Char(string="Product Name", tracking=True)
    proposed_default_code = fields.Char(string="Internal Reference")
    categ_id = fields.Many2one("product.category", string="Product Category")
    uom_id = fields.Many2one("uom.uom", string="Unit of Measure")
    weight = fields.Float(string="Unit Weight (kg)")
    volume = fields.Float(string="Unit Volume (m3)")
    package_type_id = fields.Many2one("stock.package.type", string="Handling Unit")
    abc_class = fields.Selection([("A", "A"), ("B", "B"), ("C", "C")], default="B")

    # -- outcome -----------------------------------------------------------
    product_id = fields.Many2one("product.product", string="Created Product", readonly=True, copy=False)
    rejection_reason = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.wms.product.registration") or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("%s is not a draft registration.") % rec.name)
            if not rec.proposed_name:
                raise UserError(_("Give %s a product name before submitting it.") % rec.name)
            rec.state = "submitted"
        return True

    def action_approve(self):
        """Create the real product and stamp it back on the registration."""
        for rec in self:
            if rec.state != "submitted":
                raise UserError(_("%s must be submitted before approval.") % rec.name)
            if rec.product_id:
                raise UserError(_("%s already produced product %s.") % (rec.name, rec.product_id.display_name))
            duplicate = self.env["product.product"].search([("barcode", "=", rec.barcode)], limit=1)
            if duplicate:
                raise UserError(
                    _("Barcode %(bc)s already belongs to %(prod)s — reject this registration instead.")
                    % {"bc": rec.barcode, "prod": duplicate.display_name}
                )
            rec.product_id = rec._create_product()
            rec.state = "approved"
            rec.message_post(
                body=_("Approved — product %s created.") % rec.product_id.display_name,
                message_type="comment",
            )
        return True

    def _create_product(self):
        self.ensure_one()
        vals = {
            "name": self.proposed_name,
            "default_code": self.proposed_default_code or False,
            "barcode": self.barcode,
            "is_storable": True,
            "type": "consu",
            "weight": self.weight or 0.0,
            "volume": self.volume or 0.0,
            "abc_class": self.abc_class or "B",
        }
        if self.categ_id:
            vals["categ_id"] = self.categ_id.id
        if self.uom_id:
            vals["uom_id"] = self.uom_id.id
        if self.package_type_id:
            vals["wms_package_type_id"] = self.package_type_id.id
        return self.env["product.product"].create(vals)

    def action_reject(self):
        for rec in self:
            if rec.state in ("approved", "rejected"):
                raise UserError(_("%s is already closed.") % rec.name)
            rec.state = "rejected"
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state == "approved":
                raise UserError(_("%s created a product and cannot be reopened.") % rec.name)
            rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Capture entrypoint (called from scanning flows)
    # ------------------------------------------------------------------

    @api.model
    def capture(self, barcode, picking=None, description=None, quantity=1.0):
        """Idempotently record an unknown barcode seen during receiving.

        Re-scanning the same barcode on the same receipt accumulates quantity
        on the open registration rather than creating a second row.
        """
        if not barcode:
            raise UserError(_("Cannot register an empty barcode."))
        domain = [("barcode", "=", barcode), ("state", "in", ("draft", "submitted"))]
        if picking:
            domain.append(("picking_id", "=", picking.id))
        existing = self.search(domain, limit=1)
        if existing:
            existing.quantity += quantity
            return existing
        return self.create(
            {
                "barcode": barcode,
                "scanned_description": description or "",
                "picking_id": picking.id if picking else False,
                "partner_id": picking.partner_id.id if picking and picking.partner_id else False,
                "quantity": quantity,
            }
        )
