# -*- coding: utf-8 -*-
"""Witholding computation engine."""

from __future__ import annotations

import logging
import re
from decimal import Decimal, ROUND_HALF_UP

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

_NPWP_RE = re.compile(r"^\d{15,16}$")


def _has_valid_npwp(partner) -> bool:
    """Check `res.partner.vat` for a 15-or-16 digit Indonesian NPWP."""
    if not partner:
        return False
    value = (partner.vat or "").strip()
    if not value:
        return False
    # Strip common separators just in case (dots/dashes), then validate.
    cleaned = re.sub(r"[.\-\s]", "", value)
    return bool(_NPWP_RE.match(cleaned))


def _round_half_up_int(value) -> int:
    """Round to integer rupiah using half-up semantics."""
    if value in (None, False):
        return 0
    d = Decimal(str(value))
    return int(d.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class CustomWitholdingEngine(models.AbstractModel):
    _name = "custom.witholding.engine"
    _description = "PPh Witholding Computation Engine"

    @api.model
    def compute(self, partner, amount, pph_type, date=None, service_category=None):
        """Compute withholding for a given gross amount.

        Returns
        -------
        dict
            ``{rate, withheld, gross_remain, applicable_rule_id, has_npwp}``
            where ``rate`` is the percentage applied, ``withheld`` is an
            integer rupiah, ``gross_remain = amount - withheld``,
            ``applicable_rule_id`` is the ``custom.witholding.rate`` id
            (or ``False`` if no rule matched — in which case
            ``withheld == 0``).
        """
        if amount in (None, False):
            amount = 0.0
        date = date or fields.Date.context_today(self)
        Rate = self.env["custom.witholding.rate"]
        rule = Rate._find_active(pph_type, service_category, date)
        has_npwp = _has_valid_npwp(partner)
        if not rule:
            return {
                "rate": 0.0,
                "withheld": 0,
                "gross_remain": float(amount),
                "applicable_rule_id": False,
                "has_npwp": has_npwp,
            }
        rate = rule.with_npwp_rate if has_npwp else rule.without_npwp_rate
        withheld_raw = float(amount) * (rate / 100.0)
        withheld = _round_half_up_int(withheld_raw)
        return {
            "rate": rate,
            "withheld": withheld,
            "gross_remain": float(amount) - withheld,
            "applicable_rule_id": rule.id,
            "has_npwp": has_npwp,
        }

    @api.model
    def compute_and_log(
        self, partner, amount, pph_type, date=None, service_category=None, source_doc=None, state="computed"
    ):
        """Compute + persist a ``custom.witholding.application`` row."""
        result = self.compute(partner, amount, pph_type, date, service_category=service_category)
        vals = {
            "partner_id": partner.id if partner else False,
            "pph_type": pph_type,
            "service_category": service_category or False,
            "gross": float(amount or 0.0),
            "rate": result["rate"],
            "withheld": result["withheld"],
            "rule_id": result["applicable_rule_id"] or False,
            "has_npwp": result["has_npwp"],
            "state": state,
        }
        if source_doc:
            vals["source_doc"] = f"{source_doc._name},{source_doc.id}"
        application = self.env["custom.witholding.application"].create(vals)
        result["application_id"] = application.id
        return result
