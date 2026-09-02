# -*- coding: utf-8 -*-
"""Faktur Pengganti chain + kode_status increment."""

from __future__ import annotations

from odoo.exceptions import UserError
from odoo.tests import tagged

from .common import TaxIdCommon


@tagged("post_install", "-at_install")
class TestFakturPengganti(TaxIdCommon):
    def _make_sales_invoice(self):
        return self.Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": self.vendor_npwp.id,
                "invoice_date": "2026-01-15",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": "Sale of jasa",
                            "quantity": 1.0,
                            "price_unit": 2_000_000,
                            "account_id": self.expense_account.id,
                        },
                    )
                ],
            }
        )

    def _post_with_nsfp(self, move, nsfp: str = "00012620000000001"):
        move.action_post()
        # Simulate Coretax handing back an NSFP after approval
        if hasattr(move, "x_custom_nsfp"):
            move.write({"x_custom_nsfp": nsfp})
        return move

    def test_first_pengganti_increments_kode_status(self):
        src = self._post_with_nsfp(self._make_sales_invoice())
        wizard = self.env["tax.faktur.pengganti.wizard"].create(
            {
                "source_move_id": src.id,
                "reason": "Salah NPWP pembeli",
            }
        )
        self.assertEqual(wizard.next_kode_status, "01")
        wizard.action_create_replacement()
        replacement = src.x_custom_coretax_replaced_by_id
        self.assertTrue(replacement)
        self.assertEqual(replacement.x_custom_coretax_kode_status, "01")
        self.assertEqual(replacement.x_custom_coretax_replacement_of_id, src)

    def test_second_pengganti_increments_to_02(self):
        src = self._post_with_nsfp(self._make_sales_invoice(), nsfp="01012620000000001")  # kode_status=01
        wizard = self.env["tax.faktur.pengganti.wizard"].create(
            {
                "source_move_id": src.id,
                "reason": "Kedua kalinya — Salah jumlah",
            }
        )
        self.assertEqual(wizard.next_kode_status, "02")
        wizard.action_create_replacement()
        repl = src.x_custom_coretax_replaced_by_id
        self.assertEqual(repl.x_custom_coretax_kode_status, "02")

    def test_pengganti_at_09_rejected(self):
        src = self._post_with_nsfp(self._make_sales_invoice(), nsfp="09012620000000001")  # kode_status=09
        wizard = self.env["tax.faktur.pengganti.wizard"].create(
            {
                "source_move_id": src.id,
                "reason": "Mau coba lewat 09",
            }
        )
        with self.assertRaises(UserError):
            wizard.action_create_replacement()

    def test_source_nsfp_cleared_after_pengganti(self):
        src = self._post_with_nsfp(self._make_sales_invoice())
        original_nsfp = src.x_custom_nsfp
        self.assertTrue(original_nsfp)
        self.env["tax.faktur.pengganti.wizard"].create(
            {
                "source_move_id": src.id,
                "reason": "test",
            }
        ).action_create_replacement()
        # NSFP cleared because DJP voids it once pengganti is approved
        self.assertFalse(src.x_custom_nsfp)
