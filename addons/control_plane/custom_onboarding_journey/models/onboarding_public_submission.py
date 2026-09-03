# -*- coding: utf-8 -*-
"""Public intake landing zone.

Raw submissions land here first. A BA/CSM reviews them and clicks
"Promote to Journey" to materialize an ``onboarding.journey`` record.
"""

from __future__ import annotations

import json
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class OnboardingPublicSubmission(models.Model):
    _name = "onboarding.public.submission"
    _description = "Public Onboarding Submission (raw inbox)"
    _order = "submitted_at desc"

    name = fields.Char(
        compute="_compute_name",
        store=True,
    )
    raw_payload_json = fields.Text(required=True)
    public_token = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: secrets.token_urlsafe(24),
    )
    source_ip_hash = fields.Char(
        help="SHA-256 of the source IP (no raw IP stored, PDP-friendly).",
        index=True,
    )
    submitted_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    status = fields.Selection(
        [
            ("submitted", "Submitted"),
            ("promoted", "Promoted"),
            ("rejected", "Rejected"),
        ],
        default="submitted",
        required=True,
        index=True,
    )
    journey_id = fields.Many2one(
        "onboarding.journey",
        ondelete="set null",
        copy=False,
    )
    rejection_reason = fields.Text()

    _public_token_uniq = models.Constraint(
        "unique(public_token)",
        "Submission token must be unique.",
    )

    # ------------------------------------------------------------------ API
    @api.model
    def create_from_payload(self, payload):
        """Create a submission from a JSON payload (called by orchestrator /v1/intake).

        Returns ``{token, status_url, id}`` so the public landing can
        immediately show the customer a status link.
        """
        import hashlib

        if not isinstance(payload, dict):
            raise UserError(_("payload must be a dict"))
        if not payload.get("company_name"):
            raise UserError(_("company_name is required"))
        source_ip = payload.pop("source_ip", None)
        ip_hash = hashlib.sha256(source_ip.encode("utf-8")).hexdigest()[:32] if source_ip else False
        rec = self.sudo().create(
            {
                "raw_payload_json": json.dumps(payload),
                "source_ip_hash": ip_hash,
            }
        )
        return {
            "id": rec.id,
            "token": rec.public_token,
            "status_url": f"/onboarding/public/status/{rec.public_token}",
        }

    @api.depends("raw_payload_json", "submitted_at")
    def _compute_name(self):
        for rec in self:
            label = "Submission"
            try:
                data = json.loads(rec.raw_payload_json or "{}")
                label = data.get("partner_name") or data.get("company_name") or label
            except Exception:
                pass
            rec.name = f"{label} @ {rec.submitted_at or ''}"

    # ------------------------------------------------------------------ actions
    def action_promote_to_journey(self):
        self.ensure_one()
        if self.status == "promoted" and self.journey_id:
            return self._open_journey()
        try:
            data = json.loads(self.raw_payload_json or "{}")
        except Exception as exc:
            raise UserError(_("Cannot parse submission payload: %s") % exc) from exc

        partner_name = data.get("partner_name") or data.get("company_name") or _("Unknown")
        partner_email = data.get("partner_email") or data.get("contact_email")
        partner_phone = data.get("contact_phone")
        Partner = self.env["res.partner"].sudo()  # nosemgrep: bct-odoo-sudo-on-tenant-scoped-model  # public intake has no logged-in user; partner search/create needs sudo; tenancy is per-database (dbfilter), not per-row
        partner = False
        if partner_email:
            partner = Partner.search([("email", "=", partner_email)], limit=1)
        if not partner:
            partner_vals = {"name": partner_name, "is_company": True}
            if partner_email:
                partner_vals["email"] = partner_email
            if partner_phone:
                partner_vals["phone"] = partner_phone
            partner = Partner.create(partner_vals)

        # Best-effort initial stage based on what the intake provided.
        has_brd = bool(data.get("brd_file_base64s"))
        initial_stage = "brd_uploaded" if has_brd else "intake"

        journey_vals = {
            "name": _("Onboarding - %s") % partner_name,
            "partner_id": partner.id,
            "stage": initial_stage,
            "company_profile_json": self.raw_payload_json,
        }
        if "vertical_target" in self.env["onboarding.journey"]._fields and data.get("vertical_target"):
            journey_vals["vertical_target"] = data["vertical_target"]

        journey = self.env["onboarding.journey"].sudo().create(journey_vals)

        # Extract any uploaded BRD files into ir.attachment + brd.document so
        # the AI analyzer has something to chew on.
        brd_files = data.get("brd_file_base64s") or []
        brd_filenames = data.get("brd_filenames") or []
        BrdDocument = self.env["brd.document"].sudo()
        Attachment = self.env["ir.attachment"].sudo()
        for idx, b64 in enumerate(brd_files):
            if not b64:
                continue
            try:
                # b64 might be a data URL ("data:application/...;base64,XXX")
                if isinstance(b64, str) and "," in b64 and b64.startswith("data:"):
                    b64 = b64.split(",", 1)[1]
                fname = (
                    brd_filenames[idx] if idx < len(brd_filenames) else None
                ) or f"BRD-{partner_name}-{idx + 1}.docx"
                att = Attachment.create(
                    {
                        "name": fname,
                        "datas": b64,
                        "res_model": "brd.document",
                        "res_id": 0,
                    }
                )
                doc_vals = {
                    "name": fname.rsplit(".", 1)[0],
                    "document_attachment_id": att.id,
                    "document_filename": fname,
                    "vertical_target_id": False,
                    "state": "draft",
                }
                if "journey_id" in BrdDocument._fields:
                    doc_vals["journey_id"] = journey.id
                if data.get("vertical_target") and "vertical_target" in BrdDocument._fields:
                    doc_vals["vertical_target"] = data["vertical_target"]
                if "company_profile_json" in BrdDocument._fields:
                    doc_vals["company_profile_json"] = json.dumps(
                        {
                            k: data.get(k)
                            for k in (
                                "company_name",
                                "contact_email",
                                "contact_phone",
                                "npwp",
                                "bank_name",
                                "bank_account",
                            )
                        }
                    )
                doc = BrdDocument.create(doc_vals)
                # Re-point attachment to the created BRD record so the Documents app picks it up.
                att.write({"res_id": doc.id})
            except Exception as e:
                _logger.warning("Failed to materialize BRD attachment #%d from submission %s: %s", idx, self.id, e)

        self.write({"status": "promoted", "journey_id": journey.id})
        return self._open_journey()

    def action_reject(self):
        for rec in self:
            rec.status = "rejected"
        return True

    def _open_journey(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "onboarding.journey",
            "res_id": self.journey_id.id,
            "view_mode": "form",
        }
