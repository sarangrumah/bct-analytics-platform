# -*- coding: utf-8 -*-
from __future__ import annotations

import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged


_SIG = base64.b64encode(b"fake-png-bytes").decode("ascii")


@tagged("post_install", "-at_install")
class TestCustomBast(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Bast = cls.env["custom.bast.document"]
        cls.partner_from = cls.env["res.partner"].create({"name": "Warehouse A"})
        cls.partner_to = cls.env["res.partner"].create({"name": "Customer B"})

    def _new_bast(self, **overrides):
        vals = {
            "kind": "delivery",
            "party_from_id": self.partner_from.id,
            "party_to_id": self.partner_to.id,
            "line_ids": [(0, 0, {"item_description": "Box of widgets", "qty": 5.0})],
        }
        vals.update(overrides)
        return self.Bast.create(vals)

    def test_create_and_sequence(self):
        bast = self._new_bast()
        self.assertTrue(bast.name.startswith("BAST/"))
        self.assertEqual(bast.state, "draft")
        self.assertEqual(len(bast.line_ids), 1)

    def test_distinct_parties_constraint(self):
        with self.assertRaises(ValidationError):
            self._new_bast(party_to_id=self.partner_from.id)

    def test_sign_one_side_transitions(self):
        bast = self._new_bast()
        bast.action_sign_from(_SIG, signed_by="Driver")
        self.assertEqual(bast.state, "signed_one_side")
        self.assertTrue(bast.party_from_signed_at)
        self.assertFalse(bast.party_to_signed_at)

    def test_sign_both_completes(self):
        bast = self._new_bast()
        bast.action_sign_from(_SIG, signed_by="Driver")
        bast.action_sign_to(_SIG, signed_by="Receiver", gps=(-6.2, 106.8))
        self.assertEqual(bast.state, "completed")
        self.assertAlmostEqual(bast.gps_latitude, -6.2, places=4)
        self.assertAlmostEqual(bast.gps_longitude, 106.8, places=4)

    def test_void_blocks_resign(self):
        bast = self._new_bast()
        bast.action_sign_from(_SIG)
        bast.action_void(reason="duplicate")
        self.assertEqual(bast.state, "voided")
        with self.assertRaises(UserError):
            bast.action_sign_to(_SIG)
