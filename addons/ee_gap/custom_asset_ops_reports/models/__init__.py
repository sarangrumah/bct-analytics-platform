from . import custom_report_asset_opname
from . import custom_report_event_movement
from . import custom_report_spareparts
from . import custom_report_maintenance_health
from . import custom_report_repair_history

# Register these codes in the shared report dispatcher's map so the OWL table
# client action and the XLSX exporter resolve them. custom_accounting_reports
# is a hard dependency, so it is already imported by the time this runs.
from odoo.addons.custom_accounting_reports.models.custom_report_dispatch import (  # noqa: E402
    REPORT_MODEL_MAP,
)

REPORT_MODEL_MAP.setdefault("asset_opname", "custom.report.asset.opname")
REPORT_MODEL_MAP.setdefault("event_movement", "custom.report.event.movement")
REPORT_MODEL_MAP.setdefault("spareparts", "custom.report.spareparts")
REPORT_MODEL_MAP.setdefault("maintenance_health", "custom.report.maintenance.health")
REPORT_MODEL_MAP.setdefault("repair_history", "custom.report.repair.history")
