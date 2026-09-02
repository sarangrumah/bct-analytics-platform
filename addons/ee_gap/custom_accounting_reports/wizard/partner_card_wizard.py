# -*- coding: utf-8 -*-
from datetime import date

from odoo import fields, models


class _PartnerCardWizardMixin(models.AbstractModel):
    _name = "custom.report.partner.card.wizard.mixin"
    _inherit = "custom.report.wizard.mixin"
    _description = "Partner Card Wizard (Mixin)"

    # Concrete wizards set these.
    _card_report_model = None
    _card_report_code = None
    _card_filename_prefix = "Partner_Card"

    def _report_code_for_view(self):
        return self._card_report_code

    date_from = fields.Date(
        required=True,
        default=lambda self: date.today().replace(month=1, day=1),
    )
    date_to = fields.Date(
        required=True,
        default=lambda self: date.today(),
    )
    company_ids = fields.Many2many(
        "res.company",
        default=lambda self: self.env.companies,
    )
    partner_ids = fields.Many2many("res.partner")
    posted_only = fields.Boolean(default=True)

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "partner_ids": self.partner_ids.ids,
            "posted_only": self.posted_only,
        }

    def action_print(self):
        self.ensure_one()
        data = {
            "report_code": self._card_report_code,
            "doc_model": self._name,
            "options": {
                **self._build_filters(),
                "date_from": self.date_from.isoformat(),
                "date_to": self.date_to.isoformat(),
            },
        }
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        options = {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }
        filename = "%s_%s_%s.xlsx" % (
            self._card_filename_prefix,
            self.date_from,
            self.date_to,
        )
        return self.env[self._card_report_model]._xlsx_action(options, filename)


class PayableCardWizard(models.TransientModel):
    _name = "custom.report.payable.card.wizard"
    _inherit = "custom.report.partner.card.wizard.mixin"
    _description = "Kartu Utang Wizard"

    _card_report_model = "custom.report.payable.card"
    _card_report_code = "payable_card"
    _card_filename_prefix = "Kartu_Utang"


class ReceivableCardWizard(models.TransientModel):
    _name = "custom.report.receivable.card.wizard"
    _inherit = "custom.report.partner.card.wizard.mixin"
    _description = "Kartu Piutang Wizard"

    _card_report_model = "custom.report.receivable.card"
    _card_report_code = "receivable_card"
    _card_filename_prefix = "Kartu_Piutang"
