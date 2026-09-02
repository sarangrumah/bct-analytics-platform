# -*- coding: utf-8 -*-
"""Coretax XML export wizard.

Generates a Coretax-compliant XML payload for a selected period and
document type, validates it against the (operator-supplied) XSD under
`data/xsd/<doc_type>.xsd`, attaches it to the wizard for download, and
appends an `xml_export` row to `pdp.audit_log`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import date

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None  # type: ignore

try:
    import xmlschema
except ImportError:  # pragma: no cover
    xmlschema = None  # type: ignore


DOCUMENT_TYPES = [
    ("efaktur_keluaran", "e-Faktur Keluaran (Output VAT)"),
    ("faktur_masukan", "Faktur Masukan (Input VAT)"),
    ("bupot_21_tetap", "Bupot PPh 21 — Pegawai Tetap"),
    ("bupot_21_bukan_tetap", "Bupot PPh 21 — Bukan Pegawai Tetap"),
    ("bupot_23", "Bupot PPh 23"),
    ("bupot_26", "Bupot PPh 26"),
    ("bupot_unifikasi", "Bupot Unifikasi"),
]


# XML namespace placeholder — operator must align with the official DJP
# XSD targetNamespace once the XSD is dropped into data/xsd/.
NS_CORETAX = "urn:djp:coretax:v1"


class CoretaxExportWizard(models.TransientModel):
    _name = "custom.coretax.export.wizard"
    _description = "Coretax XML Export Wizard"

    config_id = fields.Many2one(
        "custom.coretax.config",
        string="Coretax Configuration",
        required=True,
        default=lambda self: self.env["custom.coretax.config"].search([("active", "=", True)], limit=1),
    )

    document_type = fields.Selection(DOCUMENT_TYPES, string="Document Type", required=True, default="efaktur_keluaran")
    period_year_from = fields.Integer(string="Year From", required=True, default=lambda self: date.today().year)
    period_month_from = fields.Integer(string="Month From", required=True, default=1)
    period_year_to = fields.Integer(string="Year To", required=True, default=lambda self: date.today().year)
    period_month_to = fields.Integer(string="Month To", required=True, default=lambda self: date.today().month)

    xml_filename = fields.Char(string="Generated Filename", readonly=True)
    xml_file = fields.Binary(string="Generated XML", readonly=True, attachment=False)
    validation_warning = fields.Text(string="Validation Warning", readonly=True)
    record_count = fields.Integer(string="Records Included", readonly=True)

    # ----- Public action -----
    def action_generate_xml(self):
        self.ensure_one()
        if etree is None:
            raise UserError(_("lxml is not installed."))

        records = self._gather_records()
        if not records:
            raise UserError(
                _("No %s records found in the selected period.")
                % dict(DOCUMENT_TYPES).get(self.document_type, self.document_type)
            )

        xml_bytes = self._build_xml(records)
        warning = self._validate_xml(xml_bytes)

        filename = "coretax_%s_%04d%02d-%04d%02d.xml" % (
            self.document_type,
            self.period_year_from,
            self.period_month_from,
            self.period_year_to,
            self.period_month_to,
        )

        # Persist an ir.attachment for traceability + download
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "datas": base64.b64encode(xml_bytes),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/xml",
            }
        )

        self.write(
            {
                "xml_filename": filename,
                "xml_file": base64.b64encode(xml_bytes),
                "validation_warning": warning or False,
                "record_count": len(records),
            }
        )

        self._audit_log_export(filename, len(records))

        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%d?download=true" % attachment.id,
            "target": "self",
        }

    # ----- Record gathering -----
    def _gather_records(self):
        """Return the recordset(s) to be marshalled.

        For VAT documents -> account.move; for bupot -> custom.coretax.bukti.potong.
        """
        domain_period = self._period_domain("invoice_date")
        if self.document_type == "efaktur_keluaran":
            return self.env["account.move"].search(
                [
                    ("move_type", "in", ("out_invoice", "out_refund")),
                    ("state", "=", "posted"),
                ]
                + domain_period
            )
        if self.document_type == "faktur_masukan":
            return self.env["account.move"].search(
                [
                    ("move_type", "in", ("in_invoice", "in_refund")),
                    ("state", "=", "posted"),
                ]
                + domain_period
            )

        # Bupot variants
        bupot_jenis_map = {
            "bupot_21_tetap": "21",
            "bupot_21_bukan_tetap": "21",
            "bupot_23": "23",
            "bupot_26": "26",
            "bupot_unifikasi": False,  # all
        }
        jenis = bupot_jenis_map[self.document_type]
        bupot_domain = [
            "&",
            "&",
            ("period_year", ">=", self.period_year_from),
            ("period_year", "<=", self.period_year_to),
            ("state", "in", ("confirmed", "exported", "submitted", "approved")),
        ]
        if jenis:
            bupot_domain.append(("jenis_pph", "=", jenis))
        return self.env["custom.coretax.bukti.potong"].search(bupot_domain)

    def _period_domain(self, date_field: str):
        start = date(self.period_year_from, self.period_month_from, 1)
        # naive end-of-month for the "to" bound
        end_month = self.period_month_to
        end_year = self.period_year_to
        if end_month == 12:
            end = date(end_year, 12, 31)
        else:
            end = date(end_year, end_month + 1, 1)
            end = date(end.year, end.month, 1)
        return [(date_field, ">=", start.isoformat()), (date_field, "<", end.isoformat())]

    # ----- XML build -----
    def _build_xml(self, records) -> bytes:
        nsmap = {None: NS_CORETAX}
        root = etree.Element("CoretaxDocument", nsmap=nsmap)
        etree.SubElement(root, "DocumentType").text = self.document_type
        etree.SubElement(root, "TaxpayerNPWP").text = self.config_id.npwp or ""
        etree.SubElement(root, "TaxpayerName").text = self.config_id.taxpayer_name or ""
        period = etree.SubElement(root, "Period")
        etree.SubElement(period, "From").text = "%04d-%02d" % (self.period_year_from, self.period_month_from)
        etree.SubElement(period, "To").text = "%04d-%02d" % (self.period_year_to, self.period_month_to)

        items = etree.SubElement(root, "Items")
        if self.document_type in ("efaktur_keluaran", "faktur_masukan"):
            for inv in records:
                self._append_invoice(items, inv)
        else:
            for bup in records:
                self._append_bupot(items, bup)

        return etree.tostring(root, pretty_print=True, xml_declaration=True, encoding="UTF-8")

    def _append_invoice(self, parent, inv):
        node = etree.SubElement(parent, "Faktur")
        etree.SubElement(node, "NSFP").text = inv.x_custom_nsfp or ""
        etree.SubElement(node, "StatusCode").text = inv.x_custom_coretax_status_code or "00"
        etree.SubElement(node, "InvoiceNumber").text = inv.name or ""
        etree.SubElement(node, "InvoiceDate").text = inv.invoice_date.isoformat() if inv.invoice_date else ""
        etree.SubElement(node, "CounterpartyNPWP").text = (inv.partner_id.vat or "").replace(".", "").replace("-", "")
        etree.SubElement(node, "CounterpartyName").text = inv.partner_id.name or ""
        etree.SubElement(node, "DPP").text = "%.2f" % (inv.amount_untaxed or 0.0)
        etree.SubElement(node, "PPN").text = "%.2f" % (inv.amount_tax or 0.0)
        etree.SubElement(node, "Total").text = "%.2f" % (inv.amount_total or 0.0)

    def _append_bupot(self, parent, bup):
        node = etree.SubElement(parent, "BuktiPotong")
        etree.SubElement(node, "NoBupot").text = bup.no_bupot or ""
        etree.SubElement(node, "Tanggal").text = bup.tanggal_bupot.isoformat() if bup.tanggal_bupot else ""
        etree.SubElement(node, "JenisPPh").text = (bup.jenis_pph or "").replace("_", ".")
        etree.SubElement(node, "CounterpartyNPWP").text = (bup.partner_id.vat or "").replace(".", "").replace("-", "")
        etree.SubElement(node, "CounterpartyName").text = bup.partner_id.name or ""
        etree.SubElement(node, "Tarif").text = "%.4f" % (bup.tarif or 0.0)
        etree.SubElement(node, "DPP").text = "%.2f" % (bup.dpp or 0.0)
        etree.SubElement(node, "PPhTerpotong").text = "%.2f" % (bup.pph_terpotong or 0.0)

    # ----- XSD validation (best-effort) -----
    def _validate_xml(self, xml_bytes: bytes) -> str | None:
        if xmlschema is None:
            return _("xmlschema library not installed — skipping XSD validation.")

        module_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        xsd_path = os.path.join(module_path, "data", "xsd", f"{self.document_type}.xsd")
        if not os.path.isfile(xsd_path):
            msg = _(
                "XSD for %s is not present at %s. XML generated but NOT "
                "validated client-side. DJP does not publish Coretax XSDs "
                "publicly; submission via the Coretax portal still triggers "
                "authoritative server-side validation. See data/xsd/README.md "
                "for client-side options (ASPP subscription required)."
            ) % (self.document_type, xsd_path)
            _logger.warning("coretax export: %s", msg)
            return msg

        try:
            schema = xmlschema.XMLSchema(xsd_path)
            schema.validate(xml_bytes)
            return None
        except Exception as exc:  # noqa: BLE001 — validation surface is intentionally wide
            _logger.warning("coretax export: XSD validation failed: %s", exc)
            return _("XSD validation failed: %s") % exc

    # ----- Audit log -----
    def _audit_log_export(self, filename: str, count: int) -> None:
        cr = self.env.cr
        payload = json.dumps(
            {
                "document_type": self.document_type,
                "filename": filename,
                "records": count,
                "period_from": "%04d-%02d" % (self.period_year_from, self.period_month_from),
                "period_to": "%04d-%02d" % (self.period_year_to, self.period_month_to),
            }
        )
        cr.execute(
            """
            INSERT INTO pdp.audit_log
                (actor_user_id, actor_login, tenant_db, model_name, res_id,
                 action, field_changes, classification, reason)
            VALUES (%s, %s, %s, %s, %s, 'xml_export', %s::jsonb, 'financial', %s)
            """,
            (
                self.env.uid,
                self.env.user.login,
                cr.dbname,
                self._name,
                self.id,
                payload,
                "Coretax XML export — %s (%d records)" % (self.document_type, count),
            ),
        )
