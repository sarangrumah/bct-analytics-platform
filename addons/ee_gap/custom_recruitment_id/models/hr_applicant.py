# -*- coding: utf-8 -*-
import hashlib
import json
import logging
import re

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


JOB_BOARD_SOURCES = [
    ("manual", "Manual"),
    ("jobstreet", "Jobstreet"),
    ("glints", "Glints"),
    ("linkedin", "LinkedIn"),
    ("kalibrr", "Kalibrr"),
    ("direct", "Direct"),
]


_PHONE_RE = re.compile(r"[^0-9+]")


def _normalize_phone(phone):
    if not phone:
        return ""
    digits = _PHONE_RE.sub("", phone or "")
    # strip leading + and convert local 0xxx Indonesia prefix to 62xxx if applicable.
    if digits.startswith("+"):
        digits = digits[1:]
    if digits.startswith("0"):
        digits = "62" + digits[1:]
    return digits


def _compute_dedup_hash(email, phone):
    e = (email or "").strip().lower()
    p = _normalize_phone(phone)
    if not e and not p:
        return False
    raw = ("%s|%s" % (e, p)).encode("utf-8")
    # Hash used as stored x_dedup_hash key — not security; switching to SHA-256
    # would orphan existing applicant records keyed by their sha1 hash.
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()  # nosemgrep


