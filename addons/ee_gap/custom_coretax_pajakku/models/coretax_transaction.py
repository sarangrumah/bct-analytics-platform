# -*- coding: utf-8 -*-
"""Transaction ledger for every Pajakku API submission.

Every ``submit_xml`` call materialises one row; subsequent ``query_nsfp``
and ``download_response`` calls update the same row. Persisting state
on a dedicated table (rather than only as fields on ``account.move``)
lets ops debug failed submissions, retry, and reconcile by transaction
UUID independent of the underlying document.
"""

from __future__ import annotations

from odoo import _, api, fields, models


TRANSACTION_TYPES = [
    ("efaktur_keluaran", "e-Faktur Keluaran"),
    ("efaktur_masukan", "e-Faktur Masukan"),
    ("bupot_pph21", "Bupot PPh 21"),
    ("bupot_pph23", "Bupot PPh 23"),
    ("bupot_pph26", "Bupot PPh 26"),
    ("bupot_pph42", "Bupot PPh 4(2)"),
    ("bupot_unifikasi", "Bupot Unifikasi"),
]

STATES = [
    ("queued", "Queued"),  # local — not yet sent
    ("submitting", "Submitting"),  # in-flight
    ("submitted", "Submitted (waiting DJP)"),
    ("approved", "Approved"),
    ("rejected", "Rejected"),
    ("error", "Error"),  # transport / auth failure
]


class CoretaxTransaction(models.Model):
    _name = "custom.coretax.transaction"
    _description = "Pajakku Submission Transaction"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc"
    _rec_name = "name"

    name = fields.Char(compute="_compute_name", store=True)

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)
    config_id = fields.Many2one("custom.coretax.config", required=True, ondelete="restrict")

    transaction_type = fields.Selection(TRANSACTION_TYPES, required=True, index=True)

    # External UUID returned by Pajakku on submit.
    external_uuid = fields.Char(string="Pajakku UUID", readonly=True, index=True, copy=False)

    # Origin document (one of these is set depending on transaction_type)
    account_move_id = fields.Many2one("account.move", string="Source Invoice", ondelete="set null")
    bukti_potong_id = fields.Many2one(
        "custom.coretax.bukti.potong",
        string="Source Bukti Potong",
        ondelete="set null",
    )

    # Payload + response (kept for audit + reprocessing)
    payload = fields.Binary(attachment=True, string="Submitted XML")
    payload_filename = fields.Char()
    response_xml = fields.Binary(attachment=True, string="DJP Response XML")
    response_filename = fields.Char()
    response_pdf = fields.Binary(attachment=True, string="Approval PDF")
    response_pdf_filename = fields.Char()

    # NSFP-style identifiers issued by DJP via Pajakku
    nsfp = fields.Char(string="NSFP / No. Bupot", readonly=True, tracking=True)
    djp_status_code = fields.Char(string="DJP Status Code", readonly=True)
    djp_message = fields.Text(string="DJP Message", readonly=True)

    state = fields.Selection(STATES, default="queued", required=True, tracking=True, index=True)

    submitted_at = fields.Datetime(readonly=True)
    last_polled_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)
    retry_count = fields.Integer(default=0, readonly=True)
    last_error = fields.Text(readonly=True)

    @api.depends("transaction_type", "external_uuid", "create_date")
    def _compute_name(self):
        for rec in self:
            ttype = dict(TRANSACTION_TYPES).get(rec.transaction_type, "Tx")
            ref = rec.external_uuid or (f"PENDING-{rec.id}" if rec.id else "NEW")
            rec.name = f"{ttype} / {ref}"

    # ----- Audit classification -----
    def _pdp_audit_classification(self):
        return "financial"

    # ----- State helpers -----

    def mark_submitting(self):
        self.write(
            {
                "state": "submitting",
                "submitted_at": fields.Datetime.now(),
            }
        )

    def mark_submitted(self, external_uuid: str, response_xml: bytes | None = None):
        vals = {"state": "submitted", "external_uuid": external_uuid}
        if response_xml:
            import base64

            vals["response_xml"] = base64.b64encode(response_xml)
            vals["response_filename"] = f"{external_uuid}-submit-ack.xml"
        self.write(vals)
        self._pdp_audit_write("coretax_pajakku_submitted", self.id, {"uuid": external_uuid})

    def mark_approved(self, nsfp: str, response_pdf: bytes | None = None):
        import base64

        vals = {
            "state": "approved",
            "nsfp": nsfp,
            "completed_at": fields.Datetime.now(),
        }
        if response_pdf:
            vals["response_pdf"] = base64.b64encode(response_pdf)
            vals["response_pdf_filename"] = f"{self.external_uuid}-approval.pdf"
        self.write(vals)
        # Push NSFP back to the source document (custom_coretax extends account.move with x_custom_nsfp / x_custom_coretax_status)
        if self.account_move_id and hasattr(self.account_move_id, "x_custom_nsfp"):
            self.account_move_id.sudo().write({"x_custom_nsfp": nsfp})
            if hasattr(self.account_move_id, "x_custom_coretax_status"):
                self.account_move_id.sudo().write({"x_custom_coretax_status": "approved"})
        if self.bukti_potong_id and hasattr(self.bukti_potong_id, "no_bupot"):
            self.bukti_potong_id.sudo().write({"no_bupot": nsfp})
        self._pdp_audit_write("coretax_pajakku_approved", self.id, {"uuid": self.external_uuid, "nsfp": nsfp})

    def mark_rejected(self, status_code: str, message: str):
        self.write(
            {
                "state": "rejected",
                "djp_status_code": status_code,
                "djp_message": message,
                "completed_at": fields.Datetime.now(),
            }
        )
        if self.account_move_id and hasattr(self.account_move_id, "coretax_status"):
            self.account_move_id.sudo().write({"coretax_status": "rejected_djp"})
        self.message_post(
            body=_("DJP rejected this submission: <b>%(code)s</b> — %(msg)s", code=status_code, msg=message),
        )
        self._pdp_audit_write("coretax_pajakku_rejected", self.id, {"uuid": self.external_uuid, "code": status_code})

    def mark_error(self, error: str, increment_retry: bool = True):
        vals = {"state": "error", "last_error": error[:2000]}
        if increment_retry:
            vals["retry_count"] = self.retry_count + 1
        self.write(vals)
        self._pdp_audit_write("coretax_pajakku_error", self.id, {"retry_count": self.retry_count, "error": error[:200]})

    def action_retry(self):
        """Re-queue an errored / rejected transaction for resubmission by the cron."""
        for rec in self:
            if rec.state not in ("error", "rejected"):
                continue
            rec.write({"state": "queued", "last_error": False})
        return True
