# -*- coding: utf-8 -*-
"""A/B test harness on top of ``mailing.mailing``.

Each ``custom.email.ab.test`` row pairs one parent mailing with two variants
(A/B) and a split percentage. ``action_split_send()`` clones the parent into
two child mailings, divides the audience by ``split_pct``, sends both, and
schedules ``cron_evaluate_winner`` 24h later to pick the better-performing
variant by the configured ``winner_metric``.
"""

import logging
import random

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomEmailAbTest(models.Model):
    _name = "custom.email.ab.test"
    _description = "Email A/B Test"
    _inherit = ["mail.thread"]
    _order = "create_date desc"

    name = fields.Char(required=True, tracking=True)
    mailing_id = fields.Many2one(
        "mailing.mailing",
        string="Parent Mailing",
        required=True,
        ondelete="cascade",
        tracking=True,
    )
    variant_a_subject = fields.Char(string="Variant A — Subject", required=True)
    variant_a_body = fields.Html(string="Variant A — Body", sanitize=False)
    variant_b_subject = fields.Char(string="Variant B — Subject", required=True)
    variant_b_body = fields.Html(string="Variant B — Body", sanitize=False)
    split_pct = fields.Integer(
        string="Split % (A)",
        default=50,
        help="Percent of the audience that receives variant A. The remainder receives variant B.",
    )
    winner_metric = fields.Selection(
        [
            ("opens", "Opens"),
            ("clicks", "Clicks"),
            ("replies", "Replies"),
        ],
        default="opens",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("concluded", "Concluded"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    winner = fields.Selection(
        [
            ("a", "Variant A"),
            ("b", "Variant B"),
            ("tie", "Tie"),
        ],
        tracking=True,
    )
    variant_a_mailing_id = fields.Many2one(
        "mailing.mailing",
        string="Variant A Mailing",
        readonly=True,
    )
    variant_b_mailing_id = fields.Many2one(
        "mailing.mailing",
        string="Variant B Mailing",
        readonly=True,
    )
    sent_at = fields.Datetime(readonly=True)
    evaluate_after = fields.Datetime(
        string="Evaluate After",
        readonly=True,
        help="The cron will evaluate the winner once this timestamp is past (default: 24h after split_send).",
    )
    variant_a_score = fields.Integer(readonly=True)
    variant_b_score = fields.Integer(readonly=True)

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    @api.constrains("split_pct")
    def _check_split_pct(self):
        for rec in self:
            if rec.split_pct < 1 or rec.split_pct > 99:
                raise UserError(_("Split percent must be between 1 and 99."))

    # ------------------------------------------------------------------
    # Split send
    # ------------------------------------------------------------------

    def action_split_send(self):
        """Clone parent mailing into A/B variants and dispatch them.

        The audience is taken from ``mailing_id._get_remaining_recipients()``,
        shuffled deterministically per call, then split by ``split_pct``.
        """
        Mailing = self.env["mailing.mailing"]
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("A/B test %s is not in draft state.") % rec.display_name)
            parent = rec.mailing_id
            if not parent:
                raise UserError(_("A/B test %s has no parent mailing.") % rec.display_name)

            audience = list(parent._get_remaining_recipients() or [])
            if not audience:
                raise UserError(_("Parent mailing has no remaining recipients."))

            random.shuffle(audience)
            split_at = max(1, min(len(audience) - 1, len(audience) * rec.split_pct // 100))
            audience_a = audience[:split_at]
            audience_b = audience[split_at:]

            variant_a = parent.copy(
                {
                    "name": "%s [A]" % parent.name,
                    "subject": rec.variant_a_subject,
                    "body_arch": rec.variant_a_body or parent.body_arch,
                    "body_html": rec.variant_a_body or parent.body_html,
                }
            )
            variant_b = parent.copy(
                {
                    "name": "%s [B]" % parent.name,
                    "subject": rec.variant_b_subject,
                    "body_arch": rec.variant_b_body or parent.body_arch,
                    "body_html": rec.variant_b_body or parent.body_html,
                }
            )

            variant_a.action_send_mail(res_ids=audience_a)
            variant_b.action_send_mail(res_ids=audience_b)

            now = fields.Datetime.now()
            evaluate_after = fields.Datetime.add(now, hours=24)
            rec.write(
                {
                    "state": "running",
                    "variant_a_mailing_id": variant_a.id,
                    "variant_b_mailing_id": variant_b.id,
                    "sent_at": now,
                    "evaluate_after": evaluate_after,
                }
            )
            _logger.info(
                "[custom_email_marketing] AB test %s split_send: A=%d B=%d evaluate_after=%s",
                rec.id,
                len(audience_a),
                len(audience_b),
                evaluate_after,
            )
        return True

    # ------------------------------------------------------------------
    # Winner evaluation (cron + manual)
    # ------------------------------------------------------------------

    def _compute_variant_score(self, mailing):
        """Return integer score for one variant mailing using winner_metric."""
        self.ensure_one()
        Trace = self.env["mailing.trace"].sudo()
        if self.winner_metric == "opens":
            return Trace.search_count(
                [
                    ("mass_mailing_id", "=", mailing.id),
                    ("trace_status", "in", ("open", "reply")),
                ]
            )
        if self.winner_metric == "clicks":
            return Trace.search_count(
                [
                    ("mass_mailing_id", "=", mailing.id),
                    ("x_click_count", ">", 0),
                ]
            )
        if self.winner_metric == "replies":
            return Trace.search_count(
                [
                    ("mass_mailing_id", "=", mailing.id),
                    ("trace_status", "=", "reply"),
                ]
            )
        return 0

    def action_evaluate_winner(self):
        """Manual winner pick — usable from the form button."""
        for rec in self:
            if rec.state != "running":
                continue
            rec._evaluate_one()
        return True

    def _evaluate_one(self):
        self.ensure_one()
        score_a = self._compute_variant_score(self.variant_a_mailing_id)
        score_b = self._compute_variant_score(self.variant_b_mailing_id)
        if score_a > score_b:
            winner = "a"
        elif score_b > score_a:
            winner = "b"
        else:
            winner = "tie"
        self.write(
            {
                "variant_a_score": score_a,
                "variant_b_score": score_b,
                "winner": winner,
                "state": "concluded",
            }
        )
        self.message_post(
            body=_("A/B test concluded: A=%(a)d B=%(b)d → winner=%(w)s (metric=%(m)s)")
            % {
                "a": score_a,
                "b": score_b,
                "w": winner,
                "m": self.winner_metric,
            }
        )
        _logger.info(
            "[custom_email_marketing] AB test %s concluded: A=%d B=%d winner=%s",
            self.id,
            score_a,
            score_b,
            winner,
        )

    @api.model
    def cron_evaluate_winner(self):
        """Cron entrypoint — picks winners for all running tests past
        ``evaluate_after`` timestamp.
        """
        now = fields.Datetime.now()
        running = self.sudo().search(
            [
                ("state", "=", "running"),
                ("evaluate_after", "!=", False),
                ("evaluate_after", "<=", now),
            ]
        )
        for rec in running:
            try:
                rec._evaluate_one()
            except Exception as exc:  # pragma: no cover — defensive
                _logger.exception(
                    "[custom_email_marketing] AB test %s evaluation failed: %s",
                    rec.id,
                    exc,
                )
        return len(running)
