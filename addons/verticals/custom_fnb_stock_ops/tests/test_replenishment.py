# -*- coding: utf-8 -*-
"""Replenishment: the quantity maths, the approval gate, and the three payloads."""

from __future__ import annotations

from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from .common import FnbTestCase


@tagged("post_install", "-at_install", "esb", "fnb")
class TestReplenishment(FnbTestCase):
    def setUp(self):
        super().setUp()
        self.Proposal = self.env["custom.fnb.replenishment.proposal"]
        self.Rule = self.env["custom.fnb.replenishment.rule"]
        self.supplier = self.env["custom.esb.supplier"].search([("esb_supplier_id", "=", 41)])

    def given_rule(self, product=None, **overrides):
        vals = {
            "branch_id": self.branch.id,
            "product_id": (product or self.ayam).id,
            "esb_location_id": self.kitchen.id,
            "lead_time_days": 2,
            "review_period_days": 5,
            "service_level": 95,
        }
        vals.update(overrides)
        return self.Rule.create(vals)

    def expect_purchase_request(self):
        self.transport.register(
            "GET",
            "/purchase/purchase-request",
            {"status": "ok", "code": "EC03100000", "result": {"page": 1, "limit": 20, "count": 0, "data": None}},
        )
        self.transport.register(
            "POST",
            "/purchase/purchase-request",
            {"status": "ok", "code": "EC03100000", "result": {"purchaseRequestNum": "PR202607210001"}},
        )

    # -- the quantity -------------------------------------------------

    def test_need_is_demand_over_cover_plus_safety_minus_on_hand(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 20)
        rule = self.given_rule(lead_time_days=2, review_period_days=5)

        line_vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertIsNone(skip)
        # 7 days cover x 10/day = 70, flat series so safety = 0, minus 20 on hand.
        self.assertAlmostEqual(line_vals["forecast_horizon_qty"], 70.0)
        self.assertAlmostEqual(line_vals["safety_stock"], 0.0)
        self.assertAlmostEqual(line_vals["qty"], 50.0)

    def test_unknown_on_hand_skips_the_line_rather_than_assuming_zero(self):
        """The costliest possible default: ordering a full cover for stock the
        outlet may already be holding."""
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        rule = self.given_rule()  # no snapshot at all

        line_vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertIsNone(line_vals)
        self.assertEqual(skip, "unknown_on_hand")

    def test_zero_on_hand_is_honoured_when_esb_actually_reported_it(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0.0)
        rule = self.given_rule()

        line_vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertIsNone(skip, "a reported zero is a real balance, unlike a missing row")
        self.assertAlmostEqual(line_vals["qty"], 70.0)

    def test_no_forecast_means_no_proposal(self):
        self.given_snapshot(self.kitchen, self.ayam, 0)
        rule = self.given_rule()

        _vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertEqual(skip, "no_forecast")

    def test_sufficient_stock_proposes_nothing(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 500)
        rule = self.given_rule()

        _vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertEqual(skip, "sufficient")

    def test_order_multiple_rounds_up(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 20)
        rule = self.given_rule(order_multiple=12)

        line_vals, _skip = self.Proposal._evaluate_rule(rule)

        self.assertAlmostEqual(line_vals["raw_need"], 50.0)
        self.assertAlmostEqual(line_vals["qty"], 60.0, msg="50 rounds up to five cases of 12")

    def test_minimum_is_applied_before_pack_rounding(self):
        """A supplier minimum of 10 with a pack of 4 must give 12, not 10."""
        rule = self.given_rule(min_order_qty=10, order_multiple=4)

        self.assertAlmostEqual(rule.round_qty(3), 12.0)

    def test_max_qty_caps_the_order(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        rule = self.given_rule(max_qty=25)

        line_vals, _skip = self.Proposal._evaluate_rule(rule)

        self.assertAlmostEqual(line_vals["qty"], 25.0)

    def test_on_order_is_netted_off(self):
        """Otherwise every cron run re-proposes the same quantity."""
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 20)
        rule = self.given_rule()
        self.expect_purchase_request()
        first = self.Proposal.generate_proposals(rule)
        first.action_approve()

        line_vals, skip = self.Proposal._evaluate_rule(rule)

        self.assertEqual(skip, "sufficient", "the 50 already ordered covers the need")
        self.assertIsNone(line_vals)

    def test_min_qty_floors_the_target(self):
        self.given_demand(self.branch, self.ayam, [1] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        rule = self.given_rule(min_qty=100)

        line_vals, _skip = self.Proposal._evaluate_rule(rule)

        self.assertAlmostEqual(line_vals["qty"], 100.0)

    def test_branch_wide_on_hand_sums_every_location(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 20)
        self.given_snapshot(self.chiller, self.ayam, 30)
        rule = self.given_rule(esb_location_id=False)

        line_vals, _skip = self.Proposal._evaluate_rule(rule)

        self.assertAlmostEqual(line_vals["on_hand_qty"], 50.0)

    # -- grouping and workflow ----------------------------------------

    def test_lines_group_into_one_document_per_supplier(self):
        for product in (self.ayam, self.beras):
            self.given_demand(self.branch, product, [10] * 30)
            self.given_forecast(self.branch, product, "moving_average")
            self.given_snapshot(self.kitchen, product, 0)
        self.given_rule(self.ayam)
        self.given_rule(self.beras)

        proposals = self.Proposal.generate_proposals()

        self.assertEqual(len(proposals), 1, "same branch, same target doc → one document")
        self.assertEqual(len(proposals.line_ids), 2)

    def test_different_targets_produce_different_documents(self):
        for product in (self.ayam, self.beras):
            self.given_demand(self.branch, product, [10] * 30)
            self.given_forecast(self.branch, product, "moving_average")
            self.given_snapshot(self.kitchen, product, 0)
        self.given_rule(self.ayam, target_doc="purchase_request")
        self.given_rule(self.beras, target_doc="goods_transfer_request", source_branch_id=self.hub.id)

        proposals = self.Proposal.generate_proposals()

        self.assertEqual(len(proposals), 2)
        self.assertEqual(set(proposals.mapped("target_doc")), {"purchase_request", "goods_transfer_request"})

    def test_proposals_start_as_drafts_and_push_nothing(self):
        """The whole point of the gate."""
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule()

        proposal = self.Proposal.generate_proposals()

        self.assertEqual(proposal.state, "draft")
        self.assertFalse(proposal.esb_outbox_id)
        self.assertEqual(self.transport.count("POST", "/purchase/purchase-request"), 0)

    def test_approval_is_what_creates_the_esb_document(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule()
        proposal = self.Proposal.generate_proposals()
        self.expect_purchase_request()

        proposal.action_approve()

        self.assertEqual(proposal.state, "pushed")
        self.assertEqual(proposal.approved_by_id, self.env.user)
        # The push is a queue_job; drive it to prove the document really lands.
        proposal.esb_outbox_id.action_push_now()
        self.assertEqual(proposal.esb_doc_num, "PR202607210001")
        self.assertEqual(self.transport.count("POST", "/purchase/purchase-request"), 1)

    def test_cancelled_proposal_never_reaches_esb(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule()
        proposal = self.Proposal.generate_proposals()

        proposal.action_cancel()

        self.assertEqual(proposal.state, "cancelled")
        self.assertEqual(self.transport.count("POST", "/purchase/purchase-request"), 0)

    def test_unreliable_forecast_is_surfaced_for_review(self):
        self.given_demand(self.branch, self.ayam, [10] * 3)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule()

        proposal = self.Proposal.generate_proposals()

        self.assertTrue(proposal.has_unreliable_forecast, "three days of history must be flagged, not hidden")

    def test_proposal_line_records_the_full_derivation(self):
        """A planner has to be able to audit why the number is what it is."""
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 20)
        self.given_rule()

        line = self.Proposal.generate_proposals().line_ids

        self.assertAlmostEqual(line.forecast_daily_qty, 10.0)
        self.assertEqual(line.cover_days, 7)
        self.assertAlmostEqual(line.on_hand_qty, 20.0)
        self.assertAlmostEqual(line.raw_need, 50.0)

    # -- payloads ------------------------------------------------------

    def _one_line_proposal(self, **rule_kw):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule(**rule_kw)
        return self.Proposal.generate_proposals()

    def test_purchase_request_payload(self):
        proposal = self._one_line_proposal()

        payload = proposal._esb_payload()

        self.assertEqual(payload["branchID"], 373)
        self.assertFalse(payload["isTemplate"])
        detail = payload["purchaseRequestDetails"][0]
        self.assertEqual(detail["productDetailID"], 2112, "the stock unit's detail")
        self.assertEqual(detail["requestProcessID"], 2, "2 = Purchase")
        self.assertAlmostEqual(detail["qty"], 70.0)

    def test_transfer_request_payload_uses_the_transfer_unit(self):
        proposal = self._one_line_proposal(target_doc="goods_transfer_request", source_branch_id=self.hub.id)

        payload = proposal._esb_payload()

        self.assertEqual(payload["originBranchID"], 1)
        self.assertEqual(payload["destinationBranchID"], 373)
        self.assertEqual(payload["categoryTypeID"], 1)
        self.assertEqual(payload["transferDetails"][0]["requestQty"], 0)

    def test_purchase_order_payload_uses_the_purchase_unit_and_esb_spelling(self):
        proposal = self._one_line_proposal(target_doc="purchase_order", supplier_id=self.supplier.id, unit_price=45000)

        payload = proposal._esb_payload()

        self.assertEqual(payload["supplierID"], 41)
        self.assertEqual(payload["dueDay"], 30, "the supplier's credit term")
        detail = payload["purchaseDetails"][0]
        self.assertIn("ProductDetailID", detail, "ESB spells this one with a capital P")
        self.assertEqual(detail["ProductDetailID"], 2113, "purchases go out in the purchase unit")
        self.assertEqual(detail["price"], 45000)

    def test_purchase_order_without_a_price_is_refused_with_a_useful_message(self):
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule(target_doc="purchase_order", supplier_id=self.supplier.id)
        proposal = self.Proposal.generate_proposals()
        proposal.line_ids.unit_price = 0

        with self.assertRaises(UserError) as ctx:
            proposal._esb_payload()
        self.assertIn("Purchase Request", str(ctx.exception), "the message offers the safer alternative")

    def test_purchase_order_price_defaults_to_the_esb_base_price(self):
        proposal = self._one_line_proposal(target_doc="purchase_order", supplier_id=self.supplier.id)

        self.assertEqual(proposal.line_ids.unit_price, 45000, "the purchase unit's base price from ESB")

    # -- rule validation -----------------------------------------------

    def test_purchase_order_rule_requires_a_supplier(self):
        with self.assertRaises(ValidationError):
            self.given_rule(target_doc="purchase_order")

    def test_transfer_rule_requires_a_source_branch(self):
        with self.assertRaises(ValidationError):
            self.given_rule(target_doc="goods_transfer_request")

    def test_transfer_rule_cannot_source_from_itself(self):
        with self.assertRaises(ValidationError):
            self.given_rule(target_doc="goods_transfer_request", source_branch_id=self.branch.id)

    def test_service_level_is_bounded(self):
        with self.assertRaises(ValidationError):
            self.given_rule(service_level=140)

    def test_one_rule_per_branch_product(self):
        self.given_rule()
        with self.assertRaises(Exception):
            self.given_rule()
            self.env.flush_all()

    # -- cron ----------------------------------------------------------

    def test_cron_is_a_no_op_while_the_switch_is_off(self):
        self.param.set_param("fnb.replenishment_enabled", "0")
        self.given_demand(self.branch, self.ayam, [10] * 30)
        self.given_forecast(self.branch, self.ayam, "moving_average")
        self.given_snapshot(self.kitchen, self.ayam, 0)
        self.given_rule()

        self.Proposal._cron_generate()

        self.assertEqual(self.Proposal.search_count([]), 0)

    def test_proposal_closes_once_esb_authorizes_the_document(self):
        proposal = self._one_line_proposal()
        self.expect_purchase_request()
        proposal.action_approve()
        proposal.esb_outbox_id.state = "confirmed"

        self.Proposal._cron_close_finished()

        self.assertEqual(proposal.state, "done")
