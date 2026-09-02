from odoo import fields, models


class CustomEsgMetric(models.Model):
    _name = "custom.esg.metric"
    _description = "ESG Metric"
    _inherit = ["mail.thread"]
    _order = "category, code"

    name = fields.Char(string="Name", required=True, tracking=True)
    code = fields.Char(
        string="Code",
        required=True,
        help="Metric code per GRI/POJK 51",
    )
    category = fields.Selection(
        [
            ("environmental", "Environmental"),
            ("social", "Social"),
            ("governance", "Governance"),
        ],
        string="Category",
        required=True,
        tracking=True,
    )
    subcategory = fields.Char(string="Subcategory")
    unit = fields.Char(
        string="Unit of Measure",
        help="ton CO2, kWh, %, etc.",
    )
    description = fields.Text(string="Description")
    is_active = fields.Boolean(string="Active", default=True)
    color = fields.Integer(string="Color")

    _code_uniq = models.Constraint(
        "unique(code)",
        "ESG metric code must be unique.",
    )
