# -*- coding: utf-8 -*-
"""Approval request state machine + tier walking."""

from __future__ import annotations

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import ApprovalTestCommon


@tagged("post_install", "-at_install")
class TestRequestLifecycle(ApprovalTestCommon):
    def _build_two_tier_matrix(self):
        m = self._make_matrix("Two-tier PO")
        self._add_tier(m, sequence=10, name="Dept Head", approvers=[self.user_approver_a])
        self._add_tier(m, sequence=20, name="Finance", approvers=[self.user_approver_b])
        return m

    def _make_confirmable_po(self):
        """A PO with a service line so ``button_confirm`` actually posts."""
        product = self.env["product.product"].create(
            {"name": "Approval Test Service", "type": "service", "purchase_ok": True}
        )
        return self.PurchaseOrder.with_user(self.user_requester).create(
            {
                "partner_id": self.vendor.id,
                "order_line": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "name": "Approval Test Service",
                            "product_qty": 1.0,
                            "product_uom_id": product.uom_id.id,
                            "price_unit": 100.0,
                            "date_planned": fields.Datetime.now(),
                        },
                    )
                ],
            }
        )

    def test_submit_then_first_tier_approve_advances(self):
        m = self._build_two_tier_matrix()
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        self.assertEqual(req.state, "pending")
        self.assertEqual(req.current_tier_id.name, "Dept Head")
        self.assertIn(self.user_approver_a, req.pending_approver_ids)

        req.with_user(self.user_approver_a).action_approve(comment="ok")
        # Reload from DB (record cache)
        req.invalidate_recordset()

        self.assertEqual(req.state, "pending")
        self.assertEqual(req.current_tier_id.name, "Finance")
        self.assertIn(self.user_approver_b, req.pending_approver_ids)

    def test_final_tier_approve_completes(self):
        m = self._build_two_tier_matrix()
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()
        req.with_user(self.user_approver_a).action_approve()
        req.with_user(self.user_approver_b).action_approve()
        req.invalidate_recordset()

        self.assertEqual(req.state, "approved")
        self.assertTrue(req.decided_at)

    def test_reject_terminates_request(self):
        m = self._build_two_tier_matrix()
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        req.with_user(self.user_approver_a).action_reject(comment="missing docs")
        req.invalidate_recordset()

        self.assertEqual(req.state, "rejected")
        self.assertEqual(req.final_decision_user_id, self.user_approver_a)

    def test_non_approver_cannot_approve(self):
        m = self._build_two_tier_matrix()
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        with self.assertRaises(UserError):
            req.with_user(self.user_requester).action_approve()

    def test_require_all_waits_for_every_approver(self):
        m = self._make_matrix("Require all")
        self._add_tier(
            m,
            name="Co-approval",
            approvers=[self.user_approver_a, self.user_approver_b],
            require_all=True,
        )
        po = self._make_po()
        req = self.Request._create_for_record(po, matrix=m)
        req.action_submit()

        # First approver acts — still pending
        req.with_user(self.user_approver_a).action_approve()
        req.invalidate_recordset()
        self.assertEqual(req.state, "pending")

        # Second approver completes
        req.with_user(self.user_approver_b).action_approve()
        req.invalidate_recordset()
        self.assertEqual(req.state, "approved")

    def test_purchase_confirm_auto_submits_then_gate_opens(self):
        m = self._build_two_tier_matrix()
        po = self._make_po()

        # Clicking Confirm with a matching matrix no longer raises — it
        # auto-creates + submits the approval and leaves the PO unconfirmed.
        po.button_confirm()
        po.invalidate_recordset()
        req = po.x_custom_approval_request_id
        self.assertTrue(req, "Confirm should have auto-created an approval request")
        self.assertEqual(req.state, "pending")
        self.assertEqual(req.current_tier_id.name, "Dept Head")
        self.assertNotEqual(po.state, "purchase", "PO must wait for approval, not confirm")

        # Re-clicking Confirm while pending is idempotent — no second request.
        po.button_confirm()
        po.invalidate_recordset()
        self.assertEqual(po.x_custom_approval_request_id, req)
        self.assertEqual(
            self.Request.search_count([("res_model", "=", "purchase.order"), ("res_id", "=", po.id)]),
            1,
        )

        # Walk both tiers.
        req.with_user(self.user_approver_a).action_approve()
        req.with_user(self.user_approver_b).action_approve()
        req.invalidate_recordset()
        self.assertEqual(req.state, "approved")

        # The gate is now open: the engine auto-proceed (and any manual
        # Confirm) may proceed past approval.
        po.invalidate_recordset()
        self.assertTrue(po._approval_request_or_proceed())

    def test_no_matrix_confirm_proceeds_immediately(self):
        # No matrix matches this PO → confirm proceeds, no request created.
        po = self._make_po()
        try:
            po.button_confirm()
        except UserError as e:
            # Tolerate core errors (e.g. empty order lines) but never approval.
            self.assertNotIn("approval", str(e).lower())
        self.assertFalse(
            self.Request.search([("res_model", "=", "purchase.order"), ("res_id", "=", po.id)]),
            "No matrix → no approval request should be created",
        )

    def test_auto_proceed_confirms_as_requester(self):
        # Approvers hold only group_approval_manager (no purchase rights); the
        # PO still confirms after grant because auto-proceed runs as the
        # requester (who created the PO).
        self._build_two_tier_matrix()
        po = self._make_confirmable_po()

        po.button_confirm()
        po.invalidate_recordset()
        req = po.x_custom_approval_request_id
        self.assertEqual(req.state, "pending")
        self.assertEqual(req.requested_by_id, self.user_requester)
        self.assertNotEqual(po.state, "purchase")

        req.with_user(self.user_approver_a).action_approve()
        req.with_user(self.user_approver_b).action_approve()
        req.invalidate_recordset()
        po.invalidate_recordset()

        self.assertEqual(req.state, "approved")
        self.assertEqual(po.state, "purchase", "Final approval should auto-confirm the PO")
