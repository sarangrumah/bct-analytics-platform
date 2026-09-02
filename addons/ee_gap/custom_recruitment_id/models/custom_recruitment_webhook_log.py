# -*- coding: utf-8 -*-
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


WEBHOOK_SOURCES = [
    ("manual", "Manual"),
    ("jobstreet", "Jobstreet"),
    ("glints", "Glints"),
    ("linkedin", "LinkedIn"),
    ("kalibrr", "Kalibrr"),
    ("direct", "Direct"),
]


def _normalize_payload(source, data):
    """Map vendor-specific JSON shapes into a flat applicant vals dict.

    Supported shapes (best-effort):

    - Jobstreet:    {"candidate": {"full_name": ..., "email": ..., "phone": ...,
                                    "ref_id": ..., "job_ref": ...}}
    - Glints:       {"applicant": {"name": ..., "email": ..., "mobile": ...,
                                    "id": ..., "job_id": ...}}
    - LinkedIn:     {"applicant": {"firstName": ..., "lastName": ...,
                                    "emailAddress": ..., "phoneNumber": ...,
                                    "applicationId": ..., "jobPostingId": ...}}
    - generic:      {"name": ..., "email": ..., "phone": ..., "external_id": ...}
    """
    data = data or {}
    src = (source or "").lower()
    if src == "jobstreet":
        c = data.get("candidate") or {}
        return {
            "name": c.get("full_name") or c.get("name") or data.get("name"),
            "email": c.get("email") or data.get("email"),
            "phone": c.get("phone") or data.get("phone"),
            "external_id": c.get("ref_id") or c.get("id") or data.get("external_id"),
            "job_ref": c.get("job_ref") or data.get("job_ref"),
        }
    if src == "glints":
        a = data.get("applicant") or {}
        return {
            "name": a.get("name") or data.get("name"),
            "email": a.get("email") or data.get("email"),
            "phone": a.get("mobile") or a.get("phone") or data.get("phone"),
            "external_id": a.get("id") or data.get("external_id"),
            "job_ref": a.get("job_id") or data.get("job_ref"),
        }
    if src == "linkedin":
        a = data.get("applicant") or {}
        first = a.get("firstName") or ""
        last = a.get("lastName") or ""
        full = (first + " " + last).strip() or a.get("name") or data.get("name")
        return {
            "name": full,
            "email": a.get("emailAddress") or a.get("email") or data.get("email"),
            "phone": a.get("phoneNumber") or a.get("phone") or data.get("phone"),
            "external_id": a.get("applicationId") or a.get("id") or data.get("external_id"),
            "job_ref": a.get("jobPostingId") or data.get("job_ref"),
        }
    return {
        "name": data.get("name"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "external_id": data.get("external_id"),
        "job_ref": data.get("job_ref"),
    }


class CustomRecruitmentWebhookLog(models.Model):
    _name = "custom.recruitment.webhook.log"
    _description = "Recruitment Webhook Log"
    _inherit = ["mail.thread"]
    _order = "received_at desc"

    name = fields.Char(
        string="Reference",
        compute="_compute_name",
        store=True,
    )
    source = fields.Selection(
        selection=WEBHOOK_SOURCES,
        string="Source",
        required=True,
        default="manual",
        tracking=True,
    )
    payload_json = fields.Text(string="Raw Payload")
    processed = fields.Boolean(
        string="Processed",
        default=False,
        tracking=True,
    )
    applicant_id = fields.Many2one(
        "hr.applicant",
        string="Applicant",
        ondelete="set null",
    )
    error_message = fields.Text(string="Error")
    received_at = fields.Datetime(
        string="Received At",
        default=fields.Datetime.now,
        required=True,
    )

    @api.depends("source", "received_at")
    def _compute_name(self):
        for rec in self:
            ts = fields.Datetime.to_string(rec.received_at) if rec.received_at else ""
            rec.name = "%s / %s" % (rec.source or "?", ts or rec.id or "new")

    @api.model
    def ingest_payload(self, source, data):
        """Webhook intake.

        Persists the inbound payload, normalizes vendor-specific shapes
        (Jobstreet / Glints / LinkedIn / generic) and creates a draft
        hr.applicant. The HTTP controller performs HMAC verification before
        calling this method.
        """
        if source not in dict(WEBHOOK_SOURCES):
            source = "manual"
        try:
            payload_str = json.dumps(data, ensure_ascii=False, default=str)
        except Exception as exc:  # noqa: BLE001
            payload_str = str(data)
            _logger.warning("ingest_payload: payload not JSON-serializable: %s", exc)

        log = self.create(
            {
                "source": source,
                "payload_json": payload_str,
                "processed": False,
            }
        )

        try:
            norm = _normalize_payload(source, data)
            vals = {
                "partner_name": norm.get("name") or _("Webhook Applicant"),
                "email_from": norm.get("email"),
                "partner_phone": norm.get("phone"),
                "x_job_board_source": source,
                "x_external_id": norm.get("external_id"),
            }
            # Best-effort job match by external job_ref → hr.job.id (string) or name.
            job_ref = norm.get("job_ref")
            if job_ref:
                Job = self.env["hr.job"]
                job = Job.search([("name", "=", str(job_ref))], limit=1)
                if not job:
                    try:
                        job = Job.browse(int(job_ref)).exists()
                    except (TypeError, ValueError):
                        job = Job
                if job:
                    vals["job_id"] = job.id
            applicant = self.env["hr.applicant"].create(vals)
            log.write(
                {
                    "applicant_id": applicant.id,
                    "processed": True,
                }
            )
            _logger.info(
                "custom_recruitment_id: ingested applicant %s from %s",
                applicant.id,
                source,
            )
        except Exception as exc:  # noqa: BLE001
            log.write(
                {
                    "processed": False,
                    "error_message": str(exc),
                }
            )
            _logger.exception("custom_recruitment_id: webhook ingest failed")
        return log
