# -*- coding: utf-8 -*-
from . import witholding_rate
from . import witholding_engine
from . import witholding_application
from . import account_move
from . import account_payment

# hr.payslip override is Enterprise-only (depends on hr_payroll). Importing it
# unconditionally crashes on Community because _inherit on a missing model
# raises TypeError at registry load. Only import when hr_payroll is present.
try:
    import odoo.modules.module

    if "hr_payroll" in odoo.modules.module.get_modules():
        from . import hr_payslip  # noqa: F401
except Exception:
    pass
