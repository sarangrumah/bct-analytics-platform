# -*- coding: utf-8 -*-
"""Internal-user chat portal: /ai/chat."""

from odoo import http
from odoo.http import request


class AiChatPortal(http.Controller):
    @http.route("/ai/chat", type="http", auth="user", website=True)
    def chat(self):
        session = request.env["ai.nlq.session"].sudo().open_or_create_for_user()
        # Bind to the env-user so PII masking flag is correct downstream
        messages = session.message_ids.sorted("create_date")
        return request.render(
            "custom_ai_features.portal_chat_page",
            {"session": session, "messages": messages},
        )

    @http.route("/ai/chat/ask", type="http", auth="user", website=True, methods=["POST"], csrf=True)
    def ask(self, question: str = "", **post):
        question = (question or "").strip()
        if not question:
            return request.redirect("/ai/chat")
        session = request.env["ai.nlq.session"].sudo().open_or_create_for_user()
        # Run as the actual user so PII masking + audit user attribution are correct
        session.with_user(request.env.user).ask(question)
        return request.redirect("/ai/chat")
