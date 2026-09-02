# -*- coding: utf-8 -*-
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_ppob_sla")
class TestPpobSlaTarget(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Target = cls.env["custom.ppob.sla.target"]
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        assert cls.klass, "TELKO class should be seeded by custom_ppob_core post_init"
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Vendor SLA Test",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.provider = cls.env["custom.ppob.provider"].create(
            {
                "code": "SLATEST",
                "name": "Provider SLA Test",
                "partner_id": cls.vendor.id,
            }
        )
        # The install-seeded wildcard baseline row.
        cls.baseline = cls.env.ref("custom_ppob_sla.sla_target_baseline")

    # ------------------------------------------------------------------
    # Baseline seed
    # ------------------------------------------------------------------

    def test_baseline_is_seeded_as_a_wildcard(self):
        """Install leaves a usable target without any provider existing yet --
        this is what 'ready to go live, waiting for Oracle' means."""
        self.assertTrue(self.baseline)
        self.assertFalse(self.baseline.provider_id)
        self.assertFalse(self.baseline.class_id)
        self.assertEqual(self.baseline.daily_txn_target, 50000)

    def test_baseline_is_labelled_a_guess(self):
        """The seeded numbers must never masquerade as measurements."""
        self.assertEqual(self.baseline.calibration_source, "default_baseline")
        self.assertFalse(self.baseline.calibrated_at)

    def test_peak_tps_derivation(self):
        """50000 / (14h * 3600) * 3.0 = 2.976..."""
        self.assertAlmostEqual(self.baseline.peak_tps_target, 2.9762, places=3)

    def test_peak_tps_recomputes_from_assumptions(self):
        target = self.Target.create(
            {
                "provider_id": self.provider.id,
                "daily_txn_target": 86400,
                "active_hours": 24.0,
                "peak_factor": 1.0,
            }
        )
        # Perfectly flat 86400/day over 24h = exactly 1 TPS.
        self.assertAlmostEqual(target.peak_tps_target, 1.0, places=4)
        target.peak_factor = 5.0
        self.assertAlmostEqual(target.peak_tps_target, 5.0, places=4)

    def test_peak_tps_is_manually_overridable(self):
        """Ops must be able to pin a measured peak that the derivation cannot
        express, without the compute stomping it back."""
        target = self.Target.create(
            {
                "provider_id": self.provider.id,
                "daily_txn_target": 86400,
                "active_hours": 24.0,
                "peak_factor": 1.0,
            }
        )
        target.peak_tps_target = 42.0
        self.assertAlmostEqual(target.peak_tps_target, 42.0, places=4)

    # ------------------------------------------------------------------
    # Assumption guards
    # ------------------------------------------------------------------

    def test_peak_factor_below_one_rejected(self):
        with self.assertRaises(ValidationError):
            self.Target.create(
                {
                    "provider_id": self.provider.id,
                    "peak_factor": 0.5,
                }
            )

    def test_active_hours_over_24_rejected(self):
        with self.assertRaises(ValidationError):
            self.Target.create(
                {
                    "provider_id": self.provider.id,
                    "active_hours": 30.0,
                }
            )

    # ------------------------------------------------------------------
    # Resolution: most specific wins
    # ------------------------------------------------------------------

    def test_resolve_falls_back_to_wildcard(self):
        target = self.Target._resolve(provider=self.provider, product_class=self.klass)
        self.assertEqual(target, self.baseline, "with no scoped row, the wildcard baseline must answer")

    def test_resolve_prefers_provider_over_wildcard(self):
        scoped = self.Target.create({"provider_id": self.provider.id})
        target = self.Target._resolve(provider=self.provider, product_class=self.klass)
        self.assertEqual(target, scoped)

    def test_resolve_prefers_provider_and_class_over_provider_alone(self):
        self.Target.create({"provider_id": self.provider.id})
        exact = self.Target.create(
            {
                "provider_id": self.provider.id,
                "class_id": self.klass.id,
            }
        )
        target = self.Target._resolve(provider=self.provider, product_class=self.klass)
        self.assertEqual(target, exact)

    def test_resolve_prefers_class_over_wildcard(self):
        by_class = self.Target.create({"class_id": self.klass.id})
        target = self.Target._resolve(provider=self.provider, product_class=self.klass)
        self.assertEqual(target, by_class, "a class-scoped row beats the double wildcard")

    def test_resolve_returns_empty_when_nothing_matches(self):
        """A missing target is information, not something to fake a default for."""
        self.baseline.unlink()
        target = self.Target._resolve(provider=self.provider, product_class=self.klass)
        self.assertFalse(target)

    def test_scope_is_unique(self):
        from psycopg2 import IntegrityError
        from odoo.tools import mute_logger

        self.Target.create({"provider_id": self.provider.id, "class_id": self.klass.id})
        with self.assertRaises(IntegrityError), mute_logger("odoo.sql_db"):
            self.Target.create({"provider_id": self.provider.id, "class_id": self.klass.id})
            self.env.flush_all()

    # ------------------------------------------------------------------
    # Calibration promotion
    # ------------------------------------------------------------------

    def test_cannot_mark_baseline_calibrated(self):
        with self.assertRaises(ValidationError):
            self.baseline.action_mark_calibrated()

    def test_cannot_mark_calibrated_without_a_note(self):
        target = self.Target.create(
            {
                "provider_id": self.provider.id,
                "calibration_source": "oracle_historical",
                "calibration_note": False,
            }
        )
        with self.assertRaises(ValidationError):
            target.action_mark_calibrated()

    def test_mark_calibrated_stamps_time(self):
        target = self.Target.create(
            {
                "provider_id": self.provider.id,
                "calibration_source": "oracle_historical",
                "calibration_note": "Derived from MSG016T 2026-01-01..2026-06-30.",
            }
        )
        target.action_mark_calibrated()
        self.assertTrue(target.calibrated_at)
        self.assertEqual(target.calibration_source, "oracle_historical")
