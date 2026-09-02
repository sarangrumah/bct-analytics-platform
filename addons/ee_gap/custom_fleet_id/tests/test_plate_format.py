# -*- coding: utf-8 -*-
"""Indonesia license plate format constraint (warning, non-blocking)."""

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase, tagged

from ..models.fleet_vehicle import ID_PLATE_REGEX


@tagged("post_install", "-at_install")
class TestPlateFormat(TransactionCase):
    def setUp(self):
        super().setUp()
        self.Vehicle = self.env["fleet.vehicle"]
        # Get a usable brand/model from the demo or create one minimally.
        Model = self.env["fleet.vehicle.model"]
        Brand = self.env["fleet.vehicle.model.brand"]
        self.brand = Brand.search([], limit=1) or Brand.create({"name": "TestBrand"})
        self.model = Model.search([("brand_id", "=", self.brand.id)], limit=1) or Model.create(
            {"name": "TestModel", "brand_id": self.brand.id}
        )

    # ---------- regex unit tests ----------

    def test_regex_accepts_valid_plates(self):
        for plate in ("B 1234 ABC", "AB 12 X", "DK 9999 ZZZ", "L 1 A"):
            self.assertTrue(ID_PLATE_REGEX.match(plate), f"should accept {plate!r}")

    def test_regex_rejects_invalid_plates(self):
        for plate in (
            "B1234ABC",  # missing spaces
            "B 12345 ABC",  # too many digits
            "ABC 1234 AB",  # 3 area letters
            "B 1234 ABCD",  # 4 trailing letters
            "b 1234 abc",  # lowercase (validator uppercases first)
            "",  # empty handled by caller
            " B 1234 ABC",  # leading space
        ):
            # The validator strips & uppercases, so "b 1234 abc" actually
            # becomes "B 1234 ABC" which IS valid. We only test the raw
            # regex here, so lowercase rejection is correct.
            self.assertFalse(ID_PLATE_REGEX.match(plate), f"should reject {plate!r}")

    # ---------- behaviour tests ----------

    def test_invalid_plate_does_not_block_write(self):
        """Default behaviour: warning only, write must succeed."""
        v = self.Vehicle.create(
            {
                "model_id": self.model.id,
                "license_plate": "INVALID-FORMAT",
            }
        )
        # Should not raise; record exists
        self.assertTrue(v.exists())
        self.assertEqual(v.license_plate, "INVALID-FORMAT")

    def test_valid_plate_passes_without_warning(self):
        v = self.Vehicle.create(
            {
                "model_id": self.model.id,
                "license_plate": "B 1234 ABC",
            }
        )
        self.assertEqual(v.license_plate, "B 1234 ABC")

    def test_strict_context_raises_userror(self):
        """With ctx flag, invalid plate must raise UserError."""
        with self.assertRaises(UserError):
            self.Vehicle.with_context(custom_fleet_id_strict_plate=True).create(
                {
                    "model_id": self.model.id,
                    "license_plate": "BAD PLATE",
                }
            )

    def test_lowercase_input_is_normalized_for_check(self):
        """Validator uppercases before matching, so lowercase valid plates pass."""
        v = self.Vehicle.with_context(custom_fleet_id_strict_plate=True).create(
            {
                "model_id": self.model.id,
                "license_plate": "b 1234 abc",
            }
        )
        # Does not raise -> valid via case-insensitive logic
        self.assertTrue(v.exists())
