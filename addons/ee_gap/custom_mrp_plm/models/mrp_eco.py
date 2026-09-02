# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


ECO_KINDS = [
    ("bom_change", "BoM Change"),
    ("product_attr", "Product Attribute Change"),
    ("manufacturing_step", "Manufacturing Step Change"),
]


class MrpEco(models.Model):
    _name = "mrp.eco"
    _description = "Engineering Change Order"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    title = fields.Char(required=True, tracking=True)
    kind = fields.Selection(ECO_KINDS, required=True, default="bom_change")

    product_tmpl_id = fields.Many2one("product.template", required=True, index=True, tracking=True)
    current_bom_id = fields.Many2one("mrp.bom", domain="[('product_tmpl_id','=',product_tmpl_id)]")
    proposed_bom_id = fields.Many2one("mrp.bom", help="New revision; will be promoted on final approval.")
    revision = fields.Char(default="A", help="Free-text revision label (A, B, C / v1, v2, ...).")

    reason = fields.Html(required=True)
    impact_assessment = fields.Html()

    stage_id = fields.Many2one("mrp.eco.stage", index=True, tracking=True, group_expand="_group_expand_stages")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_review", "In Review"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    requested_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, required=True)
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "confidential"

    @api.model
    def _group_expand_stages(self, stages, domain):
        return self.env["mrp.eco.stage"].search([("active", "=", True)], order="sequence")

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("mrp.eco") or "ECO-???"
        return super().create(vals_list)

    def action_submit(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft ECOs can be submitted."))
            first_stage = (
                self.env["mrp.eco.stage"]
                .sudo()
                .search(
                    [("active", "=", True)],
                    order="sequence",
                    limit=1,
                )
            )
            rec.write({"state": "in_review", "stage_id": first_stage.id})
            rec._pdp_audit_write("eco_submit", rec.id, None)

    def action_approve(self):
        for rec in self:
            if rec.state != "in_review":
                raise UserError(_("Only in-review ECOs can be approved."))
            # Advance to next stage; if at final, promote
            next_stage = (
                self.env["mrp.eco.stage"]
                .sudo()
                .search(
                    [("active", "=", True), ("sequence", ">", rec.stage_id.sequence)],
                    order="sequence",
                    limit=1,
                )
            )
            if next_stage and not rec.stage_id.is_final:
                rec.write({"stage_id": next_stage.id})
                rec._pdp_audit_write("eco_stage_advance", rec.id, {"to_stage": next_stage.name})
            else:
                rec._promote_revision()

    def _promote_revision(self):
        self.ensure_one()
        if self.proposed_bom_id and self.current_bom_id:
            self.current_bom_id.sudo().write({"active": False})
            self.proposed_bom_id.sudo().write({"active": True})
        self.write(
            {
                "state": "approved",
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )
        self._pdp_audit_write(
            "eco_approved", self.id, {"revision": self.revision, "product_tmpl": self.product_tmpl_id.id}
        )

    def action_reject(self):
        for rec in self:
            rec.write({"state": "rejected"})
            rec._pdp_audit_write("eco_reject", rec.id, None)

    def action_cancel(self):
        for rec in self:
            if rec.state == "approved":
                raise UserError(_("Cannot cancel an approved ECO."))
            rec.write({"state": "cancelled"})
            rec._pdp_audit_write("eco_cancel", rec.id, None)
