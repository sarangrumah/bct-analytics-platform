# -*- coding: utf-8 -*-
from odoo import fields, models


CHECK_KINDS = [
    ("instructions", "Instructions Read"),
    ("pass_fail", "Pass / Fail"),
    ("measure", "Measurement"),
    ("visual", "Visual Inspection"),
]

FREQUENCIES = [
    ("every", "Every Operation"),
    ("first", "First Operation Only"),
    ("random", "Random Sample"),
    ("periodic", "Periodic"),
]


class QualityPoint(models.Model):
    _name = "quality.point"
    _description = "Quality Control Point"
    _order = "sequence, name"

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one("product.product")
    operation = fields.Selection(
        [
            ("incoming", "Incoming Goods"),
            ("manufacturing", "Manufacturing"),
            ("outgoing", "Outgoing"),
            ("ad_hoc", "Ad-hoc"),
        ],
        default="manufacturing",
        required=True,
    )
    check_kind = fields.Selection(CHECK_KINDS, required=True, default="pass_fail")
    frequency = fields.Selection(FREQUENCIES, required=True, default="every")
    instructions = fields.Html()
    measure_min = fields.Float(help="Minimum acceptable measurement (for kind=measure).")
    measure_max = fields.Float()
    measure_uom_id = fields.Many2one("uom.uom")
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)
    default_test_id = fields.Many2one(
        "custom.quality.test",
        string="Default Test Template",
        help="When provided, new quality.check records seeded from this point will copy the test's inspection lines.",
    )
