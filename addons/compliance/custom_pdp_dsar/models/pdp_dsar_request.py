# -*- coding: utf-8 -*-
"""Data Subject Access Request (DSAR) model.

Implements the UU 27/2022 right of access:
- gather all PII-classified records for a subject across models
- deliver a ZIP attachment with the dossier
- (optional) anonymize the subject on request
- log every action into pdp.audit_log via custom_pdp_audit
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PdpDsarRequest(models.Model):
    _name = "pdp.dsar.request"
    _description = "PDP Data Subject Access Request"
    _order = "requested_at desc, id desc"
    _inherit = ["pdp.audited.mixin", "mail.thread"]

    name = fields.Char(default=lambda s: s._default_name(), required=True, copy=False, readonly=True)
    subject_email = fields.Char(required=True, tracking=True)
    subject_nik = fields.Char(string="Subject NIK (KTP)", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Resolved Partner", index=True, tracking=True)
    state = fields.Selection(
        [
            ("received", "Received"),
            ("verifying", "Verifying Identity"),
            ("gathering", "Gathering Data"),
            ("delivered", "Delivered"),
            ("rejected", "Rejected"),
        ],
        default="received",
        tracking=True,
        required=True,
    )
    request_kind = fields.Selection(
        [
            ("access", "Access"),
            ("erasure", "Erasure / Anonymize"),
            ("rectify", "Rectification"),
            ("portability", "Portability"),
        ],
        default="access",
        required=True,
    )
    requested_at = fields.Datetime(default=fields.Datetime.now, required=True)
    delivered_at = fields.Datetime()
    response_attachment_id = fields.Many2one("ir.attachment", string="Response Dossier", readonly=True)
    rejection_reason = fields.Text()
    ai_summary = fields.Text(readonly=True)

    @api.model
    def _default_name(self):
        return "DSAR/%s" % datetime.utcnow().strftime("%Y%m%d-%H%M%S")

    # ---------- state transitions ----------

    def action_verify(self):
        for r in self:
            r.state = "verifying"
            r._pdp_audit_write("dsar", r.id, {"transition": "verifying"}, reason="DSAR moved to verifying")

    def action_gather(self):
        for r in self:
            r.state = "gathering"
            data = self._gather_subject_data(r.partner_id.id if r.partner_id else None)
            zip_bytes = self._build_zip(data)
            try:
                summary = self._ai_summary(data)
                r.ai_summary = summary
            except Exception as e:
                _logger.info("DSAR AI summary skipped: %s", e)
                r.ai_summary = None
            att = self.env["ir.attachment"].create(
                {
                    "name": f"{r.name}.zip",
                    "type": "binary",
                    "datas": base64.b64encode(zip_bytes),
                    "res_model": r._name,
                    "res_id": r.id,
                    "mimetype": "application/zip",
                }
            )
            r.response_attachment_id = att.id
            r.state = "delivered"
            r.delivered_at = fields.Datetime.now()
            r._pdp_audit_write(
                "dsar", r.id, {"transition": "delivered", "attachment_id": att.id}, reason="DSAR dossier delivered"
            )

    def action_reject(self):
        for r in self:
            r.state = "rejected"
            r._pdp_audit_write("dsar", r.id, {"transition": "rejected"}, reason=r.rejection_reason or "DSAR rejected")

    def action_anonymize(self):
        for r in self:
            if not r.partner_id:
                raise UserError("Resolve the subject partner before anonymizing.")
            self._anonymize_subject(r.partner_id.id)
            r._pdp_audit_write(
                "dsar",
                r.id,
                {"transition": "anonymized", "partner_id": r.partner_id.id},
                reason="DSAR-driven anonymization",
            )

    # ---------- helpers ----------

    @api.model
    def _gather_subject_data(self, partner_id: int | None) -> dict:
        """Aggregate every PII-classified record across all models for the subject.

        Returns: {model_name: [ {field: value, ...}, ... ], ...}
        """
        if not partner_id:
            return {}
        out: dict[str, list[dict]] = {}
        Field = self.env["ir.model.fields"].sudo()
        # Find models that have at least one PII-classified field
        pii_fields = Field.search([("x_pdp_classification_id", "!=", False)])
        by_model: dict[str, list[str]] = {}
        for f in pii_fields:
            by_model.setdefault(f.model, []).append(f.name)

        for model_name, fnames in by_model.items():
            if model_name not in self.env:
                continue
            Model = self.env[model_name].sudo()
            domain = []
            # Heuristic: locate the partner linkage on each model
            if model_name == "res.partner":
                domain = [("id", "=", partner_id)]
            elif "partner_id" in Model._fields:
                domain = [("partner_id", "=", partner_id)]
            elif "user_id" in Model._fields and "partner_id" in self.env["res.users"]._fields:
                domain = [("user_id.partner_id", "=", partner_id)]
            else:
                continue
            try:
                rows = Model.search_read(domain, fnames + ["id"], limit=10000)
            except Exception as e:
                _logger.info("DSAR: skipping %s: %s", model_name, e)
                continue
            if rows:
                out[model_name] = rows
        return out

    @api.model
    def _build_zip(self, data: dict) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(
                    {"generated_at": datetime.utcnow().isoformat() + "Z", "models": list(data.keys())},
                    indent=2,
                ),
            )
            for model_name, rows in data.items():
                safe = model_name.replace(".", "_")
                zf.writestr(f"{safe}.json", json.dumps(rows, default=str, indent=2))
        return buf.getvalue()

    @api.model
    def _ai_summary(self, data: dict) -> str | None:
        if not data:
            return None
        try:
            res = self.env["custom.ai"]._chat(
                system=(
                    "You produce concise, human-readable summaries of personal data "
                    "dossiers for Indonesian UU PDP DSAR responses. Output Indonesian. "
                    "Do not invent data; only summarize what is provided."
                ),
                messages=[
                    {
                        "role": "user",
                        "content": "Buat ringkasan dossier DSAR berikut (jumlah record per model, kategori data): "
                        + json.dumps({k: len(v) for k, v in data.items()}),
                    }
                ],
                quality="fast",
                max_tokens=512,
                temperature=0.2,
            )
            return (res or {}).get("text") or (res or {}).get("content") or json.dumps(res)[:1024]
        except Exception as e:
            _logger.info("custom.ai unavailable for DSAR summary: %s", e)
            return None

    @api.model
    def _anonymize_subject(self, partner_id: int) -> bool:
        """Overwrite PII-classified fields with hash-placeholders. Does NOT unlink."""
        if not partner_id:
            return False
        Field = self.env["ir.model.fields"].sudo()
        pii_fields = Field.search([("x_pdp_classification_id", "!=", False)])
        by_model: dict[str, list[str]] = {}
        for f in pii_fields:
            by_model.setdefault(f.model, []).append(f.name)

        digest = hashlib.sha256(f"pdp-anon:{partner_id}".encode()).hexdigest()[:16]
        placeholder = f"ANON-{digest}"

        for model_name, fnames in by_model.items():
            if model_name not in self.env:
                continue
            Model = self.env[model_name].sudo()
            if model_name == "res.partner":
                domain = [("id", "=", partner_id)]
            elif "partner_id" in Model._fields:
                domain = [("partner_id", "=", partner_id)]
            else:
                continue
            recs = Model.search(domain)
            if not recs:
                continue
            vals = {}
            for fn in fnames:
                f = Model._fields.get(fn)
                if not f:
                    continue
                if f.type in ("char", "text", "html"):
                    vals[fn] = placeholder
                elif f.type == "binary":
                    vals[fn] = False
            if vals:
                try:
                    recs.write(vals)
                except Exception as e:
                    _logger.warning("DSAR anonymize: %s failed: %s", model_name, e)
        return True
