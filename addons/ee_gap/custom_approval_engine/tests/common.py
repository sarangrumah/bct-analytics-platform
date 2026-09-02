# -*- coding: utf-8 -*-
"""Shared fixtures for approval engine tests."""

from __future__ import annotations


from odoo.tests.common import TransactionCase


class ApprovalTestCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Matrix = cls.env["approval.matrix"]
        cls.Tier = cls.env["approval.matrix.tier"]
        cls.Request = cls.env["approval.request"]
        cls.Delegation = cls.env["approval.delegation"]
        cls.OOO = cls.env["approval.ooo"]
        cls.PurchaseOrder = cls.env["purchase.order"]
        cls.Users = cls.env["res.users"]
        cls.Partner = cls.env["res.partner"]

        # ----- Users -----
        # Two regular approvers + one fallback + one requester
        cls.user_approver_a = cls._make_user(
            "approver_a", "Approver A", ["custom_approval_engine.group_approval_manager"]
        )
        cls.user_approver_b = cls._make_user(
            "approver_b", "Approver B", ["custom_approval_engine.group_approval_manager"]
        )
        cls.user_fallback = cls._make_user(
            "fallback_user", "Fallback Approver", ["custom_approval_engine.group_approval_manager"]
        )
        cls.user_delegate = cls._make_user(
            "delegate_user", "Delegate User", ["custom_approval_engine.group_approval_manager"]
        )
        cls.user_requester = cls._make_user(
            "requester", "Requester", ["custom_approval_engine.group_approval_user", "purchase.group_purchase_user"]
        )

        # A vendor partner for purchase orders
        cls.vendor = cls.env["res.partner"].create({"name": "Test Vendor"})

    @classmethod
    def _make_user(cls, login: str, name: str, group_xmlids: list[str]):
        groups = [cls.env.ref(x).id for x in group_xmlids]
        return cls.env["res.users"].create(
            {
                "login": login,
                "name": name,
                "email": f"{login}@example.com",
                "group_ids": [(6, 0, groups)],
            }
        )

    def _make_matrix(
        self,
        name: str,
        model_xmlid: str = "purchase.model_purchase_order",
        priority: int = 10,
        condition_domain: str = "[]",
    ):
        return self.Matrix.create(
            {
                "name": name,
                "model_id": self.env.ref(model_xmlid).id,
                "priority": priority,
                "condition_domain": condition_domain,
                "trigger": "manual",
            }
        )

    def _add_tier(
        self,
        matrix,
        *,
        sequence: int = 10,
        name: str = "Tier",
        approvers=None,
        sla_hours: float = 24.0,
        on_overdue: str = "escalate_to_next",
        require_all: bool = False,
        escalation_user=None,
    ):
        return self.Tier.create(
            {
                "matrix_id": matrix.id,
                "sequence": sequence,
                "name": name,
                "approver_type": "user",
                "approver_ids": [(6, 0, [u.id for u in (approvers or [])])],
                "sla_hours": sla_hours,
                "on_overdue": on_overdue,
                "require_all": require_all,
                "escalation_user_id": escalation_user.id if escalation_user else False,
            }
        )

    def _make_po(self, vendor=None):
        return self.PurchaseOrder.with_user(self.user_requester).create(
            {
                "partner_id": (vendor or self.vendor).id,
            }
        )
