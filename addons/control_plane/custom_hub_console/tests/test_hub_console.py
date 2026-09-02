# -*- coding: utf-8 -*-
"""Tests for ``custom_hub_console``.

Covers:
* catalog scan populates rows for the platform addon buckets
* audit event hash chain links prev_hash → hash correctly
* ``verify_chain()`` detects raw-SQL tampering
* deploy wizard creates ``custom.hub.module.deployment`` rows and emits
  an audit event (even when the orchestrator is unreachable)
* tenant extension fields are searchable
* ``write`` / ``unlink`` on audit events raise ``UserError``
"""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_hub_console")
class TestHubConsole(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Catalog = cls.env["custom.hub.module.catalog"]
        cls.Audit = cls.env["custom.hub.audit.event"]
        cls.Deployment = cls.env["custom.hub.module.deployment"]
        cls.Tenant = cls.env["tenant.registry"]
        cls.Wizard = cls.env["custom.hub.deploy.module.wizard"]

        # Stand up a fake tenant so deployment rows have something to point at.
        cls.tenant = cls.Tenant.sudo().create(
            {
                "slug": "test-vertical",
                "display_name": "Test Vertical",
                "db_name": "test_vertical",
                "state": "active",
                "business_domain": "ppob",
                "deployment_topology": "centralized",
            }
        )

    # ------------------------------------------------------------------
    # 1. Catalog scan
    # ------------------------------------------------------------------
    def test_catalog_scan_populates(self):
        result = self.Catalog._action_scan_all()
        self.assertIn("total", result)
        # The platform ships well over 30 addons across buckets; assert
        # at least a healthy minimum so we know the scanner walked them.
        self.assertGreaterEqual(
            result["total"], 20, f"Catalog scan only found {result['total']} modules — expected at least 20."
        )
        # And the catalog table reflects what scan reported.
        self.assertGreaterEqual(self.Catalog.search_count([]), 20)

    # ------------------------------------------------------------------
    # 2. Audit hash chain integrity
    # ------------------------------------------------------------------
    def test_audit_hash_chain_integrity(self):
        # Note: an install-time genesis row may already exist; chain on top.
        events = self.env["custom.hub.audit.event"]
        for i in range(5):
            events |= self.Audit.log(
                event_type="module_deploy",
                summary=f"test deploy #{i}",
                payload={"i": i},
            )
        # Every event's prev_hash should equal the prior event's hash.
        prev_hash = ""
        prior = self.Audit.sudo().search([("id", "<", events[0].id)], order="id desc", limit=1)
        if prior:
            prev_hash = prior.hash
        for ev in events.sorted("id"):
            self.assertEqual(ev.prev_hash, prev_hash, f"Event {ev.id} prev_hash should match prior row's hash")
            self.assertTrue(ev.hash, "Hash must be computed on create")
            prev_hash = ev.hash

        report = self.Audit.verify_chain()
        self.assertTrue(report["ok"], f"Chain not intact: {report}")
        self.assertEqual(report["bad_ids"], [])

    # ------------------------------------------------------------------
    # 3. Tamper detection
    # ------------------------------------------------------------------
    def test_audit_hash_chain_tamper_detected(self):
        ev = self.Audit.log(
            event_type="module_deploy",
            summary="will be tampered",
            payload={"a": 1},
        )
        # Mutate summary via raw SQL — bypassing write() lockdown — to
        # simulate an attacker that got DB-level access.
        self.env.cr.execute(
            "UPDATE custom_hub_audit_event SET summary=%s WHERE id=%s",
            ("TAMPERED", ev.id),
        )
        self.env.invalidate_all()
        report = self.Audit.verify_chain()
        self.assertFalse(report["ok"])
        self.assertIn(ev.id, report["bad_ids"])

    # ------------------------------------------------------------------
    # 4. Deploy wizard emits audit event
    # ------------------------------------------------------------------
    def test_deploy_wizard_emits_audit(self):
        # Use any catalog entry — scan first to ensure at least one exists.
        self.Catalog._action_scan_all()
        catalog = self.Catalog.search([], limit=1)
        self.assertTrue(catalog, "Catalog should have at least one entry")

        audit_before = self.Audit.search_count([])
        wizard = self.Wizard.create(
            {
                "catalog_id": catalog.id,
                "tenant_ids": [(6, 0, [self.tenant.id])],
                "deploy_mode": "install",
                "confirmed": True,
            }
        )
        action = wizard.action_confirm()
        self.assertEqual(action["res_model"], "custom.hub.module.deployment")

        # Deployment row created.
        deploys = self.Deployment.search(
            [
                ("tenant_id", "=", self.tenant.id),
                ("catalog_id", "=", catalog.id),
            ]
        )
        self.assertTrue(deploys)
        # Orchestrator will fail in the test env → state should be 'failed',
        # but the audit event should still have been emitted.
        self.assertIn(deploys[0].state, ("failed", "installed"))
        self.assertGreater(self.Audit.search_count([]), audit_before)

    # ------------------------------------------------------------------
    # 5. Tenant extension fields searchable
    # ------------------------------------------------------------------
    def test_tenant_extension_fields(self):
        # Created in setUpClass with business_domain='ppob'.
        found = self.Tenant.search(
            [
                ("business_domain", "=", "ppob"),
                ("slug", "=", "test-vertical"),
            ]
        )
        self.assertIn(self.tenant, found)
        self.assertEqual(self.tenant.deployment_topology, "centralized")
        # module_count should compute (zero modules assigned for now).
        self.assertEqual(self.tenant.module_count, 0)
        # health_status compute should not raise even when ops_monitor
        # has no health row for this tenant.
        self.assertIn(
            self.tenant.health_status,
            ("green", "yellow", "red", "unknown"),
        )

    # ------------------------------------------------------------------
    # 6. Audit write / unlink blocked
    # ------------------------------------------------------------------
    def test_audit_write_blocked(self):
        ev = self.Audit.log(
            event_type="module_deploy",
            summary="lockdown test",
            payload={"x": 1},
        )
        with self.assertRaises(UserError):
            ev.write({"summary": "modified"})
        with self.assertRaises(UserError):
            ev.unlink()
