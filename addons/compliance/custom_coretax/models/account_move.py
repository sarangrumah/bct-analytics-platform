# -*- coding: utf-8 -*-
"""Account move extensions for Coretax NSFP lifecycle."""

from __future__ import annotations

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Permissive faktur-number check: real Coretax faktur numbers vary in
# length/format (and may carry separators). We strip non-alphanumerics and
# require 10–25 alphanumeric chars so genuine typos are caught but legitimate
# values never hard-fail an Import update.
_NSFP_RE = re.compile(r"^[0-9A-Za-z]{10,25}$")


class AccountMove(models.Model):
    _inherit = "account.move"

    # Coretax submission state (independent of accounting state)
    x_custom_coretax_status = fields.Selection(
        selection=[
            ("draft", "Draft (not submitted)"),
            ("submitted", "Submitted to Coretax"),
            ("approved", "Approved by DJP"),
            ("rejected_djp", "Rejected by DJP"),
        ],
        string="Coretax Status",
        default="draft",
        tracking=True,
        copy=False,
    )

    # No. Faktur Pajak / NSFP assigned by DJP after the faktur is issued on
    # Coretax. `size` is intentionally unbounded — real Coretax faktur numbers
    # vary in length/format and the field must stay importable (no hard length
    # cap that could block an Import update).
    x_custom_nsfp = fields.Char(
        string="No. Faktur Pajak / NSFP",
        copy=False,
        tracking=True,
        index=True,
        help="Nomor Seri / Nomor Faktur Pajak dari Coretax. Diisi setelah faktur "
        "terbit/diterima (dari portal/ASPP DJP). Dapat diisi manual maupun via "
        "Import update.",
    )

    x_custom_coretax_status_code = fields.Selection(
        selection=[
            ("00", "00 — Normal"),
            ("01", "01 — Pengganti 1"),
            ("02", "02 — Pengganti 2"),
            ("03", "03 — Pengganti 3"),
            ("04", "04 — Pengganti 4"),
            ("05", "05 — Pengganti 5"),
            ("06", "06 — Pengganti 6"),
            ("07", "07 — Pengganti 7"),
            ("08", "08 — Pengganti 8"),
            ("09", "09 — Pengganti 9"),
        ],
        string="Coretax Status Code",
        default="00",
        help="Faktur status code (00 normal, 01..09 = pengganti N).",
    )

    x_custom_coretax_submission_uuid = fields.Char(
        string="Coretax Submission UUID",
        copy=False,
        help="Reference returned by the DJP Coretax portal / ASPP after submission.",
    )

    x_custom_coretax_response_attach_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Coretax Response Attachment",
        copy=False,
        help="DJP response artifact (approval PDF / XML).",
    )

    # --- Faktur Pajak (surfaced for billing, import-friendly) ---------------
    x_custom_has_faktur_pajak = fields.Boolean(
        string="Memiliki Faktur Pajak",
        copy=False,
        index=True,
        help="Centang jika dokumen ini memiliki Faktur Pajak. Terisi otomatis "
        "saat No. Faktur Pajak diisi, namun tetap dapat diubah manual / lewat "
        "Import update.",
    )
    x_custom_tanggal_faktur_pajak = fields.Date(
        string="Tanggal Faktur Pajak",
        copy=False,
        tracking=True,
        help="Tanggal Faktur Pajak sesuai Coretax.",
    )

    # --- Bukti Potong (simple, import-friendly fields synced with bupot recs) -
    x_custom_has_bukti_potong = fields.Boolean(
        string="Ada Bukti Potong",
        copy=False,
        index=True,
        help="Centang jika atas dokumen ini terdapat Bukti Potong PPh. Terisi "
        "otomatis bila ada record Bukti Potong tertaut (confirmed), namun tetap "
        "dapat diubah manual / lewat Import update.",
    )
    x_custom_no_bukti_potong = fields.Char(
        string="No. Bukti Potong",
        copy=False,
        tracking=True,
        index=True,
        help="Nomor Bukti Potong PPh. Untuk multi-bupot, diisi nomor utama; lihat "
        "record Bukti Potong tertaut untuk daftar lengkap.",
    )
    x_custom_tanggal_bukti_potong = fields.Date(
        string="Tanggal Bukti Potong",
        copy=False,
        tracking=True,
        help="Tanggal Bukti Potong PPh.",
    )

    # Navigation to the formal bupot records (smart-button counter).
    x_custom_bukti_potong_ids = fields.One2many(
        comodel_name="custom.coretax.bukti.potong",
        inverse_name="account_move_id",
        string="Bukti Potong Terkait",
    )
    x_custom_bukti_potong_count = fields.Integer(
        string="Jumlah Bukti Potong",
        compute="_compute_bukti_potong_count",
    )

    @api.depends("x_custom_bukti_potong_ids")
    def _compute_bukti_potong_count(self):
        for rec in self:
            rec.x_custom_bukti_potong_count = len(rec.x_custom_bukti_potong_ids)

    @api.constrains("x_custom_nsfp")
    def _check_nsfp(self):
        for rec in self:
            if not rec.x_custom_nsfp:
                continue
            normalized = re.sub(r"[^0-9A-Za-z]", "", rec.x_custom_nsfp)
            if not _NSFP_RE.match(normalized):
                raise ValidationError(
                    _("No. Faktur Pajak tidak valid (got: %s). Harus 10–25 karakter alfanumerik.") % rec.x_custom_nsfp
                )

    @api.constrains("x_custom_coretax_status", "x_custom_nsfp")
    def _check_nsfp_required_on_approval(self):
        for rec in self:
            if rec.x_custom_coretax_status == "approved" and not rec.x_custom_nsfp:
                raise ValidationError(_("Cannot mark a move as Coretax-approved without an NSFP."))

    # --- Auto-flag helpers (Import-update safe) ----------------------------
    # The flags are plain stored Booleans so Import update can write them
    # directly. When a CSV/manual write supplies a number but omits the flag,
    # we auto-set it; when the flag IS supplied (e.g. an explicit False), the
    # caller's value is respected.
    @staticmethod
    def _autoset_tax_flags(vals):
        if vals.get("x_custom_nsfp") and "x_custom_has_faktur_pajak" not in vals:
            vals["x_custom_has_faktur_pajak"] = True
        if vals.get("x_custom_no_bukti_potong") and "x_custom_has_bukti_potong" not in vals:
            vals["x_custom_has_bukti_potong"] = True
        return vals

    @api.onchange("x_custom_nsfp")
    def _onchange_nsfp_sets_flag(self):
        if self.x_custom_nsfp and not self.x_custom_has_faktur_pajak:
            self.x_custom_has_faktur_pajak = True

    @api.onchange("x_custom_no_bukti_potong")
    def _onchange_no_bukti_potong_sets_flag(self):
        if self.x_custom_no_bukti_potong and not self.x_custom_has_bukti_potong:
            self.x_custom_has_bukti_potong = True

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._autoset_tax_flags(vals)
        return super().create(vals_list)

    def write(self, vals):
        if "x_custom_nsfp" in vals or "x_custom_no_bukti_potong" in vals:
            vals = self._autoset_tax_flags(dict(vals))
        return super().write(vals)

    def _sync_simple_bupot_from_records(self):
        """Back-fill the simple bukti-potong fields from the newest confirmed
        linked bupot record, WITHOUT overwriting values already set (manual
        entry / CSV import always wins)."""
        confirmed_states = ("confirmed", "exported", "submitted", "approved")
        for rec in self:
            bupots = rec.x_custom_bukti_potong_ids.filtered(lambda b: b.state in confirmed_states)
            vals = {}
            if (rec.x_custom_bukti_potong_ids or bupots) and not rec.x_custom_has_bukti_potong:
                vals["x_custom_has_bukti_potong"] = True
            if bupots:
                primary = bupots.sorted(lambda b: (b.tanggal_bupot or fields.Date.today(), b.id), reverse=True)[:1]
                if not rec.x_custom_no_bukti_potong and primary.no_bupot:
                    vals["x_custom_no_bukti_potong"] = primary.no_bupot
                if not rec.x_custom_tanggal_bukti_potong and primary.tanggal_bupot:
                    vals["x_custom_tanggal_bukti_potong"] = primary.tanggal_bupot
            if vals:
                rec.write(vals)

    def action_view_bukti_potong(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bukti Potong Terkait"),
            "res_model": "custom.coretax.bukti.potong",
            "view_mode": "list,form",
            "domain": [("account_move_id", "=", self.id)],
            "context": {"default_account_move_id": self.id},
        }
