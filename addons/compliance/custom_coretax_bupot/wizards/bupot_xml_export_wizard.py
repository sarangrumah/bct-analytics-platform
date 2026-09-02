# -*- coding: utf-8 -*-
"""Generate e-Bupot Unifikasi v2 XML for upload to DJP Coretax.

The schema produced here mirrors the DJP Coretax "Bukti Potong Unifikasi"
template (kode SPT PPh Unifikasi). The output is attached as an
``ir.attachment`` (and exposed as a download via the wizard form) so it
survives the user session.
"""

from __future__ import annotations

import base64
from io import BytesIO
from xml.sax.saxutils import escape

from odoo import _, fields, models
from odoo.exceptions import UserError


class CustomBupotXmlExportWizard(models.TransientModel):
    _name = "custom.bupot.xml.export.wizard"
    _description = "Export Bukti Potong Unifikasi XML (Coretax v2)"

    bupot_id = fields.Many2one(
        comodel_name="custom.bupot.unifikasi",
        required=True,
    )
    attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        readonly=True,
    )
    output_file = fields.Binary(readonly=True)
    output_file_name = fields.Char(readonly=True)

    def action_generate(self):
        self.ensure_one()
        period = self.bupot_id
        if not period.line_ids:
            raise UserError(_("No bupot lines in this period."))
        xml_bytes = self._build_xml(period)
        fname = f"BPU_{period.company_id.id}_{period.year}_{int(period.month):02d}.xml"
        attachment = self.env["ir.attachment"].create(
            {
                "name": fname,
                "datas": base64.b64encode(xml_bytes),
                "res_model": period._name,
                "res_id": period.id,
                "type": "binary",
                "mimetype": "application/xml",
            }
        )
        self.attachment_id = attachment.id
        self.output_file = attachment.datas
        self.output_file_name = fname
        # Promote header state on first successful export.
        if period.state == "draft":
            period.state = "generated"
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }

    def _build_xml(self, period) -> bytes:
        out = BytesIO()
        out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        out.write(
            (
                f'<BuktiPotongUnifikasi version="2.0" '
                f'period="{period.year}-{int(period.month):02d}" '
                f'company="{escape(period.company_id.name or "")}">\n'
            ).encode()
        )
        for line in period.line_ids:
            out.write(b"  <Bupot>\n")
            out.write(f"    <InternalRef>{escape(line.internal_ref or '')}</InternalRef>\n".encode())
            out.write(f"    <NomorBuktiPotong>{escape(line.bupot_number or '')}</NomorBuktiPotong>\n".encode())
            out.write(f"    <JenisPPh>{line.pph_type}</JenisPPh>\n".encode())
            out.write(f"    <TanggalTransaksi>{line.transaction_date or ''}</TanggalTransaksi>\n".encode())
            out.write(b"    <Dipotong>\n")
            out.write(f"      <NPWP>{escape(line.cuttee_npwp or '')}</NPWP>\n".encode())
            out.write(f"      <NITKU>{escape(line.cuttee_nitku or '')}</NITKU>\n".encode())
            out.write(f"      <Nama>{escape(line.cuttee_name or '')}</Nama>\n".encode())
            out.write(b"    </Dipotong>\n")
            out.write(f"    <JumlahBruto>{line.gross_amount:.2f}</JumlahBruto>\n".encode())
            out.write(f"    <DPP>{line.dpp_amount:.2f}</DPP>\n".encode())
            out.write(f"    <Tarif>{line.rate:.4f}</Tarif>\n".encode())
            out.write(f"    <JumlahPPh>{line.withheld_amount:.2f}</JumlahPPh>\n".encode())
            doc_ref = ""
            if line.doc_ref:
                doc_ref = f"{line.doc_ref._name},{line.doc_ref.id}"
            out.write(f"    <DocRef>{escape(doc_ref)}</DocRef>\n".encode())
            out.write(b"  </Bupot>\n")
        out.write(b"</BuktiPotongUnifikasi>\n")
        return out.getvalue()
