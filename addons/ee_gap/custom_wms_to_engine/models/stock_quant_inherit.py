# -*- coding: utf-8 -*-
"""stock.quant hook — flag rule re-evaluation on inventory mutation.

We intentionally do NOT run the engine inline (it would block writes and may
fan out across many locations). Instead the write() override marks the
*relevant* rule(s) dirty by stamping ``last_run_at`` so the cron picks them up
next tick.

"Relevant" matters: an earlier revision stamped **every** active low-water rule
on **every** quantity write, which made the marker useless (all rules always
looked dirty) and wrote to the whole rule table on every stock move. Rules are
now matched against the warehouse and company of the quants that actually
changed.
"""

from __future__ import annotations

import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class StockQuant(models.Model):
    _inherit = "stock.quant"

    def _to_rules_to_stamp(self):
        """Active low-water rules whose warehouse/company covers these quants."""
        Rule = self.env["custom.to.rule"].sudo()
        warehouses = self.mapped("location_id.warehouse_id")
        companies = self.mapped("company_id")
        domain = [("active", "=", True), ("trigger", "=", "low_water_mark")]
        if warehouses:
            # A rule with no warehouse is global and always in scope.
            domain.append(("warehouse_id", "in", [False] + warehouses.ids))
        if companies:
            domain.append(("company_id", "in", [False] + companies.ids))
        return Rule.search(domain)

    def write(self, vals):
        res = super().write(vals)
        if "quantity" in vals:
            # Stamp low-water mark rules so the cron knows to re-evaluate.
            try:
                rules = self._to_rules_to_stamp()
                if rules:
                    rules.write({"last_run_at": fields.Datetime.now()})
            except Exception as exc:  # pragma: no cover - never break write
                _logger.debug("TO low-water stamp skipped: %s", exc)
        return res
