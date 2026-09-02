# -*- coding: utf-8 -*-
"""Rule resolution + effective-tarif logic."""

from __future__ import annotations

from .common import TaxIdCommon


class TestWithholdingRule(TaxIdCommon):
    def test_resolve_returns_rule_when_product_matches(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        line = bill.invoice_line_ids[0]
        rule = self.Rule._resolve_for_line(line)
        # Product carries x_custom_withholding_category_id but our resolver
        # uses product CATEGORY, not the wh category on product. The seeded
        # rule doesn't restrict by category so it should match.
        self.assertEqual(rule, self.rule_konsultan)

    def test_resolve_skips_foreign_only_when_domestic(self):
        # Build a foreign-only rule
        cat = self.category_pph26_jasa
        r_foreign = self.Rule.create(
            {
                "name": "PPh 26 LN",
                "category_id": cat.id,
                "tarif": 20.0,
                "foreign_only": True,
                "priority": 80,
                "account_id": self.hutang_pph_23.id,
                "active": True,
            }
        )
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        rule = self.Rule._resolve_for_line(bill.invoice_line_ids[0])
        # Should still pick the konsultan rule, not the foreign-only one
        self.assertEqual(rule, self.rule_konsultan)

    def test_resolve_picks_foreign_rule_for_foreign_partner(self):
        cat = self.category_pph26_jasa
        r_foreign = self.Rule.create(
            {
                "name": "PPh 26 LN",
                "category_id": cat.id,
                "tarif": 20.0,
                "foreign_only": True,
                "priority": 80,
                "account_id": self.hutang_pph_23.id,
                "active": True,
            }
        )
        bill = self._make_vendor_bill(self.vendor_foreign, 1_000_000)
        rule = self.Rule._resolve_for_line(bill.invoice_line_ids[0])
        self.assertEqual(rule, r_foreign)

    def test_effective_tarif_npwp_vs_no_npwp(self):
        self.assertEqual(self.rule_konsultan._effective_tarif(self.vendor_npwp), 2.0)
        self.assertEqual(self.rule_konsultan._effective_tarif(self.vendor_no_npwp), 4.0)

    def test_resolve_returns_empty_for_sales_invoice(self):
        # Sales invoice → no withholding applies on our side
        sale = self.Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": self.vendor_npwp.id,
            }
        )
        sale_line = self.env["account.move.line"]
        # No invoice line yet — _resolve_for_line should handle gracefully
        rule = self.Rule._resolve_for_line(sale_line)
        self.assertFalse(rule)

    # ------------------------------------------------------------------
    # No-guess resolution: an unfiltered rule states no condition, so it must
    # never be matched against a line. Before this, the 107-rule EBR registry
    # (all unfiltered) made every bill line resolve to whichever rule sorted
    # first — "Deviden 15%" — whatever the vendor actually billed us for.
    # ------------------------------------------------------------------

    def _plain_product(self):
        return self.Product.create({"name": "Kardus", "type": "consu"})

    def test_unfiltered_rule_never_matches_by_itself(self):
        # Product carries no PPh mapping and no rule states a condition.
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000, product=self._plain_product())
        rule = self.Rule._resolve_for_line(bill.invoice_line_ids[0])
        self.assertFalse(rule, "An unfiltered rule must not be guessed onto an unmapped line.")

    def test_unmapped_line_is_not_withheld_on_post(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000, product=self._plain_product())
        bill.action_post()
        self.assertFalse(bill.x_custom_withholding_line_ids, "Buying cardboard must not withhold PPh.")
        self.assertFalse(bill.x_custom_withholding_move_id, "No GL entry for an unmapped line.")

    def test_explicit_pick_on_line_wins(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000, product=self._plain_product())
        bill.invoice_line_ids[0].x_custom_withholding_category_id = self.category_konsultan
        rule = self.Rule._resolve_for_line(bill.invoice_line_ids[0])
        self.assertEqual(rule, self.rule_konsultan)

    def test_explicit_pick_beats_product_mapping(self):
        cat_sewa = self.category_sewa
        rule_sewa = self.Rule.create(
            {
                "name": "PPh 23 sewa (test)",
                "category_id": cat_sewa.id,
                "tarif": 2.0,
                "company_id": self.company.id,
                "account_id": self.hutang_pph_23.id,
                "active": True,
            }
        )
        # product_jasa maps to konsultan; the operator overrides it to sewa.
        bill = self._make_vendor_bill(self.vendor_npwp, 1_000_000)
        bill.invoice_line_ids[0].x_custom_withholding_category_id = cat_sewa
        self.assertEqual(self.Rule._resolve_for_line(bill.invoice_line_ids[0]), rule_sewa)

    def test_explicit_pick_drives_the_posted_amount(self):
        bill = self._make_vendor_bill(self.vendor_npwp, 10_000_000, product=self._plain_product())
        line = bill.invoice_line_ids[0]
        line.x_custom_withholding_category_id = self.category_konsultan
        bill.action_post()
        self.assertEqual(len(bill.x_custom_withholding_line_ids), 1)
        self.assertEqual(bill.x_custom_withholding_line_ids.base_amount, 10_000_000)
        self.assertEqual(bill.x_custom_withholding_line_ids.tax_amount, 200_000)  # 2% of 10jt
