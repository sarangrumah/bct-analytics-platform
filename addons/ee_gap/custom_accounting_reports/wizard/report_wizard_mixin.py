# -*- coding: utf-8 -*-
"""Shared wizard mixin adding the on-screen table view.

Every report wizard already exposes ``_build_filters()`` and drives one
``report_code``. This mixin adds a single ``action_view`` that opens the
generic OWL table client action (tag ``custom_report_table``) fed by the
report's existing ``_xlsx_columns`` + ``_build_lines`` contract — no new
per-report rendering code. PDF/Excel exports stay on the wizards untouched.
"""

from datetime import date, datetime

from odoo import models


class CustomReportWizardMixin(models.AbstractModel):
    _name = "custom.report.wizard.mixin"
    _description = "Custom Report Wizard (on-screen table) Mixin"

    # Wizards driving a single static report set this. Wizards whose code
    # is dynamic (day book) or carried under another name (partner card)
    # override ``_report_code_for_view`` instead.
    _report_code = None

    def _report_code_for_view(self):
        """The ``report_code`` this wizard should render on screen."""
        return self._report_code

    def _report_options(self):
        """JSON-serialisable filter envelope (dates → ISO strings) that the
        client hands back to ``get_report_table`` — mirrors what each
        wizard's ``action_print`` already passes."""
        self.ensure_one()
        opts = self._build_filters()
        return {
            key: (value.isoformat() if isinstance(value, (date, datetime)) else value) for key, value in opts.items()
        }

    def _report_context_extra(self):
        """Per-report context knobs (e.g. GL layout). Default: none."""
        return {}

    def _report_view_title(self):
        code = self._report_code_for_view()
        dispatch = self.env["report.custom_accounting_reports.report_dispatch"]
        report = dispatch._report_model(code)
        return report._report_title or self._description

    def action_view(self):
        """Open the interactive on-screen table for this report."""
        self.ensure_one()
        title = self._report_view_title()
        return {
            "type": "ir.actions.client",
            "tag": "custom_report_table",
            "name": title,
            "params": {
                "report_code": self._report_code_for_view(),
                "options": self._report_options(),
                "context_extra": self._report_context_extra(),
                "title": title,
            },
        }
