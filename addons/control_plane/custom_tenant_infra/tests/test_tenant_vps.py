# -*- coding: utf-8 -*-
"""Smoke tests for tenant.vps state machine + tenant.environment constraints."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_tenant_infra")
class TestTenantVps(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Vps = self.env["tenant.vps"]
        self.Env = self.env["tenant.environment"]

    def _make_vps(self, name="vps-test", hostname="vps-test.example.com"):
        return self.Vps.create(
            {
                "name": name,
                "hostname": hostname,
                "ssh_user": "root",
                "ssh_port": 22,
                "ssh_credential_ref": "vault://test/vps/dummy",
            }
        )

    def test_create_defaults(self):
        vps = self._make_vps()
        self.assertEqual(vps.state, "registered")
        self.assertEqual(vps.ssh_port, 22)
        self.assertFalse(vps.bootstrap_log)

    def test_valid_state_transitions(self):
        vps = self._make_vps()
        vps._set_state("hardening")
        self.assertEqual(vps.state, "hardening")
        vps._set_state("bootstrapping")
        vps._set_state("active")
        vps._set_state("degraded")
        vps._set_state("active")
        vps._set_state("decommissioned")
        self.assertEqual(vps.state, "decommissioned")

    def test_invalid_state_transition(self):
        vps = self._make_vps(hostname="vps-invalid.example.com")
        # registered → active is NOT allowed (must go via hardening/bootstrapping)
        with self.assertRaises(UserError):
            vps._set_state("active")

    def test_decommissioned_is_terminal(self):
        vps = self._make_vps(hostname="vps-terminal.example.com")
        vps._set_state("decommissioned")
        with self.assertRaises(UserError):
            vps._set_state("active")

    def test_hostname_unique(self):
        self._make_vps(hostname="vps-dup.example.com")
        with self.assertRaises(Exception):
            self._make_vps(name="dup2", hostname="vps-dup.example.com")
            self.env.flush_all()

    def test_append_log(self):
        vps = self._make_vps(hostname="vps-log.example.com")
        vps._append_log("hello")
        vps._append_log("world")
        self.assertIn("hello", vps.bootstrap_log)
        self.assertIn("world", vps.bootstrap_log)
