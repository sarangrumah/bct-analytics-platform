# -*- coding: utf-8 -*-
"""Smoke test for tenant.replicate.wizard."""

from __future__ import annotations

from unittest.mock import patch

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReplicateWizard(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Reg = cls.env["tenant.registry"].sudo()
        cls.src = Reg.create(
            {
                "slug": "acme_prod",
                "display_name": "Acme (Prod)",
                "db_name": "acme_prod",
                "state": "active",
            }
        )
        cls.tgt = Reg.create(
            {
                "slug": "acme_staging",
                "display_name": "Acme (Staging)",
                "db_name": "acme_staging",
                "state": "active",
            }
        )
        cls.backup = (
            cls.env["tenant.backup"]
            .sudo()
            .create(
                {
                    "master_id": 9001,
                    "tenant_id": cls.src.id,
                    "tenant_slug": cls.src.slug,
                    "kind": "manual",
                    "started_at": "2026-01-01 00:00:00",
                    "outcome": "success",
                    "s3_key": "acme_prod/2026/01/01/x.dump",
                }
            )
        )

    def test_create_wizard_defaults(self):
        wiz = (
            self.env["tenant.replicate.wizard"]
            .sudo()
            .create(
                {
                    "source_tenant_id": self.src.id,
                    "target_tenant_id": self.tgt.id,
                }
            )
        )
        self.assertEqual(wiz.source_env_type, "prod")
        self.assertEqual(wiz.target_env_type, "staging")
        self.assertTrue(wiz.latest_backup_only)

    def test_distinct_constraint(self):
        with self.assertRaises(UserError):
            self.env["tenant.replicate.wizard"].sudo().create(
                {
                    "source_tenant_id": self.src.id,
                    "target_tenant_id": self.src.id,
                    "source_env_type": "prod",
                    "target_env_type": "prod",
                }
            )

    def test_action_replicate_calls_client(self):
        wiz = (
            self.env["tenant.replicate.wizard"]
            .sudo()
            .create(
                {
                    "source_tenant_id": self.src.id,
                    "target_tenant_id": self.tgt.id,
                    "latest_backup_only": True,
                }
            )
        )
        with patch.object(
            type(self.env["custom.super.admin.orchestrator.client"]),
            "replicate_backup",
            return_value={"restored_to_db": "acme_staging"},
        ) as mocked:
            result = wiz.action_replicate()
        self.assertTrue(mocked.called)
        kwargs = mocked.call_args.kwargs
        self.assertEqual(kwargs["backup_id"], 9001)
        self.assertEqual(kwargs["target_tenant_slug"], "acme_staging")
        self.assertEqual(kwargs["target_env"], "staging")
        self.assertEqual(result["type"], "ir.actions.client")
