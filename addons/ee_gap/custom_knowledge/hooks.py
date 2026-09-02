# -*- coding: utf-8 -*-
"""Post-init hook: create the GIN tsvector index used by full-text search.

Kept SQL-only (no ORM) so it survives across registry rebuilds and stays
out of the way of the standard ``write``/``create`` paths.
"""

import logging

_logger = logging.getLogger(__name__)


def post_init_hook(env):
    """Create the GIN(to_tsvector(...)) index on knowledge_article."""
    cr = env.cr
    cr.execute(
        """
        CREATE INDEX IF NOT EXISTS knowledge_article_search_idx
        ON knowledge_article
        USING GIN (
            to_tsvector(
                'english',
                coalesce(name, '') || ' ' || coalesce(body::text, '')
            )
        )
        """
    )
    _logger.info("custom_knowledge: GIN full-text index ensured")
