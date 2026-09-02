# -*- coding: utf-8 -*-
"""Coretax XML import wizard.

Parses an uploaded Coretax document (e.g., a Bupot Unifikasi response
XML) and materialises matching `custom.coretax.bukti.potong` rows. Partner
matching uses NPWP (digits-only normalised). Every successful import
emits an `xml_import` row to `pdp.audit_log`.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from datetime import datetime

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None  # type: ignore


DOCUMENT_TYPES = [
    ("efaktur_keluaran", "e-Faktur Keluaran"),
    ("faktur_masukan", "Faktur Masukan"),
    ("bupot_21_tetap", "Bupot PPh 21 — Pegawai Tetap"),
    ("bupot_21_bukan_tetap", "Bupot PPh 21 — Bukan Pegawai Tetap"),
    ("bupot_23", "Bupot PPh 23"),
    ("bupot_26", "Bupot PPh 26"),
    ("bupot_unifikasi", "Bupot Unifikasi"),
]


def _digits_only(s: str) -> str:
    return re.sub(r"\D", "", s or "")


class CoretaxImportWizard(models.TransientModel):
    _name = "custom.coretax.import.wizard"
    _description = "Coretax XML Import Wizard"

    document_type = fields.Selection(DOCUMENT_TYPES, string="Document Type", required=True, default="bupot_unifikasi")
    xml_filename = fields.Char(string="Filename")
    xml_file = fields.Binary(string="XML File", required=True, attachment=False)
    source = fields.Selection(
        selection=[("received", "Received"), ("issued", "Issued")],
        default="received",
        required=True,
    )

    created_count = fields.Integer(string="Created Records", readonly=True)
    skipped_count = fields.Integer(string="Skipped (no NPWP match)", readonly=True)
    log = fields.Text(string="Import Log", readonly=True)

    def action_import(self):
        self.ensure_one()
        if etree is None:
            raise UserError(_("lxml is not installed."))
        if not self.xml_file:
            raise UserError(_("Upload an XML file first."))

        try:
            xml_bytes = base64.b64decode(self.xml_file)
        except (ValueError, TypeError) as exc:
            raise UserError(_("Invalid XML payload: %s") % exc) from exc

        try:
            root = etree.fromstring(xml_bytes)
        except etree.XMLSyntaxError as exc:
            raise UserError(_("Malformed XML: %s") % exc) from exc

        if self.document_type in ("efaktur_keluaran", "faktur_masukan"):
            created, skipped, lines = self._import_invoices(root)
        else:
            created, skipped, lines = self._import_bupot(root)

        self.write(
            {
                "created_count": created,
                "skipped_count": skipped,
                "log": "\n".join(lines),
            }
        )

        self._audit_log_import(created, skipped)

        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    # ----- Bupot import -----
    def _import_bupot(self, root) -> tuple[int, int, list[str]]:
        created = 0
        skipped = 0
        lines: list[str] = []
        Bupot = self.env["custom.coretax.bukti.potong"]
        Partner = self.env["res.partner"]

        # XPath tolerant of (un)namespaced docs.
        nodes = root.xpath(".//*[local-name()='BuktiPotong']")
        for n in nodes:

            def _text(tag: str) -> str:
                el = n.find(f".//{{*}}{tag}")
                if el is None:
                    el = n.find(f".//{tag}")
                return (el.text or "").strip() if el is not None and el.text else ""

            npwp = _digits_only(_text("CounterpartyNPWP"))
            no_bupot = _text("NoBupot")
            if not no_bupot:
                lines.append("Skip: missing NoBupot")
                skipped += 1
                continue

            partner = Partner.search([("vat", "=", npwp)], limit=1) if npwp else Partner.browse()
            if not partner and npwp:
                # fallback: search by stripping common formatting
                partner = Partner.search([("vat", "ilike", npwp[:9])], limit=1)

            if not partner:
                lines.append("Skip: no partner for NPWP=%s (No.Bupot=%s)" % (npwp, no_bupot))
                skipped += 1
                continue

            tanggal_raw = _text("Tanggal")
            tanggal = None
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    tanggal = datetime.strptime(tanggal_raw, fmt).date()
                    break
                except ValueError:
                    continue

            jenis_raw = _text("JenisPPh").replace(".", "_")
            jenis_map = {"21": "21", "23": "23", "26": "26", "4_2": "4_2", "15": "15", "22": "22"}
            jenis = jenis_map.get(jenis_raw)
            if not jenis:
                lines.append("Skip: unknown JenisPPh=%s for %s" % (jenis_raw, no_bupot))
                skipped += 1
                continue

            existing = Bupot.search(
                [
                    ("no_bupot", "=", no_bupot),
                    ("source", "=", self.source),
                ],
                limit=1,
            )
            if existing:
                lines.append("Skip duplicate: %s" % no_bupot)
                skipped += 1
                continue

            try:
                tarif = float(_text("Tarif") or 0)
            except ValueError:
                tarif = 0.0
            try:
                dpp = float(_text("DPP") or 0)
            except ValueError:
                dpp = 0.0
            try:
                pph = float(_text("PPhTerpotong") or 0)
            except ValueError:
                pph = 0.0

            Bupot.create(
                {
                    "no_bupot": no_bupot,
                    "partner_id": partner.id,
                    "jenis_pph": jenis,
                    "tanggal_bupot": tanggal or fields.Date.context_today(self),
                    "period_year": (tanggal.year if tanggal else fields.Date.context_today(self).year),
                    "period_month": (tanggal.month if tanggal else fields.Date.context_today(self).month),
                    "source": self.source,
                    "tarif": tarif,
                    "dpp": dpp,
                    "pph_terpotong": pph,
                    "state": "confirmed",
                }
            )
            created += 1
            lines.append("OK: %s" % no_bupot)

        return created, skipped, lines

    # ----- Invoice (faktur) import — fills NSFP/status on matched moves -----
    def _import_invoices(self, root) -> tuple[int, int, list[str]]:
        updated = 0
        skipped = 0
        lines: list[str] = []
        Move = self.env["account.move"]

        for n in root.xpath(".//*[local-name()='Faktur']"):

            def _text(tag: str) -> str:
                el = n.find(f".//{{*}}{tag}")
                if el is None:
                    el = n.find(f".//{tag}")
                return (el.text or "").strip() if el is not None and el.text else ""

            inv_num = _text("InvoiceNumber")
            nsfp = _digits_only(_text("NSFP"))
            if not inv_num:
                skipped += 1
                lines.append("Skip: no InvoiceNumber in row")
                continue

            move = Move.search([("name", "=", inv_num)], limit=1)
            if not move:
                skipped += 1
                lines.append("Skip: no matching invoice %s" % inv_num)
                continue

            vals = {"x_custom_coretax_status": "approved" if nsfp else "submitted"}
            if nsfp:
                # No hard 17-digit gate — real Coretax faktur numbers vary; the
                # model constraint (_check_nsfp) does the permissive validation.
                vals["x_custom_nsfp"] = nsfp
                vals["x_custom_has_faktur_pajak"] = True
            status_code = _text("StatusCode")
            if status_code and status_code.isdigit() and len(status_code) <= 2:
                vals["x_custom_coretax_status_code"] = status_code.zfill(2)
            # Tanggal Faktur Pajak, when carried by the response XML.
            tgl_raw = _text("TanggalFaktur") or _text("InvoiceDate")
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
                try:
                    vals["x_custom_tanggal_faktur_pajak"] = datetime.strptime(tgl_raw, fmt).date()
                    break
                except ValueError:
                    continue
            move.write(vals)
            updated += 1
            lines.append("OK: %s -> NSFP=%s" % (inv_num, nsfp or "(none)"))

        return updated, skipped, lines

    # ----- Audit log -----
    def _audit_log_import(self, created: int, skipped: int) -> None:
        cr = self.env.cr
        payload = json.dumps(
            {
                "document_type": self.document_type,
                "filename": self.xml_filename,
                "source": self.source,
                "created": created,
                "skipped": skipped,
            }
        )
        cr.execute(
            """
            INSERT INTO pdp.audit_log
                (actor_user_id, actor_login, tenant_db, model_name, res_id,
                 action, field_changes, classification, reason)
            VALUES (%s, %s, %s, %s, %s, 'xml_import', %s::jsonb, 'financial', %s)
            """,
            (
                self.env.uid,
                self.env.user.login,
                cr.dbname,
                self._name,
                self.id,
                payload,
                "Coretax XML import — %s (%d created, %d skipped)" % (self.document_type, created, skipped),
            ),
        )
