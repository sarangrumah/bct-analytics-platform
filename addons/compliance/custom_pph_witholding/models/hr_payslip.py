# -*- coding: utf-8 -*-
"""Lazy hook on hr.payslip.

We only attach the override when the ``hr_payroll`` module is actually
installed; otherwise this file is a no-op so the witholding module does
not impose a hard dependency on payroll.
"""

from __future__ import annotations

import logging

from odoo import models

_logger = logging.getLogger(__name__)


try:
    _hr_payslip_available = True

    class HrPayslip(models.Model):
        _inherit = "hr.payslip"

        def _custom_pph_apply_pph21(self):
            """Compute PPh21 for each finalised payslip.

            Best-effort: failures are logged, never raised, so payroll
            close cannot be blocked by a missing rate.
            """
            Engine = self.env["custom.witholding.engine"]
            for slip in self:
                try:
                    partner = slip.employee_id.work_contact_id or slip.employee_id.user_id.partner_id
                    Engine.compute_and_log(
                        partner=partner,
                        amount=slip.basic_wage if hasattr(slip, "basic_wage") else 0.0,
                        pph_type="21",
                        date=slip.date_to,
                        source_doc=slip,
                    )
                except Exception as e:
                    _logger.warning(
                        "PPh21 witholding compute failed on payslip %s: %s",
                        slip.id,
                        e,
                    )

except (KeyError, ValueError, TypeError):
    # hr.payslip not in registry — hr_payroll not installed (Enterprise-only
    # module, or this is a deployment without payroll). Skip silently;
    # the rest of the witholding module operates on account.move/payment.
    _hr_payslip_available = False
