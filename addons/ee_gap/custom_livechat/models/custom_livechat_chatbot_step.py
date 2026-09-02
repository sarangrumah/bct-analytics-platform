# -*- coding: utf-8 -*-
import re

from odoo import api, fields, models


STEP_TYPES = [
    ("text", "Text"),
    ("question", "Question"),
    ("forward_to_operator", "Forward to Operator"),
    ("end", "End"),
]


class CustomLivechatChatbotStep(models.Model):
    _name = "custom.livechat.chatbot.step"
    _description = "Live Chat Chatbot Step"
    _order = "script_id, sequence, id"

    script_id = fields.Many2one(
        "custom.livechat.chatbot.script",
        string="Script",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    step_type = fields.Selection(
        STEP_TYPES,
        string="Type",
        default="text",
        required=True,
    )
    message = fields.Text(string="Message")
    expected_answers = fields.Char(
        string="Expected Answers",
        help="Comma-separated regex patterns. Matched against the visitor "
        "reply (case-insensitive) for 'question' steps.",
    )
    next_step_default = fields.Many2one(
        "custom.livechat.chatbot.step",
        string="Default Next Step",
        ondelete="set null",
    )

    # ---------- helpers ----------

    def _expected_patterns(self):
        self.ensure_one()
        if not self.expected_answers:
            return []
        return [p.strip() for p in self.expected_answers.split(",") if p.strip()]

    def _match_user_message(self, user_msg):
        """Return True if any expected_answers regex matches user_msg."""
        self.ensure_one()
        if not user_msg:
            return False
        patterns = self._expected_patterns()
        if not patterns:
            return False
        for pat in patterns:
            try:
                if re.search(pat, user_msg, flags=re.IGNORECASE):
                    return True
            except re.error:
                # Fall back to literal substring match on bad regex
                if pat.lower() in user_msg.lower():
                    return True
        return False

    # ---------- API ----------

    @api.model
    def get_next_step(self, current_id, user_msg):
        """Resolve the next step from ``current_id`` given ``user_msg``.

        Logic:
        - If the current step is 'end' or 'forward_to_operator': stop (False).
        - If the current step is 'question' and any expected_answers regex
          matches the user message: go to next sequential step of the script
          (or ``next_step_default`` if no following step).
        - If no expected_answers or no match: fall back to
          ``next_step_default``.
        - For 'text' steps: walk to the next sequential step.

        Returns a dict ``{found, step_id, step_type, message}``.
        """
        empty = {"found": False, "step_id": False, "step_type": False, "message": ""}
        if not current_id:
            return empty
        step = self.browse(current_id).exists()
        if not step:
            return empty
        if step.step_type in ("end", "forward_to_operator"):
            return empty

        next_step = self.browse()
        if step.step_type == "question":
            if step._match_user_message(user_msg or ""):
                next_step = step._next_sequential() or step.next_step_default
            else:
                next_step = step.next_step_default or step._next_sequential()
        else:
            # 'text' or anything else: walk sequentially, fallback to default
            next_step = step._next_sequential() or step.next_step_default

        if not next_step:
            return empty
        return {
            "found": True,
            "step_id": next_step.id,
            "step_type": next_step.step_type,
            "message": next_step.message or "",
        }

    def _next_sequential(self):
        """Return the next step in the same script by (sequence, id)."""
        self.ensure_one()
        siblings = self.script_id.step_ids.sorted(key=lambda s: (s.sequence, s.id))
        seen = False
        for s in siblings:
            if seen:
                return s
            if s.id == self.id:
                seen = True
        return self.browse()
