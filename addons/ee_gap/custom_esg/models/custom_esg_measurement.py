from odoo import fields, models


class CustomEsgMeasurement(models.Model):
    _name = "custom.esg.measurement"
    _description = "ESG Measurement"
    _inherit = ["mail.thread"]
    _order = "period_end desc, id desc"

    metric_id = fields.Many2one(
        comodel_name="custom.esg.metric",
        string="Metric",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    period_start = fields.Date(string="Period Start", required=True, tracking=True)
    period_end = fields.Date(string="Period End", required=True, tracking=True)
    value = fields.Float(string="Value", tracking=True)
    source_document = fields.Char(
        string="Source Document",
        help="Source ref e.g. invoice, payroll",
    )
    notes = fields.Text(string="Notes")
    company_id = fields.Many2one(
        comodel_name="res.company",
        string="Company",
        default=lambda self: self.env.company,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("validated", "Validated"),
            ("audited", "Audited"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )
    validated_by_user_id = fields.Many2one(
        comodel_name="res.users",
        string="Validated By",
        readonly=True,
    )

    def action_validate(self):
        for rec in self:
            rec.write(
                {
                    "state": "validated",
                    "validated_by_user_id": self.env.user.id,
                }
            )
        return True

    def action_audit(self):
        for rec in self:
            rec.state = "audited"
        return True

    def action_reset_draft(self):
        for rec in self:
            rec.write(
                {
                    "state": "draft",
                    "validated_by_user_id": False,
                }
            )
        return True
