# -*- coding: utf-8 -*-
"""One withholding line per (vendor bill line × resolved rule).

The line carries the base, tarif and resulting PPh amount. A corresponding
draft ``custom.coretax.bukti.potong`` is materialised so Coretax can
serialise it to XML when the operator runs the export wizard.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Kode Fasilitas, transcribed verbatim from the "List KOP & Kode Fasilitas"
# sheet of the DJP templates. Codes 1-6 come from the Unifikasi template; code
# 7 (SKD WPLN) exists only in the Non-Resident template, so the union is kept
# here and each exporter is responsible for emitting a code its own template
# accepts. '9' (Tanpa Fasilitas) is the default in practice.
FASILITAS_SELECTION = [
    ("1", "1 - SKB PPh Pasal 22"),
    ("2", "2 - SKB PPh Pasal 23"),
    ("3", "3 - SKB PPh PHTB"),
    ("4", "4 - DTP"),
    ("5", "5 - SKB PPh atas bunga deposito, dana pensiun, tabungan"),
    ("6", "6 - Suket PP23/PP52"),
    ("7", "7 - SKD WPLN (hanya Bupot Non-Resident)"),
    ("8", "8 - Fasilitas Lainnya"),
    ("9", "9 - Tanpa Fasilitas"),
]
# Only the SKD WPLN path is reliably rate-bearing: the treaty rate is the whole
# point of the claim and DJP rejects it blank. Observed client data fills Tarif
# Fasilitas on code 9 too, so this is deliberately not enforced elsewhere.
FASILITAS_NEEDS_TARIF = ("7",)


class WithholdingLine(models.Model):
    _name = "account.move.withholding.line"
    _description = "Account Move Withholding Line"
    _order = "move_id, id"

    move_id = fields.Many2one("account.move", required=True, ondelete="cascade", index=True)
    move_line_id = fields.Many2one("account.move.line", string="Source Move Line", ondelete="set null")
    rule_id = fields.Many2one("tax.withholding.rule", required=True, ondelete="restrict")
    category_id = fields.Many2one(related="rule_id.category_id", store=True, readonly=True)
    pph_kind = fields.Selection(related="rule_id.pph_kind", store=True, readonly=True)

    base_amount = fields.Monetary(required=True, currency_field="currency_id")
    tarif = fields.Float(required=True, digits=(6, 4))
    tax_amount = fields.Monetary(required=True, currency_field="currency_id")

    currency_id = fields.Many2one(related="move_id.currency_id", store=True)
    company_id = fields.Many2one(related="move_id.company_id", store=True)

    bupot_id = fields.Many2one(
        "custom.coretax.bukti.potong",
        string="Bukti Potong",
        ondelete="set null",
        readonly=True,
    )

    fasilitas_insentif = fields.Selection(
        FASILITAS_SELECTION,
        string="Fasilitas Insentif",
        default="9",
        required=True,
        help="Kode fasilitas emitted to Coretax. Defaults to 9 (Tanpa Fasilitas).",
    )
    nomor_sertifikat_insentif = fields.Char(
        string="Nomor Sertifikat Insentif",
        help="SKB / DTP / SKD WPLN certificate number backing the facility claim.",
    )
    tarif_fasilitas = fields.Float(
        string="Tarif Fasilitas",
        digits=(6, 2),
        help="Treaty or facility rate. Mandatory when Fasilitas Insentif is 7 or 8.",
    )
    norma_penghasilan_neto = fields.Float(
        string="Norma Penghasilan Neto (%)",
        digits=(6, 2),
        default=100.0,
        help="Deemed-profit percentage. Coretax expects 100 when no norma applies.",
    )

    @api.constrains("fasilitas_insentif", "tarif_fasilitas", "nomor_sertifikat_insentif")
    def _check_fasilitas(self):
        for rec in self:
            if rec.fasilitas_insentif in FASILITAS_NEEDS_TARIF and not rec.tarif_fasilitas:
                raise ValidationError(
                    _(
                        "Fasilitas Insentif %s mewajibkan Tarif Fasilitas diisi.",
                        rec.fasilitas_insentif,
                    )
                )
            if rec.fasilitas_insentif == "7" and not rec.nomor_sertifikat_insentif:
                raise ValidationError(_("Fasilitas 7 (SKD WPLN) mewajibkan Nomor Sertifikat Insentif diisi."))

    @api.model_create_multi
    def create(self, vals_list):
        recs = super().create(vals_list)
        recs._materialise_bupot()
        return recs

    def _materialise_bupot(self):
        Bupot = self.env["custom.coretax.bukti.potong"].sudo()
        for line in self:
            if line.bupot_id:
                continue
            partner = line.move_id.partner_id.commercial_partner_id
            try:
                bupot = Bupot.create(
                    {
                        "no_bupot": f"DRAFT-{line.move_id.name or line.move_id.id}-{line.id}",
                        "partner_id": partner.id,
                        # withholding_category.pph_kind uses "pph_23" style; bupot.jenis_pph
                        # expects the bare code ("23", "4_2"). Strip the prefix on write.
                        "jenis_pph": (line.pph_kind or "").removeprefix("pph_"),
                        "tarif": line.tarif,
                        "dpp": line.base_amount,
                        "pph_terpotong": line.tax_amount,
                        "currency_id": line.currency_id.id,
                        "tanggal_bupot": line.move_id.invoice_date or fields.Date.context_today(self),
                        "period_year": (line.move_id.invoice_date or fields.Date.context_today(self)).year,
                        "period_month": (line.move_id.invoice_date or fields.Date.context_today(self)).month,
                        "source": "issued",  # we are the cutter
                        "account_move_id": line.move_id.id,
                        "state": "draft",
                    }
                )
                line.bupot_id = bupot.id
            except Exception as e:
                _logger.exception("Failed to materialise bukti.potong for withholding line %s", line.id)
                # Withholding stays — operator can manually create the bupot
                line.move_id.message_post(body=_("Failed to auto-create Bukti Potong: %s") % e)
