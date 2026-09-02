# -*- coding: utf-8 -*-
"""Bukti Potong (withholding tax slip) record.

Holds both inbound (received from counterparties) and outbound (issued
by this taxpayer) bupot evidence, normalised across the unified Coretax
PPh templates introduced in PER-11/PJ/2025.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CoretaxBuktiPotong(models.Model):
    _name = "custom.coretax.bukti.potong"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Custom Coretax — Bukti Potong (PPh)"
    _order = "tanggal_bupot desc, id desc"
    _rec_name = "no_bupot"

    no_bupot = fields.Char(string="No. Bukti Potong", required=True, index=True, copy=False)
    partner_id = fields.Many2one("res.partner", string="Counterparty", required=True, index=True)

    jenis_pph = fields.Selection(
        selection=[
            ("21", "PPh 21 (Karyawan)"),
            ("23", "PPh 23 (Jasa)"),
            ("26", "PPh 26 (Subjek LN)"),
            ("4_2", "PPh 4 ayat (2) — Final"),
            ("15", "PPh 15"),
            ("22", "PPh 22"),
        ],
        string="Jenis PPh",
        required=True,
        index=True,
    )

    tarif = fields.Float(string="Tarif (%)", digits=(6, 4), help="Withholding rate in percent, e.g. 2.0 for 2%.")
    dpp = fields.Monetary(string="Dasar Pengenaan Pajak", currency_field="currency_id")
    pph_terpotong = fields.Monetary(string="PPh Terpotong", currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.ref("base.IDR", raise_if_not_found=False) or self.env.company.currency_id,
        required=True,
    )

    tanggal_bupot = fields.Date(string="Tanggal Bupot", required=True, index=True)
    period_year = fields.Integer(string="Period Year", required=True, index=True)
    period_month = fields.Integer(string="Period Month", required=True, index=True)

    source = fields.Selection(
        selection=[
            ("received", "Received (Pemotong = Counterparty)"),
            ("issued", "Issued (Pemotong = This Taxpayer)"),
        ],
        string="Source",
        required=True,
        default="issued",
        index=True,
    )

    attachment_id = fields.Many2one("ir.attachment", string="Original Document")
    account_move_id = fields.Many2one("account.move", string="Linked Invoice/Bill", ondelete="set null", index=True)

    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("exported", "Exported"),
            ("submitted", "Submitted to Coretax"),
            ("approved", "Approved"),
            ("cancelled", "Cancelled"),
        ],
        string="State",
        default="draft",
        tracking=True,
        required=True,
        index=True,
    )

    notes = fields.Text(string="Notes")

    _no_bupot_unique_per_source = models.Constraint(
        "unique(no_bupot, source)",
        "Bukti Potong number must be unique per source (issued/received).",
    )

    @api.constrains("period_month")
    def _check_period_month(self):
        for rec in self:
            if not (1 <= rec.period_month <= 12):
                raise ValidationError(_("Period month must be between 1 and 12."))

    @api.constrains("period_year")
    def _check_period_year(self):
        for rec in self:
            if not (2000 <= rec.period_year <= 2100):
                raise ValidationError(_("Period year must be a 4-digit year."))

    # --- Sync the simple bukti-potong fields back onto the linked move ------
    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.account_move_id._sync_simple_bupot_from_records()
        return records

    def write(self, vals):
        # Capture the previously-linked moves so an unlink/relink re-syncs both.
        previous_moves = self.account_move_id
        res = super().write(vals)
        if {"account_move_id", "state", "no_bupot", "tanggal_bupot"} & set(vals):
            (previous_moves | self.account_move_id)._sync_simple_bupot_from_records()
        return res

    def action_confirm(self):
        # write() triggers _sync_simple_bupot_from_records via the 'state' key.
        self.write({"state": "confirmed"})

    def action_cancel(self):
        self.write({"state": "cancelled"})

    def action_draft(self):
        self.write({"state": "draft"})
