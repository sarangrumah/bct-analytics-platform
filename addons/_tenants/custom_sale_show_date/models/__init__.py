from . import res_company
from . import sale_order
from . import sale_order_line
from . import sale_make_invoice_advance
from . import account_move
from . import account_payment_term
from . import custom_report_profit_loss_show
from . import profit_loss_wizard

# Register the tenant per-show P&L in the shared report dispatcher's code map so
# the OWL table client action and the XLSX exporter resolve "profit_loss_show".
# custom_accounting_reports is a hard dependency, so it is already imported.
from odoo.addons.custom_accounting_reports.models.custom_report_dispatch import (  # noqa: E402
    REPORT_MODEL_MAP,
)

REPORT_MODEL_MAP.setdefault("profit_loss_show", "custom.report.profit.loss.show")
