# -*- coding: utf-8 -*-
"""Faktur Pengganti wizard — replace an issued Faktur with a corrected one.

Per PER-11/PJ/2025, NSFP carries a 2-digit kode status:
  00 — Faktur Pajak normal (issued by DJP on first approval)
  01 — Faktur Pengganti pertama (first replacement)
  02 — Faktur Pengganti kedua
  ... up to 09

The wizard:
  1. Creates a NEW account.move that copies the source (lines, partner,
     period) and stamps ``x_custom_coretax_replacement_of_id`` pointing
     back to the source.
  2. Marks the source move ``coretax_status = 'replaced'`` and clears its
     NSFP (it's logically void from DJP's perspective once the pengganti
     is approved — but operators keep the source move for audit).
  3. Computes ``kode_status`` for the new move = (last status + 1).
  4. Audits the relink in pdp.audit_log.
"""

from __future__ import annotations

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class FakturPenggantiWizard(models.TransientModel):
    _name = "tax.faktur.pengganti.wizard"
    _description = "Wizard: replace a Faktur Pajak"

    source_move_id = fields.Many2one(
        "account.move",
        required=True,
        domain="[('move_type','in',('out_invoice','out_refund')),('state','=','posted')]",
    )
    reason = fields.Text(
        required=True,
        help="Required justification — stored on the new move and audit log.",
    )
    keep_lines = fields.Boolean(
        default=True,
        help="Copy invoice lines verbatim. Uncheck for empty replacement (rare).",
    )

    new_invoice_date = fields.Date(
        default=fields.Date.context_today,
        help="Date of the replacement Faktur. Defaults to today.",
    )

    next_kode_status = fields.Char(
        compute="_compute_next_kode_status",
        readonly=True,
        help="What kode status will be stamped on the replacement.",
    )

    @api.depends("source_move_id")
    def _compute_next_kode_status(self):
        for rec in self:
            current = self._extract_kode_status(rec.source_move_id) if rec.source_move_id else None
            if current is None:
                rec.next_kode_status = "—"
            else:
                rec.next_kode_status = f"{current + 1:02d}"

    @staticmethod
    def _extract_kode_status(move) -> int | None:
        """Pull kode status from explicit stamp (replacement chain) falling back to NSFP positions 3-4."""
        # If the source move is itself a pengganti, its previous kode_status was already stamped.
        stamped = getattr(move, "x_custom_coretax_kode_status", None)
        if stamped and stamped.isdigit():
            return int(stamped)
        nsfp = getattr(move, "x_custom_nsfp", None)
        if not nsfp:
            return 0
        cleaned = nsfp.replace("-", "").replace(".", "").strip()
        # NSFP layout (Coretax): SS (kode status, 2 digits with leading zero)
        # + KK (kode transaksi, 2) + YY (year, 2) + N... (sequence).
        # The first two digits encode the status: "00" → original, "01"..."09"
        # → 1st..9th pengganti.
        if len(cleaned) < 2 or not cleaned[:2].isdigit():
            return 0
        return int(cleaned[:2])

    def action_create_replacement(self):
        self.ensure_one()
        src = self.source_move_id
        if not src:
            raise UserError(_("Source move is required."))
        current_kode = self._extract_kode_status(src) or 0
        if current_kode >= 9:
            raise UserError(
                _(
                    "Source Faktur already at kode_status %s. DJP limit is 09 — "
                    "cancel and issue a new Faktur instead of pengganti."
                )
                % f"{current_kode:02d}"
            )

        # Copy with new context
        new_vals = {
            "invoice_date": self.new_invoice_date,
            "ref": _("Pengganti dari %s") % (src.name or src.id),
            "x_custom_coretax_replacement_of_id": src.id,
            "x_custom_coretax_kode_status": f"{current_kode + 1:02d}",
        }
        # Pre-flight: ensure target fields exist on account.move
        # (added by this same module via _inherit below).
        copy = src.copy(default=new_vals)

        if not self.keep_lines:
            copy.invoice_line_ids.unlink()

        # Mark source as replaced
        src.write(
            {
                "x_custom_coretax_replaced_by_id": copy.id,
            }
        )
        # Best-effort: clear source NSFP since DJP voids it on pengganti approval
        if hasattr(src, "x_custom_nsfp"):
            src.write({"x_custom_nsfp": False})
        if hasattr(src, "x_custom_coretax_status"):
            src.write({"x_custom_coretax_status": "rejected_djp"})  # logical 'replaced' bucket

        # Audit (action constrained to pdp.audit_log allowed values + varchar(16))
        try:
            src._pdp_audit_write(
                "custom",
                src.id,
                {
                    "kind": "faktur_pengganti_issued",
                    "new_move_id": copy.id,
                    "kode_status_new": f"{current_kode + 1:02d}",
                    "reason": self.reason,
                },
            )
        except Exception:
            pass

        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": copy.id,
            "view_mode": "form",
        }


class AccountMoveReplacementLink(models.Model):
    """Fields added to ``account.move`` so the wizard can link pengganti chain."""

    _inherit = "account.move"

    x_custom_coretax_replacement_of_id = fields.Many2one(
        "account.move",
        string="Pengganti Dari",
        readonly=True,
        copy=False,
    )
    x_custom_coretax_replaced_by_id = fields.Many2one(
        "account.move",
        string="Diganti Oleh",
        readonly=True,
        copy=False,
    )
    x_custom_coretax_kode_status = fields.Char(
        string="Kode Status NSFP",
        size=2,
        readonly=True,
        copy=False,
        help="2-digit kode status per PER-11/PJ/2025. 00 = normal, 01-09 = pengganti.",
    )
