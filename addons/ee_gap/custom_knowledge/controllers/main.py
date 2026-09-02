# -*- coding: utf-8 -*-
"""Public portal endpoint for share-by-token articles.

Route: ``/knowledge/share/<token>`` returns a minimal QWeb rendering of
the article body. Only articles with ``is_shared_externally=True`` and a
matching ``share_token`` are exposed; everything else 404s.
"""

import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


class KnowledgePortalController(http.Controller):
    @http.route(
        "/knowledge/share/<string:token>",
        type="http",
        auth="public",
        website=False,
        csrf=False,
        sitemap=False,
    )
    def share_article(self, token, **kw):
        if not token or len(token) < 16:
            return request.not_found()
        article = (
            request.env["knowledge.article"]
            .sudo()
            .search(
                [
                    ("share_token", "=", token),
                    ("is_shared_externally", "=", True),
                ],
                limit=1,
            )
        )
        if not article:
            return request.not_found()
        return request.render(
            "custom_knowledge.portal_share_article",
            {"article": article},
        )

    @http.route(
        "/knowledge/search",
        type="json",
        auth="user",
        csrf=False,
    )
    def search_articles(self, query="", limit=20):
        """JSON helper for client-side quick-search."""
        try:
            limit = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            limit = 20
        return request.env["knowledge.article"].search_articles(query, limit=limit)
