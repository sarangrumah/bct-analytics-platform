# -*- coding: utf-8 -*-
"""Pemotong-side identity required by the Coretax import templates.

Every bupot template repeats the same four header columns for the withholding
agent — NPWP Pemotong, NITKU Pemotong, NPWP Penandatangan, User Id — so they
live on the company rather than being re-typed per export.
"""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

NITKU_6_RE = re.compile(r"^\d{6}$")
SIGNER_RE = re.compile(r"^\d{15,16}$")


class ResCompany(models.Model):
    _inherit = "res.company"

    x_custom_nitku_suffix = fields.Char(
        string="NITKU Pemotong (6 Digit)",
        default="000000",
        help="Nomor Identitas Tempat Kegiatan Usaha of this withholding agent, "
        "6 digits. '000000' denotes the head office.",
    )
    x_custom_npwp_penandatangan = fields.Char(
        string="NPWP Penandatangan",
        help="Nomor identitas of the person signing the bukti potong. Emitted "
        "verbatim in the 'NPWP Penandatangan' column of every bupot export.",
    )
    x_custom_coretax_user_id = fields.Char(
        string="Coretax User Id",
        help="User Id registered on Coretax, emitted in the 'User Id' column.",
    )

    def _check_coretax_pemotong(self, require_signer=True):
        """Guard the export wizards: refuse to emit a file DJP will reject.

        ``require_signer`` exists because only the bupot layouts carry an "NPWP
        Penandatangan" column. e-Faktur Keluaran (FK/OF) and Retur Masukan take
        the pemotong's NPWP and NITKU but never the signer, so demanding it
        there blocks a perfectly valid export on a field the file will not hold.
        Defaults to True: a caller that has not thought about it gets the strict
        check.
        """
        self.ensure_one()
        partner = self.partner_id
        npwp = (partner.x_custom_npwp or "").replace(".", "").replace("-", "")
        missing = []
        if not npwp:
            missing.append(_("NPWP Pemotong (pada partner perusahaan)"))
        if not self.x_custom_nitku_suffix:
            missing.append(_("NITKU Pemotong (6 Digit)"))
        if require_signer and not self.x_custom_npwp_penandatangan:
            missing.append(_("NPWP Penandatangan"))
        if missing:
            raise ValidationError(
                _(
                    "Identitas pemotong belum lengkap pada perusahaan %(company)s.\nLengkapi dahulu: %(missing)s",
                    company=self.display_name,
                    missing="\n  - ".join([""] + missing),
                )
            )
        return npwp

    @api.constrains("x_custom_nitku_suffix")
    def _check_nitku_suffix(self):
        for rec in self:
            if rec.x_custom_nitku_suffix and not NITKU_6_RE.match(rec.x_custom_nitku_suffix):
                raise ValidationError(_("NITKU Pemotong harus 6 digit angka."))

    @api.constrains("x_custom_npwp_penandatangan")
    def _check_penandatangan(self):
        for rec in self:
            signer = (rec.x_custom_npwp_penandatangan or "").replace(".", "").replace("-", "")
            if signer and not SIGNER_RE.match(signer):
                raise ValidationError(_("NPWP Penandatangan harus 15 atau 16 digit angka."))
