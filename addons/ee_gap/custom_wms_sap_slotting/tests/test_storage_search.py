# -*- coding: utf-8 -*-
"""SAP storage-search progression on a minimal bin lattice.

The lattice is deliberately one bin per (type, section) bucket with room for
exactly one unit, so filling a bin forces the search to its next step and the
whole fallback chain can be walked deterministically in a single test.
"""

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_wms_sap_slotting")
class TestSapStorageSearch(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env = cls.env(context=dict(cls.env.context, tracking_disable=True))

        cls.warehouse = cls.env["stock.warehouse"].create({"name": "SAP Test WH", "code": "SAPT"})
        cls.stock = cls.warehouse.lot_stock_id

        ref = cls.env.ref
        cls.types = {
            code: ref(f"custom_wms_sap_slotting.stype_{code.lower()}") for code in ("FO1", "FO2", "AC1", "AP1", "FL1")
        }
        cls.sections = {code: ref(f"custom_wms_sap_slotting.ssec_{code.lower()}") for code in ("RU1", "SS1", "GA2")}

        # One bin per bucket, each holding exactly one unit of the test product.
        cls.bins = {}
        for name, type_code, section_code in (
            ("FO1-RU1", "FO1", "RU1"),
            ("FO1-SS1", "FO1", "SS1"),
            ("FO2-GA2", "FO2", "GA2"),
            ("AC1-RU1", "AC1", "RU1"),
            ("AP1-RU1", "AP1", "RU1"),
            ("FL1-GA2", "FL1", "GA2"),
        ):
            cls.bins[name] = cls.env["stock.location"].create(
                {
                    "name": name,
                    "location_id": cls.stock.id,
                    "usage": "internal",
                    "wms_storage_type_id": cls.types[type_code].id,
                    "wms_storage_section_id": cls.sections[section_code].id,
                    "wms_volume_ccm": 1000.0,
                }
            )

        # Bins outside the search: no classification at all.
        cls.damage = cls.env["stock.location"].create(
            {"name": "G.DAMAGE", "location_id": cls.stock.id, "usage": "internal"}
        )
        cls.overflow = cls.env["stock.location"].create(
            {"name": "OVERFLOW", "location_id": cls.stock.id, "usage": "internal"}
        )

        cls.product = cls.env["product.product"].create(
            {
                "name": "Test Runner",
                "type": "consu",
                "is_storable": True,
                "wms_storage_type_id": cls.types["FO1"].id,
                "wms_storage_section_id": cls.sections["RU1"].id,
                "wms_volume_ccm": 1000.0,
            }
        )

        cls.strategy = cls.env["custom.wms.putaway.strategy"].create(
            {
                "name": "SAPT Storage Search",
                "warehouse_id": cls.warehouse.id,
                "rule_set": "custom",
            }
        )
        cls.rule = cls.env["custom.wms.putaway.rule"].create(
            {
                "name": "T1 SAP Storage Search",
                "strategy_id": cls.strategy.id,
                "kind": "sap_storage_search",
                "tier": 1,
                "target_location_domain": f"[('id', 'child_of', {cls.stock.id})]",
                "sap_fail_action": "overflow",
                "sap_overflow_location_id": cls.overflow.id,
            }
        )

    def _propose(self):
        return self.env["custom.putaway.engine"].propose_for_product(self.product, self.warehouse, 1.0)

    def _fill(self, bin_name):
        """Occupy a bin to its cm3 ceiling so the search must move on."""
        self.env["stock.quant"]._update_available_quantity(self.product, self.bins[bin_name], 1.0)

    def test_fallback_chain(self):
        """Walk the whole chain, asserting both the bin and the exact score.

        Expected progression for an FO1/RU1 product, with the shipped penalties
        (type 12, section 1). Section indices are positions in the RU1 sequence
        [RU1, GA2, SL1, SS1, TR1, BB1, GF1, GO1, LS1, OD1].
        """
        expected = [
            # (bin, score, type step, section step)
            ("FO1-RU1", 100),  # i=0 j=0 -- exact
            ("FO1-SS1", 97),  # i=0 j=3 -- GA2/SL1 buckets do not exist, skipped free
            ("FO2-GA2", 87),  # i=1 j=1 -- first storage-type fallback
            ("AC1-RU1", 76),  # i=2 j=0
            ("AP1-RU1", 64),  # i=3 j=0
            ("FL1-GA2", 51),  # i=4 j=1 -- floor, last resort
        ]
        for bin_name, score in expected:
            proposals = self._propose()
            self.assertTrue(proposals, f"no proposal before reaching {bin_name}")
            top = proposals[0]
            self.assertEqual(
                top["location_id"],
                self.bins[bin_name].id,
                f"expected {bin_name}, got {self.env['stock.location'].browse(top['location_id']).name}",
            )
            self.assertEqual(top["score"], score, f"wrong score for {bin_name}: {top['reason']}")
            self._fill(bin_name)

        # Every classified bin is full: the overflow location takes over, at a
        # score low enough that it can never auto-apply.
        proposals = self._propose()
        self.assertTrue(proposals, "exhausted search produced no overflow proposal")
        self.assertEqual(proposals[0]["location_id"], self.overflow.id)
        self.assertEqual(proposals[0]["score"], 40)
        self.assertLess(proposals[0]["score"], self.env["custom.putaway.engine"]._auto_apply_threshold())

    def test_unclassified_bins_are_unreachable(self):
        """A bin with no storage type/section is invisible to the search."""
        for bin_name in self.bins:
            self._fill(bin_name)
        proposals = self._propose()
        self.assertTrue(proposals)
        self.assertNotEqual(proposals[0]["location_id"], self.damage.id)

    def test_no_overflow_falls_through(self):
        """With ``sap_fail_action='none'`` an exhausted search yields nothing."""
        self.rule.write({"sap_fail_action": "none", "sap_overflow_location_id": False})
        for bin_name in self.bins:
            self._fill(bin_name)
        self.assertFalse(self._propose())

    def test_consolidation_prefers_occupied_bin(self):
        """A partly-filled bin beats an empty peer of the same bucket."""
        second = self.env["stock.location"].create(
            {
                "name": "FO1-RU1-B",
                "location_id": self.stock.id,
                "usage": "internal",
                "wms_storage_type_id": self.types["FO1"].id,
                "wms_storage_section_id": self.sections["RU1"].id,
                "wms_volume_ccm": 5000.0,
                "wms_walk_sequence": 1,
            }
        )
        self.env["stock.quant"]._update_available_quantity(self.product, second, 1.0)
        top = self._propose()[0]
        self.assertEqual(top["location_id"], second.id, "consolidation should win over an empty bin")

    def test_search_sequences_match_source(self):
        """The shipped sequences are the ones from the client's STORAGE SEARCH sheet."""
        expected_types = {
            "AC1": ["AC1", "AC2", "AP1", "FO1", "FL1"],
            "AP1": ["AP1", "AP2", "AC1", "FO1", "FL1"],
            "FO1": ["FO1", "FO2", "AC1", "AP1", "FL1"],
            "AC2": ["AC2", "AP2", "FO2", "FL1"],
            "AP2": ["AP2", "AC2", "FO2", "FL1"],
            "FO2": ["FO2", "AC2", "AP2", "FL1"],
            "FL1": ["FL1"],
        }
        for code, sequence in expected_types.items():
            record = self.env.ref(f"custom_wms_sap_slotting.stype_{code.lower()}")
            self.assertEqual(record._search_sequence().mapped("code"), sequence)

        expected_sections = {
            "BB1": ["BB1", "GA2", "GF1", "GO1", "LS1", "OD1", "RU1", "SL1", "SS1", "TR1"],
            "RU1": ["RU1", "GA2", "SL1", "SS1", "TR1", "BB1", "GF1", "GO1", "LS1", "OD1"],
            "TR1": ["TR1", "GA2", "BB1", "GF1", "GO1", "LS1", "OD1", "RU1", "SL1", "SS1"],
            "GA2": ["GA2"],
        }
        for code, sequence in expected_sections.items():
            record = self.env.ref(f"custom_wms_sap_slotting.ssec_{code.lower()}")
            self.assertEqual(record._search_sequence().mapped("code"), sequence)
