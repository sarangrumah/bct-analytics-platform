# -*- coding: utf-8 -*-
from datetime import datetime, timedelta

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestRentalPricing(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmpl = cls.env["product.template"].create(
            {
                "name": "Test Rentable",
                "is_rentable": True,
            }
        )
        Pricing = cls.env["custom.rental.pricing"]
        cls.day_tier = Pricing.create(
            {
                "product_template_id": cls.tmpl.id,
                "duration": 1,
                "unit": "day",
                "price": 100.0,
            }
        )
        cls.week_tier = Pricing.create(
            {
                "product_template_id": cls.tmpl.id,
                "duration": 1,
                "unit": "week",
                "price": 500.0,
            }
        )

    def test_single_day(self):
        start = datetime(2025, 1, 1, 9, 0)
        end = start + timedelta(hours=12)
        price = self.env["custom.rental.pricing"]._get_rental_price(self.tmpl, start, end)
        # 12 hours < 1 day -> floor to smallest tier
        self.assertEqual(price, 100.0)

    def test_one_week_uses_week_tier(self):
        start = datetime(2025, 1, 1)
        end = start + timedelta(days=7)
        price = self.env["custom.rental.pricing"]._get_rental_price(self.tmpl, start, end)
        # Should pick the week tier
        self.assertEqual(price, 500.0)

    def test_mixed_period(self):
        start = datetime(2025, 1, 1)
        end = start + timedelta(days=9)  # 1 week + 2 days
        price = self.env["custom.rental.pricing"]._get_rental_price(self.tmpl, start, end)
        self.assertEqual(price, 500.0 + 2 * 100.0)
