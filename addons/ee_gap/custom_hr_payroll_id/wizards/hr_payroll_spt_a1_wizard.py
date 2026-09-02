# -*- coding: utf-8 -*-
"""SPT 1721 A1 generator — annual PPh 21 reconciliation per employee.

Per employee, the wizard:
  1. Aggregates all payslips (regular + THR) for the calendar year.
  2. Recomputes the annual PPh 21 using progressive UU HPP brackets.
  3. Compares to the sum of monthly TER/annualised deductions.
  4. Reports kurang/lebih bayar.
  5. Renders the PT.A1 PDF (one page per employee).
  6. Builds an XML batch suitable for Coretax upload.
"""

from __future__ import annotations

import base64
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

from odoo import _, fields, models
from odoo.exceptions import UserError

from ..models.hr_payslip import _compute_pph21


class HrPayrollSPTA1Wizard(models.TransientModel):
    _name = "hr.payroll.spt.a1.wizard"
    _description = "Wizard: SPT 1721 A1 generator"

    fiscal_year = fields.Integer(required=True, default=lambda s: date.today().year - 1)
    employee_ids = fields.Many2many(
        "hr.employee",
        string="Employees",
        help="Empty = all employees with payslips in the fiscal year.",
    )
    output_format = fields.Selection(
        [("pdf", "PDF (PT.A1 form)"), ("xml", "Coretax XML batch"), ("both", "Both")],
        default="pdf",
        required=True,
    )

    # Result fields
    run_done = fields.Boolean(readonly=True)
    xml_attachment_id = fields.Many2one("ir.attachment", readonly=True)
    summary_html = fields.Html(readonly=True)

    # ------------------------------------------------------------------

    def action_run(self):
        self.ensure_one()
        Payslip = self.env["hr.payslip"].sudo()
        config = self.env["hr.payroll.config"].get_default()

        employees = self.employee_ids
        if not employees:
            # Discover from payslip presence in the year
            employees = Payslip.search([("period_year", "=", self.fiscal_year)]).mapped("employee_id")
        if not employees:
            raise UserError(_("No payslips found for fiscal year %s.") % self.fiscal_year)

        rows = []
        xml_root = ET.Element(
            "SPT1721A1Batch",
            attrib={
                "tahun_pajak": str(self.fiscal_year),
                "company_npwp": self.env.company.partner_id.x_custom_npwp or "",
            },
        )

        for emp in employees:
            data = self._compute_employee_annual(emp, config)
            rows.append(data)
            self._append_xml_employee(xml_root, data)

        # Persist XML attachment if requested
        attachment = None
        if self.output_format in ("xml", "both"):
            xml_bytes = ET.tostring(xml_root, encoding="utf-8", xml_declaration=True)
            attachment = (
                self.env["ir.attachment"]
                .sudo()
                .create(
                    {
                        "name": f"SPT_1721_A1_{self.fiscal_year}.xml",
                        "type": "binary",
                        "datas": base64.b64encode(xml_bytes),
                        "res_model": self._name,
                        "res_id": self.id,
                        "mimetype": "application/xml",
                    }
                )
            )

        # Summary HTML
        parts = [f"<h3>SPT 1721 A1 — Tahun Pajak {self.fiscal_year}</h3>"]
        parts.append(
            '<table class="table table-sm"><thead><tr>'
            "<th>Karyawan</th><th>PTKP</th><th>Bruto</th>"
            "<th>PPh 21 Terutang</th><th>PPh 21 Dipotong</th>"
            "<th>Lebih/Kurang Bayar</th></tr></thead><tbody>"
        )
        for r in rows:
            diff_class = "text-danger" if r["delta"] > 0 else ("text-success" if r["delta"] < 0 else "")
            parts.append(
                f"<tr><td>{r['employee_name']}</td>"
                f"<td>{r['ptkp_status']}</td>"
                f"<td>{r['bruto_year']:,.0f}</td>"
                f"<td>{r['pph_due']:,.0f}</td>"
                f"<td>{r['pph_paid']:,.0f}</td>"
                f"<td class='{diff_class}'><b>{r['delta']:,.0f}</b></td></tr>"
            )
        parts.append("</tbody></table>")

        self.write(
            {
                "run_done": True,
                "xml_attachment_id": attachment.id if attachment else False,
                "summary_html": "".join(parts),
            }
        )

        if self.output_format == "pdf":
            return self.env.ref("custom_hr_payroll_id.action_report_spt_1721_a1").report_action(
                self, data={"rows": rows, "fiscal_year": self.fiscal_year}
            )

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ------------------------------------------------------------------

    def _compute_employee_annual(self, emp, config) -> dict[str, Any]:
        Payslip = self.env["hr.payslip"].sudo()
        slips = Payslip.search(
            [
                ("employee_id", "=", emp.id),
                ("period_year", "=", self.fiscal_year),
                ("state", "in", ("approved", "paid")),
            ]
        )
        bruto_year = sum((s.gross_salary or 0) + (s.tunjangan_jabatan or 0) + (s.tunjangan_lain or 0) for s in slips)
        jht_emp_year = sum(s.bpjs_jht_emp or 0 for s in slips)
        jp_emp_year = sum(s.bpjs_jp_emp or 0 for s in slips)
        pph_paid = sum(s.pph21 or 0 for s in slips)

        biaya_jabatan_year = min(
            bruto_year * (config.biaya_jabatan_pct / 100.0),
            config.biaya_jabatan_max_year,
        )
        ptkp_status = emp.x_custom_ptkp_status or "TK/0"
        ptkp = config.get_ptkp(ptkp_status)

        net_year = bruto_year - biaya_jabatan_year - jht_emp_year - jp_emp_year
        taxable_year = max(0.0, net_year - ptkp)
        pph_due = _compute_pph21(taxable_year)
        delta = pph_due - pph_paid  # > 0 = kurang bayar; < 0 = lebih bayar

        return {
            "employee_id": emp.id,
            "employee_name": emp.name,
            "npwp": emp.x_custom_npwp or "",
            "nik": emp.x_custom_nik or "",
            "ptkp_status": ptkp_status,
            "ter_category": emp.x_custom_ter_category or "A",
            "bruto_year": bruto_year,
            "biaya_jabatan": biaya_jabatan_year,
            "jht_emp": jht_emp_year,
            "jp_emp": jp_emp_year,
            "ptkp": ptkp,
            "taxable_year": taxable_year,
            "pph_due": pph_due,
            "pph_paid": pph_paid,
            "delta": delta,
            "slips": slips,
        }

    def _append_xml_employee(self, root, data):
        """Append a <Pegawai> element. Simplified DJP-style structure."""
        emp_el = ET.SubElement(
            root,
            "Pegawai",
            attrib={
                "npwp": data["npwp"],
                "nik": data["nik"],
                "nama": data["employee_name"],
                "ptkp": data["ptkp_status"],
                "kategori_ter": data["ter_category"],
            },
        )
        for tag, val in [
            ("BrutoSetahun", data["bruto_year"]),
            ("BiayaJabatan", data["biaya_jabatan"]),
            ("JHTKaryawan", data["jht_emp"]),
            ("JPKaryawan", data["jp_emp"]),
            ("PTKP", data["ptkp"]),
            ("PenghasilanKenaPajak", data["taxable_year"]),
            ("PPh21Terutang", data["pph_due"]),
            ("PPh21Dipotong", data["pph_paid"]),
            ("KurangLebihBayar", data["delta"]),
        ]:
            el = ET.SubElement(emp_el, tag)
            el.text = f"{val:.2f}"
