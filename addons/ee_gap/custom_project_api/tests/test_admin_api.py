# -*- coding: utf-8 -*-
"""Regression test for a bug this API shipped with for exactly one afternoon.

`_guard` caught `ValidationError` and returned a tidy 422 — but a model constraint fires at
flush, *after* the UPDATE has been issued. Without a `cr.rollback()` the request went on to
commit, so the API answered "rejected" while quietly saving the invalid value. The symptom
was the Hold stage silently losing its paused clock, which in turn makes every cycle-time
number wrong.

So the assertion that matters here is not the status code. It is that the database did not
change.
"""

import json

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install", "pmo")
class TestAdminApi(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "dummy-portal-password"
        cls.admin_user = cls.env["res.users"].create(
            {
                "name": "VAS Admin (test)",
                "login": "pmo.admin@test.invalid",
                "email": "pmo.admin@test.invalid",
                "password": cls.password,
                "group_ids": [
                    (4, cls.env.ref("custom_project_portfolio.group_pmo_admin").id),
                ],
            }
        )
        cls.member_password = "PmoMember!2026"
        cls.member_user = cls.env["res.users"].create(
            {
                "name": "VAS Member (test)",
                "login": "pmo.member@test.invalid",
                "email": "pmo.member@test.invalid",
                "password": cls.member_password,
                "group_ids": [
                    (4, cls.env.ref("custom_project_portfolio.group_pmo_user").id),
                ],
            }
        )
        cls.hold_stage = cls.env.ref("custom_project_portfolio.stage_hold")

    def _token(self, login, password):
        response = self.url_open(
            "/pmo/api/auth/login",
            data=json.dumps({"login": login, "password": password}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.text[:300])
        return response.json()["data"]["access"]

    def _patch(self, path, payload, token):
        return self.opener.patch(
            self.base_url() + path,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            timeout=30,
        )

    def test_login_returns_roles(self):
        token = self._token(self.admin_user.login, self.password)
        response = self.url_open(
            "/pmo/api/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("admin", response.json()["data"]["roles"])

    def test_wrong_password_is_401(self):
        response = self.url_open(
            "/pmo/api/auth/login",
            data=json.dumps({"login": self.admin_user.login, "password": "nope"}),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 401)

    def test_rejected_write_does_not_persist(self):
        """The bug: 422 returned, invalid value saved anyway."""
        token = self._token(self.admin_user.login, self.password)
        self.assertEqual(self.hold_stage.custom_sla_clock, "paused")

        response = self._patch(
            f"/pmo/api/admin/stages/{self.hold_stage.id}",
            {"custom_sla_clock": "running"},
            token,
        )
        self.assertEqual(response.status_code, 422, "An incoherent stage must be refused.")
        self.assertEqual(response.json()["error"]["code"], "RULE_REJECTED")

        self.hold_stage.invalidate_recordset()
        self.assertEqual(
            self.hold_stage.custom_sla_clock,
            "paused",
            "A refused write must leave the stage exactly as it was — otherwise the API "
            "says 'rejected' and saves the change anyway, and every cycle-time number "
            "downstream is wrong.",
        )
        self.assertTrue(self.hold_stage.custom_is_hold)

    def test_accepted_write_does_persist(self):
        """The mirror image: a legal change must survive, or the rollback is too eager."""
        token = self._token(self.admin_user.login, self.password)
        waiting = self.env.ref("custom_project_portfolio.stage_waiting_user")
        original = waiting.custom_auto_close_days
        try:
            response = self._patch(
                f"/pmo/api/admin/stages/{waiting.id}",
                {"custom_auto_close_days": original + 2},
                token,
            )
            self.assertEqual(response.status_code, 200, response.text[:300])
            waiting.invalidate_recordset()
            self.assertEqual(waiting.custom_auto_close_days, original + 2)
        finally:
            waiting.write({"custom_auto_close_days": original})

    def test_master_data_is_admin_only(self):
        token = self._token(self.member_user.login, self.member_password)
        response = self.url_open(
            "/pmo/api/admin/verticals",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "FORBIDDEN")
