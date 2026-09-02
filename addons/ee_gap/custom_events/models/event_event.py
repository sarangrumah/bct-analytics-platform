# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class EventEvent(models.Model):
    _inherit = "event.event"

    # ---------- WhatsApp / Check-in / PDP (existing) ----------

    x_whatsapp_ticket_template_id = fields.Many2one(
        "whatsapp.template",
        string="WhatsApp Ticket Template",
        help="Template used when delivering tickets to attendees via WhatsApp.",
    )
    x_marketing_consent_purpose = fields.Selection(
        [
            ("event_followup", "Event Follow-up"),
            ("none", "No Marketing Follow-up"),
        ],
        string="Marketing Consent Purpose",
        default="none",
        required=True,
        help="PDP consent purpose required before marketing follow-up after the event.",
    )
    x_qr_checkin_enabled = fields.Boolean(
        string="QR Check-in Enabled",
        default=True,
        help="Allow attendees to be checked in by scanning their QR token.",
    )

    # ---------- Sponsors ----------

    sponsor_ids = fields.One2many(
        "custom.event.sponsor",
        "event_id",
        string="Sponsors",
    )
    sponsor_count = fields.Integer(
        string="Sponsor #",
        compute="_compute_sponsor_count",
    )

    # ---------- Multi-track ----------

    x_has_tracks = fields.Boolean(
        string="Has Sessions / Tracks",
        default=False,
        help="If enabled, the event uses event.track for multi-session scheduling "
        "and attendees may pick track preferences on registration.",
    )

    # ---------- Post-event survey ----------

    x_post_event_survey_id = fields.Many2one(
        "survey.survey",
        string="Post-event Survey",
        help="Survey link sent to confirmed attendees via the post-event cron after x_end_date / date_end has passed.",
    )
    x_post_event_survey_sent = fields.Boolean(
        string="Post-event Survey Sent",
        default=False,
        copy=False,
    )
    x_end_date = fields.Datetime(
        string="Event End (Extended)",
        help="Optional override of date_end used by the post-event survey cron. Falls back to date_end if not set.",
    )

    # ---------- Computes ----------

    @api.depends("sponsor_ids")
    def _compute_sponsor_count(self):
        for ev in self:
            ev.sponsor_count = len(ev.sponsor_ids)

    # ---------- Survey cron ----------

    @api.model
    def _cron_send_post_event_survey(self):
        """Daily cron: for events whose effective end < now and a survey is set,
        send the survey link to all confirm-state registrations once."""
        now = fields.Datetime.now()
        events = self.search(
            [
                ("x_post_event_survey_sent", "=", False),
                ("x_post_event_survey_id", "!=", False),
            ]
        )
        sent_events = self.env["event.event"]
        for ev in events:
            effective_end = ev.x_end_date or ev.date_end
            if not effective_end or effective_end > now:
                continue
            survey = ev.x_post_event_survey_id
            template = self.env.ref(
                "custom_events.mail_template_post_event_survey",
                raise_if_not_found=False,
            )
            if not template:
                _logger.warning("Post-event survey template missing; skip event %s", ev.name)
                continue
            regs = ev.registration_ids.filtered(lambda r: r.state == "open" and r.email)
            for reg in regs:
                try:
                    survey_url = (
                        survey.get_start_url()
                        if hasattr(survey, "get_start_url")
                        else "/survey/start/%s" % survey.access_token
                    )
                except Exception:  # pragma: no cover
                    survey_url = "/survey/start/%s" % (survey.access_token or "")
                template.with_context(survey_url=survey_url).send_mail(
                    reg.id,
                    force_send=False,
                )
            ev.x_post_event_survey_sent = True
            sent_events |= ev
            _logger.info(
                "Post-event survey dispatched for event=%s recipients=%s",
                ev.name,
                len(regs),
            )
        return len(sent_events)

    # ---------- Manual: promote waitlist for this event ----------

    def action_promote_waitlist(self):
        """Promote eligible waitlisted registrations up to seats_available."""
        self.ensure_one()
        return self.registration_ids.filtered(lambda r: r.state == "waitlist").action_promote_from_waitlist()