class HrApplicant(models.Model):
    _inherit = "hr.applicant"

    x_job_board_source = fields.Selection(
        selection=JOB_BOARD_SOURCES,
        string="Job Board Source",
        default="manual",
        tracking=True,
        help="External job board from which this applicant originated.",
    )
    x_external_id = fields.Char(
        string="External Applicant ID",
        help="Source-side applicant ID (e.g. Jobstreet/Glints reference).",
        tracking=True,
    )
    x_pdp_retention_until = fields.Date(
        string="PDP Retention Until",
        help="Auto-purge after this date per UU PDP.",
        tracking=True,
    )
    x_pdp_consent_given = fields.Boolean(
        string="PDP Consent Given",
        default=False,
        tracking=True,
        help="Applicant explicitly consented to personal data processing.",
    )

    # ---------- Dedup ----------
    x_dedup_hash = fields.Char(
        string="Dedup Hash",
        compute="_compute_x_dedup_hash",
        store=True,
        index=True,
        help="SHA1(lower(email) + normalize(phone)) — used to detect duplicates.",
    )
    x_duplicate_of = fields.Many2one(
        "hr.applicant",
        string="Duplicate Of",
        ondelete="set null",
        help="If this applicant is a duplicate, points to the canonical record.",
        index=True,
    )
    x_is_duplicate = fields.Boolean(
        string="Is Duplicate",
        default=False,
        tracking=True,
    )

    # ---------- Offer letter ----------
    x_offer_salary = fields.Monetary(
        string="Offer Salary (Gross)",
        currency_field="x_offer_currency_id",
        help="Gross monthly salary in offer letter.",
    )
    x_offer_currency_id = fields.Many2one(
        "res.currency",
        string="Offer Currency",
        default=lambda self: self.env.company.currency_id.id,
    )
    x_offer_probation_months = fields.Integer(
        string="Probation (Months)",
        default=3,
    )
    x_offer_start_date = fields.Date(
        string="Start Date",
    )
    x_offer_pph21_estimated = fields.Monetary(
        string="Estimated PPh 21 / Month",
        currency_field="x_offer_currency_id",
        compute="_compute_x_offer_pph21_estimated",
        store=False,
        help="Indicative PPh 21 deduction estimate (flat 5%/15% bracket approximation).",
    )

    @api.depends("email_from", "partner_phone")
    def _compute_x_dedup_hash(self):
        for rec in self:
            rec.x_dedup_hash = _compute_dedup_hash(rec.email_from, rec.partner_phone)

    @api.depends("x_offer_salary")
    def _compute_x_offer_pph21_estimated(self):
        # Rough TER-style estimate: <= 5,400,000 → 0%, <= 6,200,000 → 0.25%,
        # else 2% as a placeholder. Real computation lives in custom_hr_payroll_id.
        for rec in self:
            sal = rec.x_offer_salary or 0.0
            if sal <= 5400000:
                rate = 0.0
            elif sal <= 6200000:
                rate = 0.0025
            elif sal <= 10700000:
                rate = 0.005
            elif sal <= 15000000:
                rate = 0.0175
            elif sal <= 30000000:
                rate = 0.05
            else:
                rate = 0.08
            rec.x_offer_pph21_estimated = sal * rate

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            rec._flag_if_duplicate()
        return records

    def write(self, vals):
        res = super().write(vals)
        if any(k in vals for k in ("email_from", "partner_phone")):
            for rec in self:
                rec._flag_if_duplicate()
        return res

    def _flag_if_duplicate(self):
        """Find an earlier applicant with the same dedup hash and flag self as a duplicate."""
        self.ensure_one()
        if not self.x_dedup_hash:
            return False
        existing = self.search(
            [
                ("x_dedup_hash", "=", self.x_dedup_hash),
                ("id", "!=", self.id),
                ("x_is_duplicate", "=", False),
            ],
            order="create_date asc, id asc",
            limit=1,
        )
        if existing:
            self.sudo().write(
                {
                    "x_duplicate_of": existing.id,
                    "x_is_duplicate": True,
                }
            )
            self.message_post(
                body=_("Flagged as duplicate of applicant <b>%s</b> (#%d) based on email + phone match.")
                % (existing.partner_name or "?", existing.id),
            )
            return True
        return False

    # ---------- Interview scheduling helper ----------
    def action_schedule_interview(self):
        """Open a calendar.event create form pre-filled with applicant info + interviewers."""
        self.ensure_one()
        partner_ids = []
        if self.partner_id:
            partner_ids.append(self.partner_id.id)
        interviewers = self.env["res.users"]
        # Pull interviewers from the job position (interviewer_ids exists in Odoo CE since v14).
        job = self.job_id
        if job and "interviewer_ids" in job._fields:
            interviewers |= job.interviewer_ids
        # Also consider hr.recruitment.source-based interviewers if available.
        Source = self.env.get("hr.recruitment.source")
        if Source is not None and "source_id" in self._fields and self.source_id:
            src = self.source_id
            if "user_id" in src._fields and src.user_id:
                interviewers |= src.user_id
        for u in interviewers:
            if u.partner_id:
                partner_ids.append(u.partner_id.id)

        name = _("Interview — %s") % (self.partner_name or self.display_name or _("Applicant"))
        ctx = {
            "default_name": name,
            "default_partner_ids": [(6, 0, list(set(partner_ids)))],
            "default_user_id": (interviewers and interviewers[0].id) or self.env.user.id,
            "default_description": _("Interview for applicant %s\nJob: %s\nSource: %s")
            % (
                self.partner_name or "?",
                (job and job.name) or "—",
                self.x_job_board_source or "manual",
            ),
            "default_res_model": "hr.applicant",
            "default_res_id": self.id,
        }
        return {
            "type": "ir.actions.act_window",
            "name": _("Schedule Interview"),
            "res_model": "calendar.event",
            "view_mode": "form",
            "target": "new",
            "context": ctx,
        }

    # ---------- Offer letter print ----------
    def action_print_offer_letter(self):
        self.ensure_one()
        return self.env.ref("custom_recruitment_id.action_report_offer_letter").report_action(self)

    # ---------- PDP-aware anonymization cron ----------
    @api.model
    def cron_purge_expired_applicants(self):
        """Anonymize applicants whose PDP retention horizon has passed.

        Per UU PDP, personally identifiable fields must be anonymized once the
        retention period expires. We keep the row (stage history preserved) but
        strip PII, post a chatter note, and write an entry to pdp.audit_log.
        """
        today = fields.Date.context_today(self)
        domain = [
            ("x_pdp_retention_until", "!=", False),
            ("x_pdp_retention_until", "<", today),
            ("partner_name", "not like", "REDACTED-%"),
        ]
        expired = self.search(domain)
        if not expired:
            _logger.info("custom_recruitment_id: no expired applicants to purge")
            return 0

        for applicant in expired:
            redacted_name = "REDACTED-%d" % applicant.id
            redacted_email = "redacted-%d@example.invalid" % applicant.id
            applicant.write(
                {
                    "partner_name": redacted_name,
                    "email_from": redacted_email,
                    "partner_phone": False,
                    "x_external_id": False,
                    "x_pdp_consent_given": False,
                    "x_dedup_hash": False,
                }
            )
            applicant.message_post(
                body=_("PDP retention horizon (%s) reached. PII fields anonymized per UU PDP. Stage history preserved.")
                % applicant.x_pdp_retention_until,
                subject=_("PDP Auto-Purge"),
            )
            # Audit trail via pdp.audit_log (raw insert — see custom_pdp_audit mixin).
            try:
                user = self.env.user
                self.env.cr.execute(
                    """
                    INSERT INTO pdp.audit_log (
                        actor_user_id, actor_login, tenant_db,
                        model_name, res_id, action,
                        field_changes, classification, reason
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                    """,
                    (
                        user.id if user else None,
                        user.login if user else "cron",
                        self.env.cr.dbname,
                        "hr.applicant",
                        applicant.id,
                        "write",
                        json.dumps(
                            {
                                "partner_name": "REDACTED",
                                "email_from": "REDACTED",
                                "partner_phone": "REDACTED",
                                "x_external_id": "REDACTED",
                            }
                        ),
                        "pii",
                        "PDP retention horizon reached — auto-anonymize",
                    ),
                )
            except Exception as exc:  # pragma: no cover
                _logger.warning(
                    "custom_recruitment_id: pdp.audit_log insert failed for applicant %s: %s",
                    applicant.id,
                    exc,
                )
        _logger.info("custom_recruitment_id: anonymized %d expired applicants", len(expired))
        return len(expired)
