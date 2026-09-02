# -*- coding: utf-8 -*-
"""Hourly throughput / latency samples per provider x product class.

One table holds BOTH sides of the parallel run: ``source=oracle`` rows imported
from the legacy MSG016T history, and ``source=odoo`` rows sampled from
``custom.ppob.transaction`` by the hourly cron. Parity checking during WS-8 is
then a pivot over one model rather than a bespoke comparison harness.

Sampling is raw SQL for read_group's sake -- per-second peak and p95 percentile
are not expressible through the ORM.
"""

import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PpobThroughputSample(models.Model):
    _name = "custom.ppob.throughput.sample"
    _description = "PPOB Throughput Sample (hourly)"
    _order = "bucket_start desc, id desc"

    bucket_start = fields.Datetime(
        required=True,
        index=True,
        readonly=True,
        help="Start of the hour this sample covers (UTC, half-open interval [bucket_start, bucket_start + 1h)).",
    )
    source = fields.Selection(
        selection=[
            ("odoo", "Odoo (actual)"),
            ("oracle", "Oracle (historical baseline)"),
        ],
        required=True,
        index=True,
        readonly=True,
        help="Which system produced this traffic. 'oracle' rows are imported "
        "from the legacy MSG016T history to establish the D4 baseline; "
        "'odoo' rows are sampled from custom.ppob.transaction.",
    )
    provider_id = fields.Many2one(
        comodel_name="custom.ppob.provider",
        string="Provider",
        ondelete="cascade",
        index=True,
        readonly=True,
    )
    class_id = fields.Many2one(
        comodel_name="custom.ppob.product.class",
        string="Product Class",
        ondelete="cascade",
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        required=True,
        readonly=True,
    )

    txn_count = fields.Integer(readonly=True)
    success_count = fields.Integer(readonly=True)
    failed_count = fields.Integer(readonly=True)
    timeout_count = fields.Integer(readonly=True)
    peak_tps = fields.Float(
        string="Peak TPS",
        readonly=True,
        help="Highest number of dispatches landing in any single second of this "
        "hour. This is a TRUE peak, not the hourly mean -- an hour "
        "averaging 1 TPS can still contain a 40 TPS second.",
    )
    mean_tps = fields.Float(
        string="Mean TPS",
        compute="_compute_mean_tps",
        store=True,
        readonly=True,
    )
    avg_latency_ms = fields.Float(string="Avg Latency (ms)", readonly=True)
    p95_latency_ms = fields.Float(string="p95 Latency (ms)", readonly=True)
    success_rate_pct = fields.Float(
        string="Success Rate (%)",
        compute="_compute_success_rate",
        store=True,
        readonly=True,
    )
    gross_amount = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        help="Sum of sell_price. Carried so the parallel run can reconcile value, not just transaction counts.",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )

    target_id = fields.Many2one(
        comodel_name="custom.ppob.sla.target",
        string="Resolved Target",
        compute="_compute_breach",
        help="The most specific target matching this sample's scope, if any.",
    )
    breach = fields.Selection(
        selection=[
            ("ok", "Within Target"),
            ("tps", "Peak TPS Exceeded"),
            ("latency", "p95 Latency Exceeded"),
            ("success_rate", "Success Rate Below Target"),
            ("multiple", "Multiple Breaches"),
            ("no_target", "No Target Set"),
        ],
        compute="_compute_breach",
        help="Computed live against the current target, NOT stored -- editing a "
        "target re-evaluates history against it, which is what you want "
        "while the targets themselves are still being calibrated.",
    )

    _sample_uniq = models.Constraint(
        "unique(source, bucket_start, provider_id, class_id, company_id)",
        "A throughput sample already exists for this source / hour / scope.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("txn_count")
    def _compute_mean_tps(self):
        for sample in self:
            sample.mean_tps = round((sample.txn_count or 0) / 3600.0, 4)

    @api.depends("txn_count", "success_count")
    def _compute_success_rate(self):
        for sample in self:
            if not sample.txn_count:
                sample.success_rate_pct = 0.0
                continue
            sample.success_rate_pct = round(100.0 * (sample.success_count or 0) / sample.txn_count, 2)

    @api.depends("provider_id", "class_id", "company_id", "peak_tps", "p95_latency_ms", "success_rate_pct")
    def _compute_breach(self):
        Target = self.env["custom.ppob.sla.target"]
        for sample in self:
            target = Target._resolve(
                provider=sample.provider_id,
                product_class=sample.class_id,
                company=sample.company_id,
            )
            sample.target_id = target
            if not target:
                sample.breach = "no_target"
                continue
            breaches = []
            if target.peak_tps_target and sample.peak_tps > target.peak_tps_target:
                breaches.append("tps")
            if target.p95_latency_ms_target and sample.p95_latency_ms > target.p95_latency_ms_target:
                breaches.append("latency")
            # An empty hour has success_rate 0 by construction; that is not a
            # reliability breach, it is an absence of evidence.
            if (
                sample.txn_count
                and target.success_rate_target_pct
                and sample.success_rate_pct < target.success_rate_target_pct
            ):
                breaches.append("success_rate")
            if not breaches:
                sample.breach = "ok"
            elif len(breaches) == 1:
                sample.breach = breaches[0]
            else:
                sample.breach = "multiple"

    # ------------------------------------------------------------------
    # Ingestion API (shared by the Odoo cron and any external importer)
    # ------------------------------------------------------------------

    @api.model
    def _upsert_samples(self, rows, source):
        """Idempotently write sample rows.

        ``rows`` is a list of dicts keyed by the model's fields; ``bucket_start``
        and ``company_id`` are mandatory. Re-running for the same hour overwrites
        rather than duplicating, so a cron that fires twice, or an Oracle import
        replayed over a range already imported, is safe.

        This is the seam the Oracle importer plugs into: it need only produce
        rows and name the source, with no knowledge of this model's internals.
        """
        if source not in ("odoo", "oracle"):
            raise UserError(_("Unknown throughput sample source: %s") % source)
        written = self.browse()
        for row in rows:
            vals = dict(row, source=source)
            existing = self.search(
                [
                    ("source", "=", source),
                    ("bucket_start", "=", vals["bucket_start"]),
                    ("provider_id", "=", vals.get("provider_id") or False),
                    ("class_id", "=", vals.get("class_id") or False),
                    ("company_id", "=", vals["company_id"]),
                ],
                limit=1,
            )
            if existing:
                existing.write(vals)
                written |= existing
            else:
                written |= self.create(vals)
        return written

    # ------------------------------------------------------------------
    # Odoo-side sampling
    # ------------------------------------------------------------------

    @api.model
    def _sample_hour(self, bucket_start):
        """Sample one complete hour of custom.ppob.transaction into rows.

        Buckets on ``dispatched_at``: a transaction only loads the provider once
        dispatched, and pending rows never touched one. Transactions still
        in_progress when the hour is sampled count toward txn_count but not
        toward success/failed -- so success_rate for the most recent hour reads
        low until the reaper resolves them. Sampling one hour in arrears (see the
        cron) keeps that skew small; it does not eliminate it for providers whose
        stale_threshold_minutes exceeds an hour.
        """
        bucket_end = bucket_start + timedelta(hours=1)
        # Raw SQL below reads rows the ORM may still be holding in cache -- see
        # the platform-wide rule that raw-SQL readers flush first, else moves and
        # transactions written earlier in this same cursor are silently missed.
        self.env.flush_all()
        self.env.cr.execute(
            """
            WITH per_txn AS (
                SELECT provider_id, class_id, company_id, state, sell_price,
                       provider_latency_ms,
                       date_trunc('second', dispatched_at) AS sec
                  FROM custom_ppob_transaction
                 WHERE dispatched_at >= %(start)s
                   AND dispatched_at <  %(end)s
            ),
            per_sec AS (
                SELECT provider_id, class_id, company_id, sec, count(*) AS c
                  FROM per_txn
              GROUP BY provider_id, class_id, company_id, sec
            ),
            peaks AS (
                SELECT provider_id, class_id, company_id, max(c) AS peak_tps
                  FROM per_sec
              GROUP BY provider_id, class_id, company_id
            )
            SELECT t.provider_id,
                   t.class_id,
                   t.company_id,
                   count(*)                                             AS txn_count,
                   count(*) FILTER (WHERE t.state = 'success')          AS success_count,
                   count(*) FILTER (WHERE t.state IN ('failed', 'refunded'))
                                                                        AS failed_count,
                   count(*) FILTER (WHERE t.state = 'timeout')          AS timeout_count,
                   COALESCE(sum(t.sell_price), 0.0)                     AS gross_amount,
                   avg(t.provider_latency_ms)                           AS avg_latency_ms,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY t.provider_latency_ms)
                                                                        AS p95_latency_ms,
                   p.peak_tps
              FROM per_txn t
              JOIN peaks p
                ON p.company_id = t.company_id
               AND p.provider_id IS NOT DISTINCT FROM t.provider_id
               AND p.class_id    IS NOT DISTINCT FROM t.class_id
          GROUP BY t.provider_id, t.class_id, t.company_id, p.peak_tps
            """,
            {"start": bucket_start, "end": bucket_end},
        )
        rows = []
        for rec in self.env.cr.dictfetchall():
            rows.append(
                {
                    "bucket_start": bucket_start,
                    "provider_id": rec["provider_id"],
                    "class_id": rec["class_id"],
                    "company_id": rec["company_id"],
                    "txn_count": rec["txn_count"],
                    "success_count": rec["success_count"],
                    "failed_count": rec["failed_count"],
                    "timeout_count": rec["timeout_count"],
                    "gross_amount": rec["gross_amount"] or 0.0,
                    "avg_latency_ms": rec["avg_latency_ms"] or 0.0,
                    "p95_latency_ms": rec["p95_latency_ms"] or 0.0,
                    "peak_tps": float(rec["peak_tps"] or 0),
                }
            )
        return self._upsert_samples(rows, source="odoo")

    @api.model
    def _cron_sample_throughput(self, hours=1):
        """Sample the last ``hours`` COMPLETE hours.

        Sampling in arrears (never the hour in progress) means a bucket is only
        written once its hour can no longer gain transactions. Default 1; pass a
        larger number to backfill after downtime -- re-sampling is idempotent.
        """
        now = fields.Datetime.now()
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        written = self.browse()
        for offset in range(hours, 0, -1):
            bucket_start = current_hour - timedelta(hours=offset)
            written |= self._sample_hour(bucket_start)
        if written:
            _logger.info("PPOB throughput: sampled %s bucket(s)", len(written))
        return True
