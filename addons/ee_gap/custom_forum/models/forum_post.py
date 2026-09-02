# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


# forum.post Selection state values in CE website_forum:
#   active, pending, close, offensive, flagged
_ACTIVE_STATES = ("active", "pending")
_FLAG_TARGET_STATE = "close"  # close (not 'closed') per CE selection
_AI_BATCH_LIMIT = 50

_DEFAULT_SPAM_THRESHOLD = 0.8
_SPAM_THRESHOLD_PARAM = "custom_forum.spam_threshold"


class ForumPost(models.Model):
    _inherit = "forum.post"

    # ---------- AI moderation ----------

    x_ai_moderation_score = fields.Float(
        string="AI Toxicity Score 0-1",
        help="Toxicity score from AI bridge. 0.0 = safe, 1.0 = certainly toxic.",
    )
    x_ai_moderation_label = fields.Selection(
        selection=[
            ("safe", "Safe"),
            ("review", "Needs Review"),
            ("flag", "Flag"),
            ("spam", "Spam"),
        ],
        string="AI Moderation Label",
        default="safe",
    )
    x_ai_moderated_at = fields.Datetime(string="AI Moderated At")
    x_pdp_author_masked = fields.Boolean(
        string="Mask author identity",
        default=False,
        help="When enabled, the author identity is masked in public displays according to PDP requirements.",
    )

    # ---------- EE-gap: Reputation enhancement ----------

    x_helpful_count = fields.Integer(
        string="Helpful Votes",
        compute="_compute_x_helpful_count",
        store=True,
        help="Number of positive votes (vote=+1) on this post.",
    )

    @api.depends("vote_ids", "vote_ids.vote")
    def _compute_x_helpful_count(self):
        # forum.post.vote.vote is a Selection of -1/0/1 stored as string.
        for post in self:
            if not post.vote_ids:
                post.x_helpful_count = 0
                continue
            try:
                post.x_helpful_count = sum(1 for v in post.vote_ids if str(v.vote) == "1")
            except Exception:  # pragma: no cover - defensive
                post.x_helpful_count = 0

    # ---------- helpers ----------

    def _custom_ai_payload(self):
        self.ensure_one()
        return {"content": (self.content or "")[:4000]}

    def _parse_ai_label(self, raw_label):
        if not raw_label:
            return "safe"
        label = str(raw_label).strip().lower()
        if label in ("safe", "review", "flag", "spam"):
            return label
        # Permissive mapping for adjacent vocabularies returned by the bridge.
        if label in ("toxic", "offensive", "abuse", "abusive"):
            return "flag"
        if label in ("junk", "advertisement", "promotion"):
            return "spam"
        if label in ("uncertain", "borderline", "needs_review"):
            return "review"
        return "safe"

    @api.model
    def _get_spam_threshold(self):
        try:
            raw = (
                self.env["ir.config_parameter"]
                .sudo()
                .get_param(_SPAM_THRESHOLD_PARAM, default=str(_DEFAULT_SPAM_THRESHOLD))
            )
            return float(raw)
        except (TypeError, ValueError):
            return _DEFAULT_SPAM_THRESHOLD

    def _notify_forum_moderators(self, label, score):
        """Schedule an activity for the Forum Moderation Manager group."""
        self.ensure_one()
        try:
            mod_group = self.env.ref("custom_forum.group_manager", raise_if_not_found=False)
        except Exception:  # pragma: no cover
            mod_group = None
        if not mod_group or not mod_group.user_ids:
            return
        try:
            activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        except Exception:  # pragma: no cover
            activity_type = None
        if not activity_type:
            return
        for user in mod_group.user_ids:
            try:
                self.activity_schedule(
                    act_type_xmlid="mail.mail_activity_data_todo",
                    summary=_("Forum post flagged by AI (%s)") % label,
                    note=_(
                        "Post '%(name)s' was auto-flagged by AI moderation."
                        "<br/>Label: <b>%(label)s</b> — Score: %(score).2f"
                    )
                    % {
                        "name": self.name or _("(no title)"),
                        "label": label,
                        "score": score,
                    },
                    user_id=user.id,
                )
            except Exception as e:  # pragma: no cover - best-effort
                _logger.warning(
                    "Failed scheduling forum moderation activity for user %s: %s",
                    user.id,
                    e,
                )

    def _email_forum_admin_spam(self, score):
        """Send a notice email to the moderation manager when spam crosses threshold."""
        self.ensure_one()
        try:
            mod_group = self.env.ref("custom_forum.group_manager", raise_if_not_found=False)
        except Exception:  # pragma: no cover
            mod_group = None
        if not mod_group or not mod_group.user_ids:
            return
        partner_ids = [u.partner_id.id for u in mod_group.user_ids if u.partner_id]
        if not partner_ids:
            return
        try:
            self.message_post(
                body=_(
                    "<b>Spam threshold exceeded</b><br/>"
                    "AI score %(score).2f for post '%(name)s'. Auto-closed and "
                    "notified the forum admins."
                )
                % {"score": score, "name": self.name or ""},
                partner_ids=partner_ids,
                subtype_xmlid="mail.mt_comment",
            )
        except Exception as e:  # pragma: no cover - best-effort
            _logger.warning(
                "Failed sending spam notification email for post %s: %s",
                self.id,
                e,
            )

    # ---------- AI action ----------

    def action_ai_moderate(self):
        spam_threshold = self._get_spam_threshold()
        for post in self:
            try:
                result = post.env["custom.ai"]._recommend(
                    model="forum.post",
                    res_id=post.id,
                    payload=post._custom_ai_payload(),
                )
            except Exception as e:  # pragma: no cover - bridge failure is best-effort
                _logger.error("Forum AI moderation failed for post %s: %s", post.id, e)
                continue

            score = result.get("score")
            try:
                score = float(score) if score is not None else 0.0
            except (TypeError, ValueError):
                score = 0.0

            label = post._parse_ai_label(result.get("label"))

            post.write(
                {
                    "x_ai_moderation_score": score,
                    "x_ai_moderation_label": label,
                    "x_ai_moderated_at": fields.Datetime.now(),
                }
            )

            if label in ("flag", "spam"):
                post.message_post(
                    body=_(
                        "<b>AI Moderation</b><br/>"
                        "Label: <b>%(label)s</b> (score %(score).2f). "
                        "Post auto-closed for moderator review."
                    )
                    % {"label": label, "score": score},
                    subtype_xmlid="mail.mt_note",
                )
                if "state" in post._fields and post.state in _ACTIVE_STATES:
                    post.sudo().write({"state": _FLAG_TARGET_STATE})

                # Notify moderators via activity (feature 1)
                post._notify_forum_moderators(label, score)

                # Spam threshold escalation (feature 2)
                if label == "spam" and score > spam_threshold:
                    post._email_forum_admin_spam(score)

        return True

    # ---------- cron ----------

    @api.model
    def cron_ai_moderate_pending_posts(self):
        domain = [
            ("x_ai_moderation_score", "=", False),
            ("state", "=", "active"),
        ]
        posts = self.search(domain, limit=_AI_BATCH_LIMIT)
        if posts:
            posts.action_ai_moderate()
        return True

    # ---------- PDP author masking (feature 6) ----------

    @api.depends("name", "x_pdp_author_masked")
    def _compute_display_name(self):
        # Default behaviour, then mask the author display.  We intentionally
        # do NOT touch posts that aren't masked so the upstream behaviour is
        # preserved.
        super()._compute_display_name()
        for post in self:
            if post.x_pdp_author_masked:
                # Build a stable anonymous handle from the record id so the
                # same masked post always renders the same alias.
                alias = "Anonymous-%d" % (post.id or 0)
                # Display name format: "<title> — <alias>" so search/menu
                # references stay meaningful but the author is hidden.
                base = post.name or _("Untitled")
                post.display_name = f"{base} — {alias}"

    def _get_masked_author_label(self):
        """Helper used by website templates to render an anonymous label."""
        self.ensure_one()
        return _("Anonymous-%d") % (self.id or 0)
