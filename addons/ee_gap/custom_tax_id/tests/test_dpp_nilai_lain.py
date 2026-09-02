# -*- coding: utf-8 -*-
"""DPP Nilai Lain (PMK 131/2024) reduces the tax base."""

from __future__ import annotations

from odoo.exceptions import ValidationError

from .common import TaxIdCommon


class TestDppNilaiLain(TaxIdCommon):
    def _make_ppn(self, amount, dpp_method="regular", dpp_factor=1.0, dpp_category=None):
        vals = {
            "name": f"PPN test {amount}%",
            "amount_type": "percent",
            "amount": amount,
            "type_tax_use": "purchase",
            # Every assertion below reads as "PPN on top of the given base", so
            # pin tax-excluded rather than inheriting the company default: the
            # the apparel brand companies run account_price_include = tax_included, which
            # would silently reinterpret each base as gross (1,000,000 -> 900,900).
            "price_include_override": "tax_excluded",
            "x_custom_dpp_method": dpp_method,
            "x_custom_dpp_factor": dpp_factor,
        }
        if dpp_category:
            vals["x_custom_dpp_category"] = dpp_category
        return self.env["account.tax"].create(vals)

    def test_regular_dpp_full_base(self):
        tax = self._make_ppn(11.0, dpp_method="regular")
        result = tax.compute_all(1_000_000, quantity=1.0)
        # Regular: PPN = 11% × 1,000,000 = 110,000
        ppn_amount = sum(t["amount"] for t in result["taxes"])
        self.assertEqual(ppn_amount, 110_000)

    def test_nilai_lain_factor_11_12_yields_correct_effective_burden(self):
        # PMK 131/2024 transitional: PPN 12% × (11/12 × base) = 11% × base
        factor = 11 / 12
        tax = self._make_ppn(12.0, dpp_method="nilai_lain", dpp_factor=factor, dpp_category="ppn_efektif_11_12")
        result = tax.compute_all(1_200_000, quantity=1.0)
        ppn = sum(t["amount"] for t in result["taxes"])
        # 12% × (11/12 × 1,200,000) = 12% × 1,100,000 = 132,000
        # = 11% × 1,200,000 = 132,000
        # Float rounding of 11/12 introduces a ~0.05 residue; use absolute
        # delta (1 rupiah = the natural unit) instead of decimal places.
        self.assertAlmostEqual(ppn, 132_000, delta=1.0)

    def test_paket_wisata_factor_0_1(self):
        tax = self._make_ppn(11.0, dpp_method="nilai_lain", dpp_factor=0.10, dpp_category="paket_wisata")
        result = tax.compute_all(10_000_000, quantity=1.0)
        ppn = sum(t["amount"] for t in result["taxes"])
        # 11% × (10% × 10,000,000) = 11% × 1,000,000 = 110,000
        self.assertAlmostEqual(ppn, 110_000, places=2)

    def test_nilai_lain_requires_positive_factor(self):
        with self.assertRaises(ValidationError):
            self._make_ppn(11.0, dpp_method="nilai_lain", dpp_factor=0.0)
