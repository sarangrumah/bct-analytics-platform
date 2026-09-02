# -*- coding: utf-8 -*-
"""Smoke tests for Track C canary deploy + rollback flow."""

from __future__ import annotations

import json
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_hub_console_canary")
class TestCanaryDeploy(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Catalog = cls.env["custom.hub.module.catalog"]
        cls.Deployment = cls.env["custom.hub.module.deployment"]
        cls.Tenant = cls.env["tenant.registry"]

        cls.tenant = cls.Tenant.sudo().create(
            {
                "slug": "canary-tenant",
                "display_name": "Canary Tenant",
                "db_name": "canary_tenant",
                "state": "active",
                "business_domain": "ppob",
                "deployment_topology": "centralized",
            }
        )

        cls.mod_base = cls.Catalog.sudo().create(
            {
                "module_name": "mod_base_canary",
                "category": "core",
                "maturity": "production",
            }
        )
        cls.mod_dep = cls.Catalog.sudo().create(
            {
                "module_name": "mod_dep_canary",
                "category": "core",
                "maturity": "production",
                "depends_module_ids": [(6, 0, [cls.mod_base.id])],
            }
        )

    # ------------------------------------------------------------------
    def _new_deployment(self, catalog):
        return self.Deployment.sudo().create(
            {
                "catalog_id": catalog.id,
                "tenant_id": self.tenant.id,
                "deploy_mode": "install",
                "state": "pending",
            }
        )

    # ------------------------------------------------------------------
    def test_resolve_dependencies_topo_order(self):
        dep = self._new_deployment(self.mod_dep)
        dep.action_resolve_dependencies()
        payload = json.loads(dep.dep_graph_resolved_json or "{}")
        self.assertIn("order", payload)
        self.assertIn("missing", payload)
        # Dependency must come before dependent.
        self.assertEqual(payload["order"][0], "mod_base_canary")
        self.assertEqual(payload["order"][-1], "mod_dep_canary")
        self.assertEqual(payload["missing"], [])

    # ------------------------------------------------------------------
    def test_canary_phase_initial_none(self):
        dep = self._new_deployment(self.mod_base)
        self.assertEqual(dep.canary_phase, "none")
        self.assertFalse(dep.healthcheck_passed)

    # ------------------------------------------------------------------
    def test_healthcheck_no_snapshot_marks_not_passed(self):
        dep = self._new_deployment(self.mod_base)
        dep.action_healthcheck()
        # No fresh green snapshot in test env → should not pass.
        self.assertFalse(dep.healthcheck_passed)
        self.assertTrue(dep.healthcheck_at)

    # ------------------------------------------------------------------
    def test_rollout_full_gated_on_healthcheck(self):
        dep = self._new_deployment(self.mod_base)
        # Without healthcheck_passed, rollout_full should NOT advance phase.
        dep.action_rollout_full()
        self.assertNotEqual(dep.canary_phase, "full")
        self.assertIn("healthcheck", (dep.error_message or "").lower())

    # ------------------------------------------------------------------
    def test_rollback_without_snapshot_records_error(self):
        dep = self._new_deployment(self.mod_base)
        dep.action_rollback()
        self.assertNotEqual(dep.canary_phase, "rolled_back")
        self.assertIn("snapshot", (dep.error_message or "").lower())

    # ------------------------------------------------------------------
    def test_canary_sequence_runs_and_rolls_back_on_failed_health(self):
        """Mock orchestrator + force health=False → sequence should rollback
        if snapshot exists, or just set rolled_back state otherwise."""
        dep = self._new_deployment(self.mod_base)

        with (
            patch.object(
                type(self.env["custom.super.admin.orchestrator.client"]),
                "_request",
                return_value={},
            ),
            patch.object(
                type(self.env["custom.super.admin.orchestrator.client"]),
                "run_backup",
                return_value={"id": 1},
            ),
        ):
            dep._run_canary_sequence()

        # Healthcheck in test env yields not-passed → either rolled_back
        # (if a snapshot was linked) or failed state.
        self.assertIn(dep.canary_phase, ("canary", "rolled_back"))
        self.assertFalse(dep.healthcheck_passed)
