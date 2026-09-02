# -*- coding: utf-8 -*-
from odoo import fields, models


class AiNlqMessage(models.Model):
    _name = "ai.nlq.message"
    _description = "NLQ Message"
    _order = "session_id, create_date asc"

    session_id = fields.Many2one("ai.nlq.session", required=True, ondelete="cascade", index=True)
    role = fields.Selection(
        [("user", "User"), ("assistant", "Assistant")],
        required=True,
    )
    content = fields.Text()
    plan_json = fields.Text(help="Raw AI plan returned for assistant messages.")
    result_json = fields.Text(help="search_read result preview (JSON).")
    is_error = fields.Boolean(default=False)
