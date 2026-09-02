# -*- coding: utf-8 -*-
"""Declarative throughput / latency targets, scoped provider x product class.

Closes D4 as configuration. Targets are DECLARATIVE -- nothing in the dispatch
path reads them to throttle or reject; they are the yardstick that
``custom.ppob.throughput.sample`` is measured against. Enforcement (rate
limiting, in-flight caps) is a separate decision and is deliberately not
smuggled in here.
"""

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Indicative daily volume from the v2 realignment brief (10k-50k txn/day). The
# baseline seeds the UPPER bound: a target derived from the optimistic end of a
# range is a target you breach on day one.
BASELINE_DAILY_TXN = 50000

# Traffic is not spread over 24h -- PPOB skews to waking/retail hours. These two
# assumptions turn a daily volume into a peak TPS, and they are exposed as
# fields (not constants) precisely so the reviewer can argue with them.
BASELINE_ACTIVE_HOURS = 14.0
BASELINE_PEAK_FACTOR = 3.0


class PpobSlaTarget(models.Model):
    _name = "custom.ppob.sla.target"
    _description = "PPOB SLA / Throughput Target"
    _order = "provider_id, class_id, id"

    name = fields.Char(
        compute="_compute_name",
        store=True,
        readonly=True,
    )
    active = fields.Boolean(default=True)
    provider_id = fields.Many2one(
        comodel_name="custom.ppob.provider",
        string="Provider",
        ondelete="cascade",
        help="Leave empty to match ANY provider (wildcard). A row scoped to a "
        "specific provider wins over a wildcard row.",
    )
    class_id = fields.Many2one(
        comodel_name="custom.ppob.product.class",
        string="Product Class",
        ondelete="cascade",
        help="Leave empty to match ANY product class (wildcard). Gaming, pulsa "
        "and postpaid bills have very different latency profiles -- expect "
        "to scope real targets per class rather than rely on the wildcard.",
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    # ------------------------------------------------------------------
    # Provenance -- how much is this row worth?
    # ------------------------------------------------------------------

    calibration_source = fields.Selection(
        selection=[
            ("default_baseline", "Default Baseline (unverified guess)"),
            ("oracle_historical", "Oracle Historical (measured on legacy)"),
            ("measured", "Measured (measured on Odoo)"),
        ],
        default="default_baseline",
        required=True,
        help="Provenance of the numbers on this row. Install seeds "
        "'default_baseline' -- a derivation from the indicative 10k-50k "
        "txn/day, NOT an observation. Promote to 'oracle_historical' once "
        "the legacy MSG016T history has been imported and the target "
        "re-derived from it, and to 'measured' once Odoo carries real "
        "traffic. Nothing promotes this automatically: a guess stays "
        "labelled a guess until a human says otherwise.",
    )
    calibrated_at = fields.Datetime(
        readonly=True,
        help="Set by action_mark_calibrated when the source is promoted.",
    )
    calibration_note = fields.Text(
        help="Where the numbers came from: which Oracle date range, which sample set, who agreed to them.",
    )

    # ------------------------------------------------------------------
    # Volume assumptions -> peak TPS
    # ------------------------------------------------------------------

    daily_txn_target = fields.Integer(
        string="Daily Transactions",
        default=BASELINE_DAILY_TXN,
        required=True,
        help="Design volume in transactions per day for this scope.",
    )
    active_hours = fields.Float(
        string="Active Hours/Day",
        default=BASELINE_ACTIVE_HOURS,
        required=True,
        help="Hours per day over which the daily volume actually arrives. "
        "ASSUMPTION -- 24 would model perfectly flat traffic, which PPOB "
        "is not. Replace with the real active window from Oracle history.",
    )
    peak_factor = fields.Float(
        string="Peak Factor",
        default=BASELINE_PEAK_FACTOR,
        required=True,
        help="Ratio of busiest-second rate to the average rate across the "
        "active window. ASSUMPTION -- replace with peak/mean measured from "
        "Oracle history (payday and month-end bill runs will set it).",
    )
    peak_tps_target = fields.Float(
        string="Peak TPS",
        compute="_compute_peak_tps_target",
        store=True,
        readonly=False,
        help="Design peak transactions per second. Derived from the fields "
        "above, but OVERRIDABLE: type a number here to pin it and the "
        "derivation stops applying until an assumption changes. Compare "
        "against custom.ppob.throughput.sample.peak_tps.",
    )

    # ------------------------------------------------------------------
    # Latency / reliability targets
    # ------------------------------------------------------------------

    p95_latency_ms_target = fields.Integer(
        string="p95 Latency (ms)",
        default=3000,
        required=True,
        help="95th-percentile adapter round-trip budget. Measured against custom.ppob.transaction.provider_latency_ms.",
    )
    timeout_s_target = fields.Integer(
        string="Adapter Timeout (s)",
        default=15,
        required=True,
        help="Intended per-call timeout, matching ppob_http_json's default of "
        "15s. DECLARATIVE ONLY -- the effective timeout is "
        "custom.adapter.config.timeout_s on the provider's adapter_config_id "
        "(or the adapter's own DEFAULT_TIMEOUT). This field records what it "
        "SHOULD be so drift is visible; it does not set it.",
    )
    max_in_flight = fields.Integer(
        string="Max In-Flight",
        default=0,
        help="Intended ceiling on concurrent in_progress transactions for this "
        "scope. 0 = unbounded. DECLARATIVE ONLY -- nothing enforces this "
        "today; no concurrency cap exists anywhere in the suite. Recorded "
        "so the number is agreed before enforcement is designed.",
    )
    success_rate_target_pct = fields.Float(
        string="Success Rate (%)",
        default=99.0,
        required=True,
    )

    _scope_uniq = models.Constraint(
        "unique(provider_id, class_id, company_id)",
        "An SLA target already exists for this provider / product class scope.",
    )

    # ------------------------------------------------------------------
    # Computes & constraints
    # ------------------------------------------------------------------

    @api.depends("provider_id", "class_id")
    def _compute_name(self):
        for target in self:
            provider = target.provider_id.code or _("Any Provider")
            klass = target.class_id.code or _("Any Class")
            target.name = f"{provider} / {klass}"

    @api.depends("daily_txn_target", "active_hours", "peak_factor")
    def _compute_peak_tps_target(self):
        for target in self:
            seconds = (target.active_hours or 0.0) * 3600.0
            if seconds <= 0:
                target.peak_tps_target = 0.0
                continue
            mean_tps = (target.daily_txn_target or 0) / seconds
            target.peak_tps_target = round(mean_tps * (target.peak_factor or 1.0), 4)

    @api.constrains("active_hours", "peak_factor")
    def _check_assumptions(self):
        for target in self:
            if not 0 < target.active_hours <= 24:
                raise ValidationError(_("Active hours must be between 0 and 24."))
            if target.peak_factor < 1.0:
                raise ValidationError(
                    _("Peak factor cannot be below 1.0 -- the peak rate is never lower than the mean rate.")
                )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    @api.model
    def _resolve(self, provider=None, product_class=None, company=None):
        """Return the most specific active target for a scope, or an empty
        recordset when none matches.

        Specificity: (provider, class) > (provider, *) > (*, class) > (*, *).
        Callers must handle the empty recordset -- a missing target means "not
        agreed yet", which is information, not something to paper over with a
        default that nobody signed off.
        """
        company = company or self.env.company
        provider_id = provider.id if provider else False
        class_id = product_class.id if product_class else False
        candidates = [
            (provider_id, class_id),
            (provider_id, False),
            (False, class_id),
            (False, False),
        ]
        seen = set()
        for scope in candidates:
            # When provider/class is already empty the tuples collapse; dedupe so
            # a wildcard lookup does not run the same search four times.
            if scope in seen:
                continue
            seen.add(scope)
            target = self.search(
                [
                    ("provider_id", "=", scope[0]),
                    ("class_id", "=", scope[1]),
                    ("company_id", "=", company.id),
                ],
                limit=1,
            )
            if target:
                return target
        return self.browse()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_mark_calibrated(self):
        """Stamp calibrated_at. Refuses while the source is still a guess --
        the whole point of the field is that promotion is deliberate."""
        for target in self:
            if target.calibration_source == "default_baseline":
                raise ValidationError(
                    _(
                        "Target '%s' is still on the default baseline. Set "
                        "Calibration Source to Oracle Historical or Measured (and "
                        "say where the numbers came from in the note) before "
                        "marking it calibrated."
                    )
                    % target.name
                )
            if not target.calibration_note:
                raise ValidationError(
                    _(
                        "Record where the numbers for '%s' came from in the "
                        "calibration note before marking it calibrated."
                    )
                    % target.name
                )
            target.calibrated_at = fields.Datetime.now()
        return True
