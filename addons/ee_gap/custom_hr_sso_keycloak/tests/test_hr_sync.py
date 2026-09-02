# -*- coding: utf-8 -*-
"""hr.sso.sync: claim->employee link, HC API enrichment, idempotency, outage safety."""

from unittest.mock import MagicMock, patch

import requests

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

NIK = "1234567890123456"
HC_PATH = "odoo.addons.custom_hr_sso_keycloak.models.hr_sso_sync.requests.get"


@tagged("post_install", "-at_install")
class TestHrSync(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Sync = cls.env["hr.sso.sync"]
        cls.Users = cls.env["res.users"]
        cls.Employee = cls.env["hr.employee"]
        cls.ICP = cls.env["ir.config_parameter"].sudo()

    def _user_and_employee(self, login):
        user = self.Users.create({"name": login, "login": login})
        emp = self.Employee.create({"name": login, "work_email": login})
        return user, emp

    def test_link_and_fill_from_claims(self):
        login = "carol@example.com"
        user, emp = self._user_and_employee(login)
        self.Sync.sync_for_login(login, {"email": login, "nik": NIK, "dept": "Finance"})

        self.assertEqual(emp.user_id, user, "employee linked to user by work_email")
        self.assertTrue(emp.department_id, "department set from claim")
        self.assertEqual(emp.department_id.name, "Finance")
        if "x_custom_nik" in self.Employee._fields:
            self.assertEqual(emp.x_custom_nik, NIK)

        # Idempotent: a second run creates no duplicate department, changes nothing.
        dept_before = emp.department_id
        self.Sync.sync_for_login(login, {"email": login, "nik": NIK, "dept": "Finance"})
        self.assertEqual(emp.department_id, dept_before)
        self.assertEqual(self.env["hr.department"].search_count([("name", "=", "Finance")]), 1)

    def test_invalid_nik_skipped(self):
        login = "erin@example.com"
        _user, emp = self._user_and_employee(login)
        self.Sync.sync_for_login(login, {"email": login, "nik": "123"})  # not 16 digits
        if "x_custom_nik" in self.Employee._fields:
            self.assertFalse(emp.x_custom_nik, "invalid NIK is not written")

    def test_hc_api_enrichment(self):
        login = "dave@example.com"
        _user, emp = self._user_and_employee(login)
        boss = self.Employee.create({"name": "Boss", "work_email": "boss@example.com"})
        self.ICP.set_param("hc.base_url", "https://hc.test/")
        self.ICP.set_param("hc.api_key", "plainkey")  # get_encrypted passes plaintext through

        resp = MagicMock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = {
            "status": True,
            "data": {
                "department": "Engineering",
                "job_title": "Engineer",
                "superior": {"email": "boss@example.com"},
            },
        }
        with patch(HC_PATH, return_value=resp) as mocked:
            self.Sync.sync_for_login(login, {"email": login, "nik": NIK})

        mocked.assert_called_once()
        self.assertEqual(emp.department_id.name, "Engineering")
        self.assertEqual(emp.job_id.name, "Engineer")
        self.assertEqual(emp.parent_id, boss)

    def test_hc_api_outage_does_not_block(self):
        login = "frank@example.com"
        _user, emp = self._user_and_employee(login)
        self.ICP.set_param("hc.base_url", "https://hc.test/")
        self.ICP.set_param("hc.api_key", "plainkey")

        with patch(HC_PATH, side_effect=requests.exceptions.ConnectionError("down")):
            # Must not raise.
            self.Sync.sync_for_login(login, {"email": login, "nik": NIK})
        self.assertFalse(emp.department_id, "no data written on API outage")

    def test_no_employee_is_noop(self):
        login = "grace@example.com"
        self.Users.create({"name": login, "login": login})  # user but no employee
        # Should simply log + return without error.
        self.Sync.sync_for_login(login, {"email": login, "nik": NIK, "dept": "Ops"})
        self.assertFalse(self.Employee.search([("work_email", "=", login)]))
