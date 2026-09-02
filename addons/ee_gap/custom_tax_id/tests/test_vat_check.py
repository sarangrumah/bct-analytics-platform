# -*- coding: utf-8 -*-
"""Relaxed Indonesian NPWP validation on the standard ``vat`` field.

Standalone (does not use TaxIdCommon) so it has no dependency on seed
withholding data — it only needs ``res.partner`` + ``base_vat``.
"""

from __future__ import annotations

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestVatCheckId(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Partner = cls.env["res.partner"]
        cls.id_country = cls.env.ref("base.id")
        # check_vat_id is only invoked when the partner country is Indonesia
        cls.env.company.country_id = cls.id_country

    def test_check_vat_id_accepts_15_and_16_digits(self):
        Partner = self.Partner
        # 15-digit legacy NPWP with leading zero (failed core Luhn before)
        self.assertTrue(Partner.check_vat_id("015556667008000"))
        # 16-digit NIK-based NPWP
        self.assertTrue(Partner.check_vat_id("0123456789012345"))
        # separators (dots/dashes/spaces) are ignored
        self.assertTrue(Partner.check_vat_id("01.234.567.8-901.234"))

    def test_check_vat_id_rejects_bad_input(self):
        Partner = self.Partner
        self.assertFalse(Partner.check_vat_id("123"))  # too short
        self.assertFalse(Partner.check_vat_id("01234567890123456789"))  # too long
        self.assertFalse(Partner.check_vat_id("abc456789012345"))  # non-numeric
        self.assertFalse(Partner.check_vat_id(""))  # empty

    def test_partner_save_with_rejected_npwp(self):
        # The original report: this exact value raised ValidationError via
        # base_vat. With the override it must persist on the `vat` field.
        p = self.Partner.create(
            {
                "name": "PT Aero Reksa Kreasi Angkasa",
                "is_company": True,
                "country_id": self.id_country.id,
                "vat": "015556667008000",
            }
        )
        self.assertEqual(p.vat, "015556667008000")

    def test_partner_write_with_rejected_npwp(self):
        p = self.Partner.create({"name": "Test Partner", "country_id": self.id_country.id})
        p.write({"vat": "015556667008000"})
        self.assertEqual(p.vat, "015556667008000")
