# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)

# Threshold of hard bounces per email before auto-blacklisting.
HARD_BOUNCE_BLACKLIST_THRESHOLD = 3


class MailingTrace(models.Model):
    _inherit = "mailing.trace"

    x_first_open_at = fields.Datetime(
        string="First Opened At",
        readonly=True,
        help="Timestamp of the very first open (preserved across re-opens).",
    )
    x_open_count = fields.Integer(
        string="Open Count",
        default=0,
        readonly=True,
    )
    x_click_count = fields.Integer(
        string="Click Count",
        default=0,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Counter updates on standard transitions
    # ------------------------------------------------------------------

    def set_opened(self, domain=None):
        traces = super().set_opened(domain=domain)
        now = fields.Datetime.now()
        for trace in traces:
            vals = {"x_open_count": (trace.x_open_count or 0) + 1}
            if not trace.x_first_open_at:
                vals["x_first_open_at"] = now
            trace.write(vals)
        return traces

    def set_clicked(self, domain=None):
        traces = super().set_clicked(domain=domain)
        for trace in traces:
            trace.write({"x_click_count": (trace.x_click_count or 0) + 1})
        return traces

    def set_bounced(self, domain=None, bounce_message=False):
        traces = super().set_bounced(domain=domain, bounce_message=bounce_message)
        traces._blacklist_bounce()
        return traces

    # ------------------------------------------------------------------
    # Auto-blacklist after N hard bounces
    # ------------------------------------------------------------------

    def _blacklist_bounce(self):
        """Add e-mails to mail.blacklist after N hard bounces.

        We count distinct hard-bounce traces per normalized email; once the
        count hits ``HARD_BOUNCE_BLACKLIST_THRESHOLD`` we insert into
        ``mail.blacklist`` (idempotent via ``_add``).
        """
        Blacklist = self.env["mail.blacklist"].sudo()
        emails = sorted({(t.email or "").strip().lower() for t in self if t.email})
        for email in emails:
            if not email:
                continue
            count = self.sudo().search_count(
                [
                    ("email", "=", email),
                    ("failure_type", "=", "mail_bounce"),
                    ("trace_status", "=", "bounce"),
                ]
            )
            if count < HARD_BOUNCE_BLACKLIST_THRESHOLD:
                continue
            existing = Blacklist.search([("email", "=", email)], limit=1)
            if existing:
                continue
            try:
                Blacklist._add(
                    email,
                    message=("Auto-blacklisted by custom_email_marketing after %d hard bounces" % count),
                )
                _logger.info(
                    "[custom_email_marketing] auto-blacklisted %s after %d bounces",
                    email,
                    count,
                )
            except Exception as exc:  # pragma: no cover — defensive
                _logger.warning(
                    "[custom_email_marketing] failed to blacklist %s: %s",
                    email,
                    exc,
                )
        return True
