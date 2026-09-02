# -*- coding: utf-8 -*-
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestKnowledgeVersioning(TransactionCase):
    def setUp(self):
        super().setUp()
        self.article = self.env["knowledge.article"].create(
            {
                "name": "Versioned Article",
                "body": "<p>v1</p>",
            }
        )

    def test_body_change_creates_snapshot(self):
        self.assertEqual(self.article.version_count, 0)
        self.article.write({"body": "<p>v2</p>"})
        self.assertEqual(self.article.version_count, 1)
        snap = self.article.version_ids[0]
        self.assertEqual(snap.body_snapshot, "<p>v1</p>")
        self.assertEqual(snap.version_no, 1)

        self.article.write({"body": "<p>v3</p>"})
        self.assertEqual(self.article.version_count, 2)
        # Latest snapshot holds the *previous* body
        latest = self.article.version_ids.sorted("version_no")[-1]
        self.assertEqual(latest.version_no, 2)
        self.assertEqual(latest.body_snapshot, "<p>v2</p>")

    def test_restore_version(self):
        self.article.write({"body": "<p>v2</p>"})
        self.article.write({"body": "<p>v3</p>"})
        first_snap = self.article.version_ids.filtered(lambda v: v.version_no == 1)
        first_snap.action_restore_version()
        # Restored body == the original v1
        self.assertEqual(self.article.body, "<p>v1</p>")
        # Restore itself produced another snapshot (of the pre-restore v3)
        snaps = self.article.version_ids.sorted("version_no")
        self.assertEqual(snaps[-1].body_snapshot, "<p>v3</p>")

    def test_no_snapshot_when_body_unchanged(self):
        self.article.write({"name": "Renamed but same body"})
        self.assertEqual(self.article.version_count, 0)

    def test_share_token_autogen(self):
        a = self.env["knowledge.article"].create({"name": "Sharable", "is_shared_externally": True})
        self.assertTrue(a.share_token)
        self.assertGreaterEqual(len(a.share_token), 16)

    def test_favorite_toggle(self):
        a = self.env["knowledge.article"].create({"name": "Fav"})
        self.assertFalse(a.is_favorite)
        a.action_toggle_favorite()
        a.invalidate_recordset(["is_favorite"])
        self.assertTrue(a.is_favorite)
        a.action_toggle_favorite()
        a.invalidate_recordset(["is_favorite"])
        self.assertFalse(a.is_favorite)
