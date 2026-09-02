# -*- coding: utf-8 -*-
"""Tests for ``_mdm_split_size`` — the MDM ``size`` string to (Size, Inseam) split.

Why this is worth its own suite: the values this returns must be byte-identical to
the ones the X101 XLSX import writes, because they are looked up as
``product.attribute.value`` names. A split that returns ``"32"``/``"28"`` where the
file wrote ``"32"``/``"28"`` produces the same variant; one that returns ``"3228"``
silently creates a second attribute value, a second variant, and a SKU that no X24DN
sales row will ever resolve against.

So the parser does not trust the free-text ``size`` field on its own. X101 composes
its PROD SKU as ``PRODUCT_CODE without dashes + "0" + SIZE + INSEAM``, which makes the
SKU itself the authority; ``size`` only supplies the *boundary* between the two parts.
The third element of the return value is that agreement check — False means the two
source fields contradict each other and a human should look.
"""

from __future__ import annotations

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestMdmSplitSize(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Executor = cls.env["retail.import.executor"]

    def _split(self, size, sku=None, code=None):
        return self.Executor._mdm_split_size(size, sku, code)

    # -- the shapes the feed actually sends -----------------------------
    def test_waist_and_inseam(self):
        self.assertEqual(self._split("32 28", "002IJ002703228", "002IJ-0027"), ("32", "28", True))

    def test_alternate_separators(self):
        for raw in ("32/28", "32x28", "32*28", "32  28"):
            with self.subTest(raw=raw):
                self.assertEqual(self._split(raw, "002IJ002703228", "002IJ-0027"), ("32", "28", True))

    def test_letter_size_has_no_inseam(self):
        """X101 stores a missing inseam as '-', i.e. it contributes nothing."""
        self.assertEqual(self._split("L", "0002E00310L", "0002E-0031"), ("L", "", True))

    def test_one_size_fits_all(self):
        self.assertEqual(self._split("OS", "000AB00010OS", "000AB-0001"), ("OS", "", True))

    def test_waist_only_bottom(self):
        self.assertEqual(self._split("34", "000YB0001034", "000YB-0001"), ("34", "", True))

    def test_float_shaped_size_is_normalised(self):
        """A numeric cell can arrive as 34.0 or as the string "34.0"."""
        self.assertEqual(self._split(34.0, "000YB0001034", "000YB-0001"), ("34", "", True))
        self.assertEqual(self._split("34.0", "000YB0001034", "000YB-0001"), ("34", "", True))

    def test_missing_separator_is_split_on_the_sku(self):
        """MDM omitted the space: "3228" against tail "3228" is a waist/inseam pair."""
        self.assertEqual(self._split("3228", "000LO000203228", "000LO-0002"), ("32", "28", True))

    def test_inseam_first_is_taken_as_sent(self):
        """The SKU decides the order, not our assumption about which is the waist."""
        self.assertEqual(self._split("28 32", "000LO000202832", "000LO-0002"), ("28", "32", True))

    # -- disagreement is reported, never guessed away --------------------
    def test_contradicting_size_defers_to_the_sku_but_flags(self):
        size, inseam, ok = self._split("99 99", "002IJ002703228", "002IJ-0027")
        self.assertEqual((size, inseam), ("32", "28"), "the SKU is authoritative")
        self.assertFalse(ok, "the contradiction must be surfaced for review")

    def test_empty_size_is_flagged(self):
        self.assertEqual(self._split("", "002IJ002703228", "002IJ-0027"), ("", "", False))

    def test_no_sku_falls_back_to_tokens(self):
        """Callers without a SKU (partial payloads, unit callers) still get a split."""
        self.assertEqual(self._split("M"), ("M", "", True))
        self.assertEqual(self._split("32 28"), ("32", "28", True))

    def test_sku_not_matching_the_template_code_falls_back(self):
        """A SKU that is not composed from this template code carries no usable tail."""
        self.assertEqual(self._split("32 28", "SOMETHINGELSE", "002IJ-0027"), ("32", "28", True))

    # -- the composition rule the parser is built on ---------------------
    def test_composition_rule_round_trips(self):
        """size + inseam must rebuild the PROD SKU, or the variant will not resolve."""
        samples = [
            ("002IJ-0027", "002IJ002703228", "32 28"),
            ("0002E-0031", "0002E00310L", "L"),
            ("000LO-0002", "000LO000202832", "28 32"),
            ("000YB-0001", "000YB0001034", "34"),
        ]
        for code, sku, raw in samples:
            with self.subTest(sku=sku):
                size, inseam, ok = self._split(raw, sku, code)
                self.assertTrue(ok)
                rebuilt = code.replace("-", "") + "0" + size + inseam
                self.assertEqual(rebuilt, sku)
