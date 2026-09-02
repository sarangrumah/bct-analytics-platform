# -*- coding: utf-8 -*-
import json
import logging
from html import escape

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


HELPDESK_PRIORITY_SELECTION = [
    ("low", "Low"),
    ("normal", "Normal"),
    ("high", "High"),
    ("urgent", "Urgent"),
]

RATING_SELECTION = [
    ("1", "1 - Very Bad"),
    ("2", "2 - Bad"),
    ("3", "3 - Neutral"),
    ("4", "4 - Good"),
    ("5", "5 - Excellent"),
]


class DiscussChannel(models.Model):
    _inherit = "discuss.channel"

    x_helpdesk_ticket_id = fields.Many2one(
        "helpdesk.ticket",
        string="Helpdesk Ticket",
        readonly=True,
        copy=False,
    )
    x_escalated_to_helpdesk = fields.Boolean(
        string="Escalated to Helpdesk",
        default=False,
        copy=False,
    )
    x_helpdesk_priority = fields.Selection(
        HELPDESK_PRIORITY_SELECTION,
        string="Helpdesk Priority",
        default="normal",
    )
    x_ai_suggested_text = fields.Text(string="AI Suggested Reply")
    x_last_ai_query = fields.Char(
        string="Last AI Query Hash",
        readonly=True,
        copy=False,
        help="Hash of the last AI request payload, used to skip duplicate "
        "AI calls when the conversation context has not changed.",
    )
    x_rating = fields.Selection(
        RATING_SELECTION,
        string="Visitor Rating",
        copy=False,
    )
    x_rating_feedback = fields.Text(
        string="Rating Feedback",
        copy=False,
    )
    x_rating_requested = fields.Boolean(
        string="Rating Requested",
        default=False,
        copy=False,
    )

    # ---------- helpers ----------

    def _custom_livechat_recent_messages(self, limit=50):
        """Return the most recent mail.message records (oldest first)."""
        self.ensure_one()
        messages = self.env["mail.message"].search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.id),
                ("message_type", "in", ("comment", "email")),
            ],
            order="date desc, id desc",
            limit=limit,
        )
        return messages.sorted(key=lambda m: (m.date or fields.Datetime.now(), m.id))

    def _custom_livechat_build_transcript(self, limit=50):
        """Build a plain-text transcript of the recent messages."""
        self.ensure_one()
        lines = []
        for msg in self._custom_livechat_recent_messages(limit=limit):
            author = msg.author_id.name or msg.email_from or _("System")
            ts = fields.Datetime.to_string(msg.date) if msg.date else ""
            body = msg.body or ""
            # Strip basic HTML for transcript readability
            try:
                from odoo.tools import html2plaintext

                body = html2plaintext(body)
            except Exception:
                pass
            lines.append("[{ts}] {author}: {body}".format(ts=ts, author=author, body=body))
        return "\n".join(lines)

    # ---------- workflow buttons ----------

    def action_escalate_to_helpdesk(self):
        """Convert this channel to a helpdesk.ticket, link both ways."""
        self.ensure_one()
        if self.x_escalated_to_helpdesk and self.x_helpdesk_ticket_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "helpdesk.ticket",
                "res_id": self.x_helpdesk_ticket_id.id,
                "view_mode": "form",
                "target": "current",
            }
        transcript = self._custom_livechat_build_transcript(limit=50)
        # Pick a customer if one of the channel partners is not an internal user
        partner = False
        for p in self.channel_partner_ids:
            if not p.user_ids or all(u.share for u in p.user_ids):
                partner = p
                break
        subject = self.name or _("Live Chat Escalation")
        description_html = "<p><b>%s</b></p><pre>%s</pre>" % (
            _("Escalated from live chat channel #%s") % self.id,
            escape(transcript),
        )
        priority_map = {"low": "0", "normal": "1", "high": "2", "urgent": "3"}
        ticket_vals = {
            "subject": subject[:200],
            "description": description_html,
            "partner_id": partner.id if partner else False,
            "priority": priority_map.get(self.x_helpdesk_priority or "normal", "1"),
        }
        ticket = self.env["helpdesk.ticket"].create(ticket_vals)
        self.write(
            {
                "x_helpdesk_ticket_id": ticket.id,
                "x_escalated_to_helpdesk": True,
            }
        )
        # Post note on the channel
        self.message_post(
            body=_("Chat escalated to Helpdesk ticket <b>%s</b>.") % escape(ticket.name or ""),
            subtype_xmlid="mail.mt_note",
        )
        # Post note on the ticket
        ticket.message_post(
            body=_("Created from live chat channel <b>%s</b> (id=%s).") % (escape(self.name or ""), self.id),
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "helpdesk.ticket",
            "res_id": ticket.id,
            "view_mode": "form",
            "target": "current",
        }

    # ---------- AI ----------

    def _custom_ai_payload(self):
        self.ensure_one()
        messages = self._custom_livechat_recent_messages(limit=10)
        history = []
        try:
            from odoo.tools import html2plaintext
        except Exception:
            html2plaintext = lambda x: x  # noqa: E731
        for msg in messages:
            history.append(
                {
                    "author": msg.author_id.name or msg.email_from or "system",
                    "date": fields.Datetime.to_string(msg.date) if msg.date else "",
                    "body": html2plaintext(msg.body or "")[:1000],
                }
            )
        return {
            "channel_id": self.id,
            "channel_name": self.name or "",
            "channel_type": self.channel_type or "",
            "participants": self.channel_partner_ids.mapped("name"),
            "history": history,
        }

    def _custom_ai_payload_hash(self, payload):
        """Stable hash of the AI payload used for duplicate-call caching."""
        import hashlib

        try:
            blob = json.dumps(payload, sort_keys=True, default=str)
        except Exception:
            blob = str(payload)
        # Hash used only as a cache/dedup key, not security — bandit B324
        return hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()  # nosemgrep

    def action_ai_suggest_reply(self):
        self.ensure_one()
        payload = self._custom_ai_payload()
        payload_hash = self._custom_ai_payload_hash(payload)
        if self.x_last_ai_query and self.x_last_ai_query == payload_hash and self.x_ai_suggested_text:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Suggested Reply"),
                    "message": _("Cached reply reused (no new context)."),
                    "type": "info",
                },
            }
        try:
            result = self.env["custom.ai"]._recommend(
                model="discuss.channel",
                res_id=self.id,
                payload=payload,
            )
        except Exception as e:
            _logger.error("AI suggest reply failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = result.get("response") or result.get("text") or result.get("summary") or json.dumps(result)[:1000]
        self.write(
            {
                "x_ai_suggested_text": text,
                "x_last_ai_query": payload_hash,
            }
        )
        self.message_post(
            body=_("<b>AI Suggested Reply</b><br/>%s") % text,
            author_id=self.env.ref("base.partner_root").id,
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ---------- Skill routing ----------

    def _custom_livechat_pick_operator(self, query_text=""):
        """Return a res.users record for routing using a round-robin strategy.

        Filters the channel's available operators by skill tags on the
        underlying ``im_livechat.channel`` when the visitor text contains
        any tag keyword; otherwise round-robins across all operators.
        """
        self.ensure_one()
        livechat_channel = self.livechat_channel_id
        if not livechat_channel:
            return self.env["res.users"]
        operators = livechat_channel.user_ids
        if not operators:
            return self.env["res.users"]
        skill_tags = livechat_channel._skill_tag_list()
        query_lower = (query_text or "").lower()
        matched_by_skill = bool(skill_tags and any(tag in query_lower for tag in skill_tags))
        # Round-robin: order by id, skip operators that already have many
        # active livechat channels.
        sorted_ops = operators.sorted(key=lambda u: u.id)
        if matched_by_skill:
            # Skill filter is currently a stub on the channel level (any
            # operator on a channel that declares the tag is eligible).
            return sorted_ops[:1]
        # Pure round-robin based on existing open channel count
        Channel = self.env["discuss.channel"].sudo()
        counts = []
        for op in sorted_ops:
            cnt = (
                Channel.search_count(
                    [
                        ("livechat_channel_id", "=", livechat_channel.id),
                        ("livechat_operator_id", "=", op.partner_id.id),
                        ("livechat_end_dt", "=", False),
                    ]
                )
                if "livechat_end_dt" in Channel._fields
                else 0
            )
            counts.append((cnt, op.id, op))
        counts.sort(key=lambda x: (x[0], x[1]))
        return counts[0][2] if counts else self.env["res.users"]

    def message_post(self, **kwargs):
        """Route the channel to a skill-matched / round-robin operator on
        the first inbound message when no operator is assigned yet."""
        message = super().message_post(**kwargs)
        try:
            if (
                self.channel_type == "livechat"
                and "livechat_channel_id" in self._fields
                and self.livechat_channel_id
                and "livechat_operator_id" in self._fields
                and not self.livechat_operator_id
            ):
                body = kwargs.get("body") or ""
                try:
                    from odoo.tools import html2plaintext

                    body_text = html2plaintext(body)
                except Exception:
                    body_text = str(body)
                operator = self._custom_livechat_pick_operator(body_text)
                if operator and operator.partner_id:
                    self.sudo().write(
                        {
                            "livechat_operator_id": operator.partner_id.id,
                        }
                    )
        except Exception as e:
            _logger.debug("Skill routing skipped: %s", e)
        return message

    # ---------- Visitor satisfaction rating ----------

    def action_request_visitor_rating(self):
        """Post a note + flag the channel as awaiting a visitor rating."""
        self.ensure_one()
        self.x_rating_requested = True
        self.message_post(
            body=_(
                "<b>Please rate your chat experience</b><br/>"
                "Reply with a number from 1 (very bad) to 5 (excellent), "
                "and any additional feedback."
            ),
            subtype_xmlid="mail.mt_comment",
        )
        return True

    @api.model
    def submit_visitor_rating(self, channel_id, rating, feedback=None):
        """Public-ish helper to record a visitor rating from the frontend.

        ``rating`` must be a string in ``{'1','2','3','4','5'}``.
        """
        if not channel_id or not rating:
            return False
        rating_str = str(rating)
        if rating_str not in {r[0] for r in RATING_SELECTION}:
            return False
        channel = self.browse(int(channel_id)).exists()
        if not channel:
            return False
        channel.sudo().write(
            {
                "x_rating": rating_str,
                "x_rating_feedback": feedback or False,
                "x_rating_requested": False,
            }
        )
        return True
