# -*- coding: utf-8 -*-
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "custom_ppob_sla")
class TestPpobThroughputSample(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Sample = cls.env["custom.ppob.throughput.sample"]
        cls.Target = cls.env["custom.ppob.sla.target"]
        Mapping = cls.env["custom.ppob.account.mapping"]
        cls.klass = cls.env["custom.ppob.product.class"].search([("code", "=", "TELKO")], limit=1)
        assert cls.klass, "TELKO class should be seeded by custom_ppob_core post_init"
        cls.bank = Mapping._get_account("cash_bca_escrow", cls.company)
        cls.baseline = cls.env.ref("custom_ppob_sla.sla_target_baseline")

        cls.mitra = cls.env["res.partner"].create(
            {
                "name": "Mitra Throughput Test",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "MTRTHR1",
            }
        )
        cls.vendor = cls.env["res.partner"].create(
            {
                "name": "Vendor Throughput Test",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.product = cls.env["custom.ppob.product"].create(
            {
                "code": "TSELTHR5",
                "name": "TSEL Pulsa 5k (throughput test)",
                "class_id": cls.klass.id,
                "denom": 5000.0,
                "cost_price_default": 4900.0,
            }
        )
        cls.wallet = cls.env["custom.ppob.wallet"].create(
            {
                "partner_id": cls.mitra.id,
                "class_id": cls.klass.id,
            }
        )
        cls.wallet._atomic_credit(
            amount=1_000_000.0,
            reason="seed",
            counterpart_account=cls.bank,
            move_type="topup",
        )
        cls.provider = cls.env["custom.ppob.provider"].create(
            {
                "code": "THRTEST",
                "name": "Provider Throughput Test",
                "partner_id": cls.vendor.id,
                "settlement_mode": "prepaid_deposit",
                "bucket_mode": "bulky",
                "adapter_class": "ppob_mock",
                "mock_outcome": "success",
            }
        )
        cls.provider.action_ensure_buckets()
        cls.provider.bucket_ids._atomic_credit(
            dpp_amount=500_000.0,
            tax_amount=0.0,
            gross_amount=500_000.0,
            reason="seed bucket",
            counterpart_account=cls.bank,
            move_type="topup",
        )
        cls.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": cls.provider.id,
                "product_id": cls.product.id,
                "provider_sku": "SKU-THRTEST",
                "buy_price": 4900.0,
            }
        )

    def _dispatch(self, count=1):
        Txn = self.env["custom.ppob.transaction"]
        txns = Txn.browse()
        for i in range(count):
            txn = Txn.create(
                {
                    "mitra_id": self.mitra.id,
                    "product_id": self.product.id,
                    "msisdn": f"0812000{i:04d}",
                    "sell_price": 5000.0,
                    "cost_price": 4900.0,
                    "idempotency_key": f"THR-{i}",
                }
            )
            txn._dispatch_one()
            txns |= txn
        return txns

    def _current_hour(self):
        return fields.Datetime.now().replace(minute=0, second=0, microsecond=0)

    # ------------------------------------------------------------------
    # Instrumentation (custom_ppob_sale)
    # ------------------------------------------------------------------

    def test_dispatch_records_provider_latency(self):
        """Without this field there is nothing to measure an SLA against --
        and it must be written for every adapter, mock included."""
        txn = self._dispatch(1)
        self.assertEqual(txn.state, "success")
        self.assertIsNotNone(txn.provider_latency_ms)
        self.assertGreaterEqual(txn.provider_latency_ms, 0)

    def test_failed_dispatch_still_records_latency(self):
        """A provider that fails slowly is exactly the sample the SLA needs."""
        self.provider.mock_outcome = "fail"
        txn = self._dispatch(1)
        self.assertEqual(txn.state, "failed")
        self.assertGreaterEqual(txn.provider_latency_ms, 0)

    def test_retry_clone_resets_latency(self):
        txn = self._dispatch(1)
        action = txn.action_retry()
        clone = self.env["custom.ppob.transaction"].browse(action["res_id"])
        self.assertEqual(clone.provider_latency_ms, 0)
        self.assertFalse(clone.dispatched_at)

    # ------------------------------------------------------------------
    # Sampling
    # ------------------------------------------------------------------

    def test_sample_empty_hour_writes_nothing(self):
        """Installing before go-live must be free: no traffic, no rows, no
        crash."""
        samples = self.Sample._sample_hour(self._current_hour() - timedelta(hours=48))
        self.assertFalse(samples)

    def test_sample_counts_dispatched_transactions(self):
        self._dispatch(3)
        samples = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples.txn_count, 3)
        self.assertEqual(samples.success_count, 3)
        self.assertEqual(samples.provider_id, self.provider)
        self.assertEqual(samples.class_id, self.klass)
        self.assertEqual(samples.source, "odoo")
        self.assertAlmostEqual(samples.gross_amount, 15000.0, places=2)

    def test_sample_separates_success_from_failure(self):
        self._dispatch(2)
        self.provider.mock_outcome = "fail"
        Txn = self.env["custom.ppob.transaction"]
        txn = Txn.create(
            {
                "mitra_id": self.mitra.id,
                "product_id": self.product.id,
                "msisdn": "081299999",
                "sell_price": 5000.0,
                "cost_price": 4900.0,
                "idempotency_key": "THR-FAIL",
            }
        )
        txn._dispatch_one()
        samples = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(samples.txn_count, 3)
        self.assertEqual(samples.success_count, 2)
        self.assertEqual(samples.failed_count, 1)
        self.assertAlmostEqual(samples.success_rate_pct, 66.67, places=1)

    def test_peak_tps_is_a_true_per_second_peak(self):
        """Three dispatches inside one test land in the same second, so the peak
        must read 3 even though the hourly mean is ~0.0008."""
        self._dispatch(3)
        samples = self.Sample._sample_hour(self._current_hour())
        self.assertGreaterEqual(samples.peak_tps, 1.0)
        self.assertGreater(samples.peak_tps, samples.mean_tps, "peak must not collapse into the hourly average")

    def test_sample_flushes_before_raw_sql(self):
        """The transactions above are dispatched in this same cursor; without
        env.flush_all() the raw-SQL reader would not see them at all."""
        self._dispatch(1)
        samples = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(samples.txn_count, 1)

    def test_resampling_the_same_hour_is_idempotent(self):
        """A cron that fires twice, or a backfill replayed over an already
        imported range, must not double-count."""
        self._dispatch(2)
        first = self.Sample._sample_hour(self._current_hour())
        second = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(first, second, "the same bucket must be updated, not duplicated")
        self.assertEqual(second.txn_count, 2)
        self.assertEqual(
            self.Sample.search_count(
                [
                    ("source", "=", "odoo"),
                    ("bucket_start", "=", self._current_hour()),
                    ("provider_id", "=", self.provider.id),
                ]
            ),
            1,
        )

    def test_cron_samples_previous_complete_hour_only(self):
        """Sampling in arrears: the hour still in progress can still gain
        transactions, so it must not be written yet."""
        self._dispatch(1)
        self.Sample._cron_sample_throughput()
        current = self.Sample.search([("bucket_start", "=", self._current_hour())])
        self.assertFalse(current, "the in-progress hour must not be sampled")

    # ------------------------------------------------------------------
    # Oracle / Odoo coexistence -- the parallel-run pivot
    # ------------------------------------------------------------------

    def test_oracle_and_odoo_samples_coexist_for_the_same_hour(self):
        """The whole point of the source field: one table, both systems, one
        pivot for WS-8 parity."""
        hour = self._current_hour()
        self._dispatch(2)
        odoo_sample = self.Sample._sample_hour(hour)
        oracle_sample = self.Sample._upsert_samples(
            [
                {
                    "bucket_start": hour,
                    "provider_id": self.provider.id,
                    "class_id": self.klass.id,
                    "company_id": self.company.id,
                    "txn_count": 2,
                    "success_count": 2,
                    "peak_tps": 2.0,
                }
            ],
            source="oracle",
        )
        self.assertNotEqual(odoo_sample, oracle_sample)
        self.assertEqual(
            odoo_sample.txn_count, oracle_sample.txn_count, "parity: the same hour reconciles across both systems"
        )

    def test_unknown_source_rejected(self):
        with self.assertRaises(UserError):
            self.Sample._upsert_samples([], source="sap")

    # ------------------------------------------------------------------
    # Breach evaluation
    # ------------------------------------------------------------------

    def test_breach_ok_within_baseline(self):
        self._dispatch(1)
        sample = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(sample.target_id, self.baseline)
        self.assertEqual(sample.breach, "ok")

    def test_breach_flags_tps_over_target(self):
        target = self.Target.create({"provider_id": self.provider.id})
        target.peak_tps_target = 0.5
        self._dispatch(2)
        sample = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(sample.target_id, target)
        self.assertEqual(sample.breach, "tps")

    def test_breach_flags_latency_over_target(self):
        target = self.Target.create({"provider_id": self.provider.id})
        target.p95_latency_ms_target = 1
        self._dispatch(1)
        sample = self.Sample._sample_hour(self._current_hour())
        sample.p95_latency_ms = 5000.0
        self.assertEqual(sample.breach, "latency")

    def test_breach_reports_no_target_rather_than_inventing_one(self):
        self.baseline.unlink()
        self._dispatch(1)
        sample = self.Sample._sample_hour(self._current_hour())
        self.assertEqual(sample.breach, "no_target")
        self.assertFalse(sample.target_id)

    def test_empty_hour_is_not_a_success_rate_breach(self):
        """0 transactions means 0% success by arithmetic -- that is an absence
        of evidence, not a reliability incident."""
        sample = self.Sample._upsert_samples(
            [
                {
                    "bucket_start": self._current_hour() - timedelta(hours=5),
                    "provider_id": self.provider.id,
                    "class_id": self.klass.id,
                    "company_id": self.company.id,
                    "txn_count": 0,
                    "success_count": 0,
                }
            ],
            source="odoo",
        )
        self.assertEqual(sample.success_rate_pct, 0.0)
        self.assertEqual(sample.breach, "ok")
