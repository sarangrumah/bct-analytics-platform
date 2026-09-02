# -*- coding: utf-8 -*-
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestFinancePortal(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env["hr.employee"].create({"name": "Budi Requester", "user_id": cls.env.uid})
        cls.item = cls.env["finance.item.submission"].create({"name": "Meal Allowance", "code": "MEAL"})

    def _make_ca(self, amount, pr=None):
        return self.env["finance.cash.advance"].create(
            {
                "requester_id": self.employee.id,
                "pr_number": pr,
                "line_ids": [(0, 0, {"name": "Item", "item_id": self.item.id, "quantity": 1.0, "unit_amount": amount})],
            }
        )

    # ---- PR-required threshold (spreadsheet rule: > Rp 1.000.000 needs PR) ----
    def test_pr_required_above_threshold(self):
        ca = self._make_ca(2_000_000)
        self.assertEqual(ca.amount, 2_000_000)
        with self.assertRaises(UserError):
            ca.action_submit_for_approval()
        # Supplying a PR number unblocks submission.
        ca.pr_number = "PR-001"
        ca.action_submit_for_approval()
        self.assertEqual(ca.state, "submitted")

    def test_below_threshold_no_pr_needed(self):
        ca = self._make_ca(500_000)
        ca.action_submit_for_approval()
        self.assertEqual(ca.state, "submitted")

    # ---- No-matrix happy path + SAP stub push + status mirror ----
    def test_engagement_lifecycle_stub(self):
        ca = self._make_ca(500_000)
        ca.action_submit_for_approval()
        ca.action_approve()
        # Stub push (no bridge configured) marks the doc pushed, never posts GL.
        self.assertEqual(ca.state, "pushed")
        self.assertEqual(ca.sap_sync_state, "pushed")
        self.assertTrue(ca.sap_pushed_at)
        # Bridge mirrors SAP status back.
        ca._finance_apply_sap_status({"sap_document_no": "SAP-CA-1", "sap_payment_status": "paid"})
        self.assertEqual(ca.sap_document_no, "SAP-CA-1")
        self.assertEqual(ca.state, "paid")

    # ---- Two-tier Tax -> Finance matrix wires through the engine ----
    def test_two_tier_matrix_integration(self):
        model = self.env["ir.model"]._get("finance.cash.advance")
        tax_group = self.env.ref("custom_finance_portal.group_finance_tax")
        fin_group = self.env.ref("custom_finance_portal.group_finance_officer")
        matrix = self.env["approval.matrix"].create(
            {
                "name": "CA Tax->Finance",
                "model_id": model.id,
                "condition_domain": "[]",
                "trigger": "manual",
                "tier_ids": [
                    (
                        0,
                        0,
                        {
                            "sequence": 10,
                            "name": "Tax Review",
                            "approver_type": "group",
                            "approver_group_id": tax_group.id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "sequence": 20,
                            "name": "Finance Review",
                            "approver_type": "group",
                            "approver_group_id": fin_group.id,
                        },
                    ),
                ],
            }
        )
        self.assertEqual(len(matrix.tier_ids), 2)
        ca = self._make_ca(500_000)
        ca.action_submit_for_approval()
        # The engine took over — an approval request now governs the document.
        self.assertTrue(ca.x_custom_approval_request_id)

    # ---- Vendor invoice PO Non-Trade requires a PO number ----
    def test_vendor_invoice_po_requires_po(self):
        vendor = self.env["res.partner"].create({"name": "Vendor A", "supplier_rank": 1})
        with self.assertRaises(UserError):
            self.env["finance.vendor.invoice"].create({"invoice_subtype": "po_non_trade", "vendor_id": vendor.id})
        inv = self.env["finance.vendor.invoice"].create({"invoice_subtype": "non_po_non_trade", "vendor_id": vendor.id})
        self.assertTrue(inv.id)

    # ---- Realization difference computation ----
    def test_realization_difference(self):
        ca = self._make_ca(1_000_000)
        ca.action_submit_for_approval()
        ca.action_approve()
        real = self.env["finance.cash.advance.realization"].create(
            {
                "advance_id": ca.id,
                "requester_id": self.employee.id,
                "line_ids": [(0, 0, {"name": "Spent", "subtotal": 700_000})],
            }
        )
        self.assertEqual(real.amount, 700_000)
        self.assertEqual(real.difference, 300_000)
