# -*- coding: utf-8 -*-
{
    "name": "Custom Live Chat Extensions",
    "summary": "Escalate chat to helpdesk, canned responses, chatbot scripts, skill routing, AI suggested reply, visitor ratings",
    "description": """
Custom Live Chat Extensions is a CE-targeted set of extensions on top of
im_livechat documented at
https://www.odoo.com/documentation/19.0/applications/websites/livechat.html.

Tier 3 features:

- Convert an active chat channel into a helpdesk ticket (escalation) with
  priority + last 50 message transcript attached as description.
- Canned responses with shortcut, category, language and usage count plus a
  composer-side :shortcut expansion JS asset.
- Chatbot scripts with multiple step types (text, question, forward to
  operator, end) and regex-matched expected answers.
- Operator skill tags + simple round-robin routing on first message of a
  livechat channel.
- AI Suggested Reply via custom_ai_bridge based on recent chat history,
  with last-query caching and a clipboard "Insert into Reply" helper.
- Visitor satisfaction rating (1-5) + free-text feedback with an agent
  panel button to request the rating.
""",
    "author": "Custom Platform",
    "category": "Productivity/Live Chat",
    "version": "19.0.0.2.0",
    "license": "LGPL-3",
    "depends": [
        "custom_core",
        "custom_pdp_audit",
        "custom_ai_bridge",
        "custom_helpdesk",
        "im_livechat",
    ],
    "capability_tags": ["helpdesk", "livechat", "ai", "audit-trail", "pdp"],
    "data": [
        "views/generated_search_views.xml",
        "security/security.xml",
        "security/ir.model.access.csv",
        "views/discuss_channel_views.xml",
        "views/custom_livechat_canned_response_views.xml",
        "views/custom_livechat_chatbot_views.xml",
        "views/im_livechat_channel_views.xml",
        "views/menu_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "custom_livechat/static/src/js/canned_response_composer.js",
            "custom_livechat/static/src/js/ai_reply_clipboard.js",
        ],
    },
    "installable": True,
    "application": True,
    "auto_install": False,
}
