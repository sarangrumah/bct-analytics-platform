# -*- coding: utf-8 -*-
"""EOD settlement + balance-snapshot records for the POS mirror bridge.

Used by reconciliation control #2 (mitra wallet snapshot) and #3 (biller
deposit snapshot). A settlement row captures an authoritative balance reported
by POS POS (wallet) or the H2H switcher (deposit) at a point in time; the
reconciliation cron compares it against Odoo's mirror and flags variance.
"""

from odoo import fields, models


class PosSettlement(models.Model):
    _name = "custom.ppob.pos.settlement"
    _description = "POS Bridge: Settlement / Balance Snapshot"
    _order = "snapshot_date desc, id desc"

    name = fields.Char(default="/", copy=False)
    feed = fields.Selection(
        selection=[("pos", "POS (mitra wallet)"), ("h2h", "H2H (biller deposit)")],
        required=True,
        index=True,
    )
    snapshot_date = fields.Date(required=True, index=True)
    mitra_id = fields.Many2one("res.partner", string="Mitra", index=True)
    provider_id = fields.Many2one("custom.ppob.provider", string="Biller", index=True)
    reported_balance = fields.Monetary(
        currency_field="currency_id",
        help="Authoritative balance reported by the upstream (POS wallet / H2H deposit) at snapshot time.",
    )
    odoo_balance = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        help="Odoo mirror balance captured when the variance was computed.",
    )
    variance = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_variance",
        store=True,
    )
    state = fields.Selection(
        selection=[
            ("imported", "Imported"),
            ("matched", "Matched"),
            ("variance", "Variance"),
        ],
        default="imported",
        required=True,
        index=True,
    )
    external_ref = fields.Char(index=True, copy=False)
    raw_payload = fields.Text()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    _external_ref_uniq = models.Constraint("unique(external_ref)", "Settlement snapshot external ref must be unique.")

    def _compute_variance(self):
        for rec in self:
            rec.variance = (rec.reported_balance or 0.0) - (rec.odoo_balance or 0.0)
