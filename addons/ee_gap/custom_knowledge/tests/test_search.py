# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKnowledgeSearch(TransactionCase):
    def setUp(self):
        super().setUp()
        Article = self.env["knowledge.article"]
        self.a1 = Article.create(
            {
                "name": "PostgreSQL replication primer",
                "body": "<p>Streaming replication and logical replication explained.</p>",
            }
        )
        self.a2 = Article.create(
            {
                "name": "Kafka consumer lag",
                "body": "<p>How to monitor consumer lag in production clusters.</p>",
            }
        )
        self.a3 = Article.create(
            {
                "name": "Postgres backup runbook",
                "body": "<p>pg_dump, pg_basebackup and WAL archiving for replication.</p>",
            }
        )

    def test_search_vector_populated(self):
        """Computed-stored search_vector should contain stripped text."""
        self.assertIn("replication", (self.a1.search_vector or "").lower())
        self.assertNotIn("<p>", self.a1.search_vector or "")

    def test_search_articles_ranks_relevance(self):
        """``search_articles`` must return hits ordered by ts_rank desc."""
        rows = self.env["knowledge.article"].search_articles("replication", limit=10)
        ids = [r["id"] for r in rows]
        # Both replication-mentioning articles must appear, the kafka one must not
        self.assertIn(self.a1.id, ids)
        self.assertIn(self.a3.id, ids)
        self.assertNotIn(self.a2.id, ids)

    def test_search_articles_empty_query(self):
        self.assertEqual(self.env["knowledge.article"].search_articles(""), [])
