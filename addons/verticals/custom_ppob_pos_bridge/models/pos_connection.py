# -*- coding: utf-8 -*-
"""Per-feed inbound connection config for the POS mirror bridge.

One record per feed (pos / h2h). Holds the HMAC secret indirection
(``credential_ref`` -> ir.config_parameter), IP allowlist and clock-skew
window. Mirrors ``custom.ppob.va.bank.connection``; the secret itself is never
stored on the record.
"""

from odoo import fields, models


class PosConnection(models.Model):
    _name = "custom.ppob.pos.connection"
    _description = "POS Bridge: Inbound Feed Connection"
    _order = "feed"

    name = fields.Char(compute="_compute_name", store=True)
    feed = fields.Selection(
        selection=[
            ("pos", "POS Feed (sales / top-up / refund)"),
            ("h2h", "H2H Feed (fulfillment / cost / deposit)"),
        ],
        required=True,
        index=True,
    )
    credential_ref = fields.Char(
        string="Secret Key (ir.config_parameter)",
        required=True,
        help="Name of the ir.config_parameter holding the HMAC shared secret "
        "for this feed. NEVER paste the secret here.",
    )
    ip_whitelist = fields.Char(
        string="IP Allowlist (CIDR, comma-separated)",
        help="Optional. When set, only these source IPs may push this feed.",
    )
    max_clock_skew_s = fields.Integer(
        string="Max Clock Skew (s)",
        default=300,
    )
    status = fields.Selection(
        selection=[("active", "Active"), ("disabled", "Disabled")],
        default="active",
        required=True,
        index=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    _feed_company_uniq = models.Constraint("unique(feed, company_id)", "Only one connection per feed per company.")

    def _compute_name(self):
        labels = dict(self._fields["feed"].selection)
        for rec in self:
            rec.name = labels.get(rec.feed, rec.feed or "?")
