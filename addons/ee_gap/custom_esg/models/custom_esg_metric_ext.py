# -*- coding: utf-8 -*-
"""Extend ``custom.esg.metric`` with multi-framework code mapping.

Stores GRI / POJK51 / SASB codes inline as comma-separated tokens of the
form ``FRAMEWORK:CODE``, e.g. ``GRI:302-1,POJK51:E1,SASB:EM-CG-110a.1``.
A helper resolves metrics by framework prefix for use by the report.
"""

from __future__ import annotations

from odoo import api, fields, models


class CustomEsgMetric(models.Model):
    _inherit = "custom.esg.metric"

    framework_codes = fields.Char(
        string="Framework Codes",
        help=("Comma-separated framework:code mapping, e.g. 'GRI:302-1,POJK51:E1,SASB:EM-CG-110a.1'."),
    )

    @api.model
    def get_framework_metrics(self, framework):
        """Return metrics whose ``framework_codes`` mentions ``framework``.

        Match is case-insensitive on the prefix before ``:`` of each token.
        """
        if not framework:
            return self.browse([])
        framework_norm = framework.strip().upper()
        candidates = self.search([("framework_codes", "!=", False), ("is_active", "=", True)])
        hits = self.browse([])
        for metric in candidates:
            tokens = (metric.framework_codes or "").split(",")
            for tok in tokens:
                tok = tok.strip()
                if not tok or ":" not in tok:
                    continue
                prefix = tok.split(":", 1)[0].strip().upper()
                if prefix == framework_norm:
                    hits |= metric
                    break
        return hits
