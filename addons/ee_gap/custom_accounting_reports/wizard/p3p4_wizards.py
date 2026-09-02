# -*- coding: utf-8 -*-
"""Wizards for the P3/P4 tax-team reports.

They all share the same parameter set (masa + company + posted_only), so the
common fields and the print / xlsx actions live on an AbstractModel mixin;
each concrete wizard just declares which report model/code it drives.
"""

from datetime import date, datetime, time

from odoo import _, fields, models
from odoo.exceptions import UserError


class TaxReportWizardMixin(models.AbstractModel):
    _name = "custom.report.tax.wizard.mixin"
    _inherit = "custom.report.wizard.mixin"
    _description = "Common tax report wizard (masa + company)"

    date_from = fields.Date(
        string="Masa Dari",
        required=True,
        default=lambda self: date.today().replace(day=1),
    )
    date_to = fields.Date(
        string="Masa Sampai",
        required=True,
        default=lambda self: date.today(),
    )
    company_ids = fields.Many2many("res.company", default=lambda self: self.env.companies)
    posted_only = fields.Boolean(default=True)

    # Subclasses set these three.
    _report_code = None
    _report_model = None
    _filename_prefix = "Report"
    # Drill-down: source model + optional custom domain. Leave ``_source_model``
    # None on pure-summary reports (no per-row transaction to open).
    _source_model = None

    def _build_filters(self):
        self.ensure_one()
        return {
            "date_from": self.date_from,
            "date_to": self.date_to,
            "company_ids": self.company_ids.ids or self.env.companies.ids,
            "posted_only": self.posted_only,
        }

    def _options(self):
        return {
            **self._build_filters(),
            "date_from": self.date_from.isoformat(),
            "date_to": self.date_to.isoformat(),
        }

    def action_print(self):
        self.ensure_one()
        data = {"report_code": self._report_code, "doc_model": self._name, "options": self._options()}
        return self.env.ref("custom_accounting_reports.action_report_custom_financial").report_action(self, data=data)

    def action_export_xlsx(self):
        self.ensure_one()
        filename = "%s_%s_%s.xlsx" % (self._filename_prefix, self.date_from, self.date_to)
        return self.env[self._report_model]._xlsx_action(self._options(), filename)

    # ------------------------------------------------------------------
    # Drill-down to the source transactions
    # ------------------------------------------------------------------
    def _source_domain(self):
        """Default drill-down domain (masa + company). Overridden per report."""
        self.ensure_one()
        return [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]

    def action_view_source(self):
        self.ensure_one()
        if not self._source_model or self._source_model not in self.env:
            raise UserError(_("Tidak ada drill-down transaksi untuk laporan ini."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Transaksi Sumber"),
            "res_model": self._source_model,
            "view_mode": "list,form",
            "domain": self._source_domain(),
            "target": "current",
        }


class DppNilaiLainWizard(models.TransientModel):
    _name = "custom.report.dpp.nilai.lain.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "DPP Nilai Lain Wizard"
    _report_code = "dpp_nilai_lain"
    _report_model = "custom.report.dpp.nilai.lain"
    _filename_prefix = "DPP_Nilai_Lain"
    _source_model = "account.move.line"

    def _source_domain(self):
        self.ensure_one()
        domain = [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("parent_state", "=", "posted"))
        if "x_custom_dpp_method" in self.env["account.tax"]._fields:
            domain += [
                "|",
                ("tax_line_id.x_custom_dpp_method", "=", "nilai_lain"),
                ("tax_ids.x_custom_dpp_method", "=", "nilai_lain"),
            ]
        return domain


class FakturPenggantiWizard(models.TransientModel):
    _name = "custom.report.faktur.pengganti.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Faktur Pengganti Wizard"
    _report_code = "faktur_pengganti"
    _report_model = "custom.report.faktur.pengganti"
    _filename_prefix = "Faktur_Pengganti"
    _source_model = "account.move"

    def _source_domain(self):
        self.ensure_one()
        Move = self.env["account.move"]
        domain = [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("move_type", "in", ("out_invoice", "out_refund")),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("state", "=", "posted"))
        if "x_custom_coretax_kode_status" in Move._fields:
            domain.append(("x_custom_coretax_kode_status", "not in", (False, "", "00", "0")))
        elif "x_custom_coretax_status_code" in Move._fields:
            domain.append(("x_custom_coretax_status_code", "not in", (False, "00")))
        return domain


class EkualisasiOmzetWizard(models.TransientModel):
    _name = "custom.report.ekualisasi.omzet.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Ekualisasi Omzet Wizard"
    _report_code = "ekualisasi_omzet"
    _report_model = "custom.report.ekualisasi.omzet"
    _filename_prefix = "Ekualisasi_Omzet"


class PphEqualisasiWizard(models.TransientModel):
    _name = "custom.report.pph.equalisasi.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Ekualisasi PPh Wizard"
    _report_code = "pph_equalisasi"
    _report_model = "custom.report.pph.equalisasi"
    _filename_prefix = "Ekualisasi_PPh"
    _source_model = "account.move.line"

    def _source_domain(self):
        self.ensure_one()
        domain = [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("move_id.move_type", "in", ("in_invoice", "in_refund")),
            ("display_type", "=", "product"),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("parent_state", "=", "posted"))
        if "x_custom_withholding_category_id" in self.env["product.template"]._fields:
            domain.append(("product_id.product_tmpl_id.x_custom_withholding_category_id", "!=", False))
        return domain


class CoretaxSubmissionWizard(models.TransientModel):
    _name = "custom.report.coretax.submission.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Coretax Submission Monitoring Wizard"
    _report_code = "coretax_submission"
    _report_model = "custom.report.coretax.submission"
    _filename_prefix = "Monitoring_Submission_Coretax"
    _source_model = "custom.coretax.transaction"

    def _source_domain(self):
        self.ensure_one()
        return [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("create_date", ">=", datetime.combine(self.date_from, time.min)),
            ("create_date", "<=", datetime.combine(self.date_to, time.max)),
        ]


class PajakkuUsageWizard(models.TransientModel):
    _name = "custom.report.pajakku.usage.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Pajakku Usage Wizard"
    _report_code = "pajakku_usage"
    _report_model = "custom.report.pajakku.usage"
    _filename_prefix = "Usage_Pajakku"
    _source_model = "custom.coretax.pajakku.usage"

    def _source_domain(self):
        self.ensure_one()
        return [
            ("company_id", "in", self.company_ids.ids or self.env.companies.ids),
            ("period", ">=", self.date_from),
            ("period", "<=", self.date_to),
        ]


class PphReconciliationWizard(models.TransientModel):
    _name = "custom.report.pph.reconciliation.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Rekonsiliasi PPh Terutang vs Disetor Wizard"
    _report_code = "pph_reconciliation"
    _report_model = "custom.report.pph.reconciliation"
    _filename_prefix = "Rekonsiliasi_PPh"
    _source_model = "account.move.line"

    def _source_domain(self):
        self.ensure_one()
        cids = self.company_ids.ids or self.env.companies.ids
        accounts = self.env["account.account"].search(
            [("account_type", "=", "liability_current"), ("name", "ilike", "pph"), ("company_ids", "in", cids)]
        )
        domain = [
            ("account_id", "in", accounts.ids),
            ("company_id", "in", cids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("parent_state", "=", "posted"))
        return domain


class Pph25Wizard(models.TransientModel):
    _name = "custom.report.pph25.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Monitoring Angsuran PPh 25 Wizard"
    _report_code = "pph25"
    _report_model = "custom.report.pph25"
    _filename_prefix = "Angsuran_PPh25"
    _source_model = "account.move.line"

    def _source_domain(self):
        self.ensure_one()
        cids = self.company_ids.ids or self.env.companies.ids
        accounts = self.env["custom.report.pph25"]._pph25_accounts(cids)
        domain = [
            ("account_id", "in", accounts.ids),
            ("company_id", "in", cids),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.posted_only:
            domain.append(("parent_state", "=", "posted"))
        return domain


class TaxAuditWizard(models.TransientModel):
    _name = "custom.report.tax.audit.wizard"
    _inherit = "custom.report.tax.wizard.mixin"
    _description = "Jejak Audit Pajak Wizard"
    _report_code = "tax_audit"
    _report_model = "custom.report.tax.audit"
    _filename_prefix = "Jejak_Audit_Pajak"
    _source_model = "pdp.audit.log"

    def _source_domain(self):
        self.ensure_one()
        report = self.env["custom.report.tax.audit"]
        return [
            ("ts", ">=", datetime.combine(self.date_from, time.min)),
            ("ts", "<=", datetime.combine(self.date_to, time.max)),
            "|",
            ("model_name", "in", report.TAX_MODELS),
            ("action", "=", "pph_withholding_applied"),
        ]
