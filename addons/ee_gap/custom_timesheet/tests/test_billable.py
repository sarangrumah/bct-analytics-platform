# -*- coding: utf-8 -*-
from datetime import date

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_timesheet")
class TestBillableInvoice(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Test Customer TS"})
        cls.employee = cls.env["hr.employee"].create({"name": "TS Employee"})
        cls.project = cls.env["project.project"].create({"name": "TS Project", "partner_id": cls.partner.id})

    def _make_line(self, hours=4.0, billable=True, rate=100.0):
        line = self.env["account.analytic.line"].create(
            {
                "name": "Worked on feature",
                "date": date.today(),
                "unit_amount": hours,
                "employee_id": self.employee.id,
                "project_id": self.project.id,
                "x_billable": billable,
                "x_billing_rate": rate,
            }
        )
        return line

    def test_billable_invoice_wizard_creates_invoice(self):
        line = self._make_line(hours=3.0, rate=150.0)
        # Submit + validate (no matrix in test env -> auto-validates).
        line.action_submit_validation()
        if line.x_validation_state == "submitted":
            line.action_validate()
        self.assertEqual(line.x_validation_state, "validated")

        wizard = self.env["custom.timesheet.invoice.wizard"].create(
            {
                "partner_id": self.partner.id,
                "date_from": date.today(),
                "date_to": date.today(),
            }
        )
        wizard._onchange_filters()
        self.assertTrue(wizard.line_ids, "Wizard should pick up the validated billable line")

        action = wizard.action_create_invoice()
        invoice = self.env["account.move"].browse(action["res_id"])
        self.assertEqual(invoice.move_type, "out_invoice")
        self.assertEqual(invoice.partner_id, self.partner)
        self.assertEqual(len(invoice.invoice_line_ids), 1)
        inv_line = invoice.invoice_line_ids[0]
        self.assertEqual(inv_line.quantity, 3.0)
        self.assertEqual(inv_line.price_unit, 150.0)
        self.assertEqual(line.x_billed_invoice_line_id, inv_line)

    def test_non_validated_line_is_skipped(self):
        line = self._make_line(hours=2.0, rate=50.0)
        self.assertEqual(line.x_validation_state, "draft")
        wizard = self.env["custom.timesheet.invoice.wizard"].create(
            {
                "partner_id": self.partner.id,
                "date_from": date.today(),
                "date_to": date.today(),
            }
        )
        wizard._onchange_filters()
        self.assertFalse(wizard.line_ids, "Draft lines must not be invoiceable")
