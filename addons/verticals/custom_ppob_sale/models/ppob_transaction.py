# -*- coding: utf-8 -*-
import json
import logging
import time
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# PMK-63/2022 effective PPN rate on the PPOB distributor valuation base.
PPN_RATE = 0.11


class PpobTransaction(models.Model):
    _name = "custom.ppob.transaction"
    _description = "PPOB Transaction"
    _order = "create_date desc, id desc"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(
        required=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.ppob.transaction") or "/",
        tracking=True,
    )
    mitra_id = fields.Many2one(
        comodel_name="res.partner",
        string="Mitra",
        required=True,
        domain=[("x_custom_ppob_is_mitra", "=", True)],
        tracking=True,
    )
    wallet_id = fields.Many2one(
        comodel_name="custom.ppob.wallet",
        string="Wallet",
        compute="_compute_wallet_id",
        store=True,
        readonly=True,
    )
    product_id = fields.Many2one(
        comodel_name="custom.ppob.product",
        string="Product",
        required=True,
    )
    class_id = fields.Many2one(
        related="product_id.class_id",
        store=True,
        readonly=True,
    )
    msisdn = fields.Char(
        string="MSISDN / Target",
        required=True,
        help="Phone number, meter ID, account number.",
    )
    provider_id = fields.Many2one(
        comodel_name="custom.ppob.provider",
        string="Provider",
        help="Resolved at dispatch from the SKU map when empty.",
    )
    provider_sku = fields.Char()
    sell_price = fields.Monetary(currency_field="currency_id", required=True)
    cost_price = fields.Monetary(currency_field="currency_id", required=True)
    margin = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_margin",
        store=True,
    )
    # --- Tax (merged from era_ppob_tax): PMK-63/2022 margin VAT ---
    vat_mode = fields.Selection(
        related="class_id.vat_mode",
        store=True,
        readonly=True,
    )
    dpp_amount = fields.Monetary(
        string="DPP",
        currency_field="currency_id",
        compute="_compute_tax",
        store=True,
        help="Taxable base (Dasar Pengenaan Pajak). Computed per class vat_mode; "
        "recognised in GL at the daily rollup faktur, not per transaction.",
    )
    ppn_amount = fields.Monetary(
        string="PPN",
        currency_field="currency_id",
        compute="_compute_tax",
        store=True,
    )
    state = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("inquiry_ok", "Inquiry OK"),
            ("in_progress", "In Progress"),
            ("success", "Success"),
            ("failed", "Failed"),
            ("timeout", "Timeout"),
            ("refunded", "Refunded"),
        ],
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )
    idempotency_key = fields.Char(required=True, index=True, copy=False)
    attempt_no = fields.Integer(default=1)
    provider_ref = fields.Char(copy=False, tracking=True)
    serial_token = fields.Char(help="For PLN token or similar serialised responses.")
    raw_response = fields.Text()
    inquiry_reference = fields.Char()
    inquiry_amount = fields.Monetary(currency_field="currency_id")
    move_id = fields.Many2one("account.move", string="Journal Entry", readonly=True, copy=False)
    reverse_move_id = fields.Many2one("account.move", string="Reversal Entry", readonly=True, copy=False)
    wallet_move_id = fields.Many2one("custom.ppob.wallet.move", readonly=True, copy=False)
    wallet_refund_move_id = fields.Many2one("custom.ppob.wallet.move", readonly=True, copy=False)
    bucket_id = fields.Many2one("custom.ppob.provider.bucket", readonly=True, copy=False)
    bucket_move_id = fields.Many2one("custom.ppob.provider.bucket.move", readonly=True, copy=False)
    bucket_refund_move_id = fields.Many2one("custom.ppob.provider.bucket.move", readonly=True, copy=False)
    dispatched_at = fields.Datetime(readonly=True, copy=False, index=True)
    completed_at = fields.Datetime(readonly=True, copy=False, index=True)
    provider_latency_ms = fields.Integer(
        string="Provider Latency (ms)",
        readonly=True,
        copy=False,
        help="Round-trip time of the provider adapter call made at dispatch, "
        "measured around adapter.pay()/inquiry() only -- it excludes the "
        "wallet + bucket GL posting that precedes it. This is the ADAPTER "
        "RTT, not the fulfilment time: for providers that accept a "
        "transaction and settle asynchronously it measures how long they "
        "took to ACCEPT. Use completed_at - dispatched_at for end-to-end "
        "time, but note that on cron-polled paths (oracle_bridge) that "
        "delta is dominated by cron lag. Populated on every adapter, "
        "unlike custom.adapter.call.log.latency_ms which is only written "
        "when the provider has an adapter_config_id.",
    )
    error_code = fields.Char(copy=False, tracking=True)
    error_message = fields.Char(copy=False, tracking=True)
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    _mitra_idempotency_uniq = models.Constraint(
        "unique(mitra_id, idempotency_key)",
        "Idempotency key must be unique per mitra.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("sell_price", "cost_price")
    def _compute_margin(self):
        for t in self:
            t.margin = (t.sell_price or 0.0) - (t.cost_price or 0.0)

    @api.depends("sell_price", "cost_price", "vat_mode")
    def _compute_tax(self):
        for t in self:
            mode = t.vat_mode or "margin"
            sell = t.sell_price or 0.0
            cost = t.cost_price or 0.0
            if mode == "margin":
                dpp = max(0.0, sell - cost)
            elif mode == "other_valuation":
                dpp = sell * (10.0 / 11.0)
            elif mode == "gross":
                dpp = sell
            else:  # exempt
                dpp = 0.0
            t.dpp_amount = dpp
            t.ppn_amount = round(dpp * PPN_RATE, 2) if mode != "exempt" else 0.0

    @api.depends("mitra_id", "class_id")
    def _compute_wallet_id(self):
        Wallet = self.env["custom.ppob.wallet"]
        for t in self:
            if t.mitra_id and t.class_id:
                t.wallet_id = Wallet.search(
                    [
                        ("partner_id", "=", t.mitra_id.id),
                        ("class_id", "=", t.class_id.id),
                    ],
                    limit=1,
                )
            else:
                t.wallet_id = False

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("idempotency_key"):
                vals["idempotency_key"] = (
                    self.env["ir.sequence"].next_by_code("custom.ppob.transaction")
                    or self.env.cr.mogrify("%s", (fields.Datetime.now(),)).decode()
                )
            if not vals.get("cost_price"):
                product = self.env["custom.ppob.product"].browse(vals.get("product_id"))
                vals["cost_price"] = product.cost_price_default if product else 0.0
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Routing & validation
    # ------------------------------------------------------------------

    def _resolve_provider(self):
        """Pick the best provider for the product based on priority."""
        self.ensure_one()
        if self.provider_id:
            sku = self.env["custom.ppob.provider.sku.map"].search(
                [
                    ("provider_id", "=", self.provider_id.id),
                    ("product_id", "=", self.product_id.id),
                    ("active", "=", True),
                ],
                limit=1,
            )
            if sku:
                return self.provider_id, sku
            raise UserError(
                _("No SKU map for provider %s and product %s.") % (self.provider_id.code, self.product_id.code)
            )
        candidates = self.env["custom.ppob.provider.sku.map"].search(
            [
                ("product_id", "=", self.product_id.id),
                ("active", "=", True),
                ("provider_id.status", "=", "active"),
            ],
            order="priority asc, id asc",
        )
        if not candidates:
            raise UserError(_("No active provider route for product %s.") % self.product_id.code)
        chosen = candidates[0]
        return chosen.provider_id, chosen

    def _check_caps(self):
        """Reject if the mitra would exceed its daily / monthly cap."""
        self.ensure_one()
        mitra = self.mitra_id
        daily_cap = mitra.x_custom_ppob_daily_txn_cap
        monthly_cap = mitra.x_custom_ppob_monthly_txn_cap
        if not daily_cap and not monthly_cap:
            return
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        Txn = self.env["custom.ppob.transaction"]
        if daily_cap:
            daily = sum(
                Txn.search(
                    [
                        ("mitra_id", "=", mitra.id),
                        ("state", "in", ["success", "in_progress"]),
                        ("dispatched_at", ">=", fields.Datetime.to_datetime(today)),
                    ]
                ).mapped("sell_price")
            )
            if daily + self.sell_price > daily_cap:
                raise UserError(_("Mitra daily transaction cap exceeded."))
        if monthly_cap:
            monthly = sum(
                Txn.search(
                    [
                        ("mitra_id", "=", mitra.id),
                        ("state", "in", ["success", "in_progress"]),
                        ("dispatched_at", ">=", fields.Datetime.to_datetime(month_start)),
                    ]
                ).mapped("sell_price")
            )
            if monthly + self.sell_price > monthly_cap:
                raise UserError(_("Mitra monthly transaction cap exceeded."))

    # ------------------------------------------------------------------
    # GL account helpers
    # ------------------------------------------------------------------

    def _get_wallet_debit_counterpart(self):
        """Account credited when debiting the mitra wallet on dispatch.

        Mitra pays pay-as-you-go via the wallet, so the counterpart is always
        the product revenue account. There is no DP-100% flow on the mitra
        side (DP-100% is provider/purchase-side only).
        """
        self.ensure_one()
        return self.product_id._get_revenue_account()

    def _get_ppn_account(self):
        """Resolve the Output VAT (PPN Keluaran) account by role mapping,
        falling back to a search by code. Used by downstream aggregation
        (rollup faktur). PPN is NOT posted per transaction (see D7)."""
        self.ensure_one()
        acc = self.env["custom.ppob.account.mapping"]._get_account("ppn_keluaran", self.company_id)
        if acc:
            return acc
        acc = (
            self.env["account.account"]
            .with_company(self.company_id)
            .search(
                [("code", "=", "2.1.8.01"), ("company_ids", "in", self.company_id.id)],
                limit=1,
            )
        )
        if acc:
            return acc
        raise UserError(
            _(
                "No Output VAT (PPN Keluaran) account found. Expected the "
                "ppn_keluaran role mapping or account code 2.1.8.01."
            )
        )

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def action_dispatch(self):
        for txn in self:
            txn._dispatch_one()
        return True

    def _dispatch_one(self):
        self.ensure_one()
        if self.state not in ("pending", "inquiry_ok"):
            raise UserError(_("Transaction %s is already %s; cannot dispatch.") % (self.name, self.state))
        self._check_caps()

        provider, sku_line = self._resolve_provider()
        # cost_price MUST reflect the buy_price of the provider actually
        # called. Failover may route to a sibling SKU map row with a
        # different buy_price than the product default the txn was created
        # with; always trust the resolved sku_line.buy_price. Fall back to
        # the existing cost only when the resolved sku_line carries no price.
        resolved_cost = sku_line.buy_price or self.cost_price
        if not resolved_cost:
            raise UserError(
                _(
                    "Transaction %s: cannot resolve cost_price. SKU map for "
                    "provider %s / product %s has buy_price=0 and the transaction "
                    "was created without a default cost."
                )
                % (self.name, provider.code, self.product_id.code)
            )
        self.write(
            {
                "provider_id": provider.id,
                "provider_sku": sku_line.provider_sku,
                "cost_price": resolved_cost,
            }
        )

        if not self.wallet_id:
            raise UserError(
                _("Mitra %s has no wallet for class %s.") % (self.mitra_id.display_name, self.class_id.code)
            )

        # 1. Debit mitra wallet (raises on insufficient).
        wallet_move = self.wallet_id._atomic_debit(
            amount=self.sell_price,
            reason=f"Sale {self.name}",
            counterpart_account=self._get_wallet_debit_counterpart(),
            move_type="sale",
            ppob_transaction_id=self.id,
        )
        self.wallet_move_id = wallet_move.id

        # 2. Debit provider bucket (prepaid only).
        if provider.settlement_mode == "prepaid_deposit":
            bucket = provider._resolve_bucket_for(self.product_id)
            self.bucket_id = bucket.id
            bucket_move = bucket._atomic_debit(
                amount=self.cost_price,
                reason=f"Sale {self.name}",
                counterpart_account=self.product_id._get_cogs_account(),
                move_type="usage",
                ppob_transaction_id=self.id,
            )
            self.bucket_move_id = bucket_move.id
            # 2b. Release stock qty via outgoing picking when the bucket has
            # an inventory product (1 unit = Rp 1 for digital deposits).
            if bucket.inventory_product_id and hasattr(bucket, "_stock_picking_outgoing"):
                bucket._stock_picking_outgoing(
                    qty=self.cost_price,
                    origin=self.name,
                    transaction=self,
                    partner=self.mitra_id,
                )

        # 3. GL is already posted by the wallet + bucket atomic helpers (each
        # posts its own paired entry). There is no separate compound sale move
        # (the ERA source's _post_sale_move was dead code -- see D7). PPN is
        # recognised at the daily rollup faktur, not per transaction.
        self.write(
            {
                "state": "in_progress",
                "dispatched_at": fields.Datetime.now(),
            }
        )

        # 4. Fire the adapter (best-effort; on failure we refund).
        # The call is timed for SLA measurement (custom_ppob_sla). Timing wraps
        # ONLY the adapter call so the number is provider RTT, uncontaminated by
        # the GL posting above. monotonic() is used so an NTP step cannot yield a
        # negative latency. The failure path is timed too -- a provider timing
        # out at 15s is exactly the sample the SLA needs to see.
        adapter = provider._get_adapter()
        t0 = time.monotonic()
        try:
            if self.product_id.inquiry_required and self.state == "pending":
                result = adapter.inquiry(self)
            else:
                result = adapter.pay(self)
        except Exception as exc:
            self.provider_latency_ms = int((time.monotonic() - t0) * 1000)
            _logger.exception("Adapter call raised for txn %s", self.name)
            return self._mark_failed(error_code="ADAPTER_EXC", error_message=str(exc))
        self.provider_latency_ms = int((time.monotonic() - t0) * 1000)

        self.raw_response = json.dumps(result.raw or {})
        # ok is TRI-STATE. None means "provider accepted it but has not settled
        # yet" -- leave the transaction in_progress and let the reaper resolve it
        # via status(). Treating pending as failure would refund a sale the
        # provider is still going to fulfil: we would hand the mitra their money
        # back AND deliver the product. `if result.ok:` alone cannot tell None
        # from False, which is why this is checked first and explicitly.
        if result.ok is None:
            self.write(
                {
                    "provider_ref": result.provider_ref or self.provider_ref,
                    "error_code": result.error_code or False,
                    "error_message": result.error_message or False,
                }
            )
            return True
        if result.ok:
            return self._mark_success(
                provider_ref=result.provider_ref,
                serial_token=result.serial_token,
            )
        return self._mark_failed(
            error_code=result.error_code or "ADAPTER_FAIL",
            error_message=result.error_message or "Adapter returned ok=False",
        )

    def _mark_success(self, provider_ref=None, serial_token=None):
        self.ensure_one()
        self.write(
            {
                "state": "success",
                "provider_ref": provider_ref,
                "serial_token": serial_token,
                "completed_at": fields.Datetime.now(),
            }
        )
        return True

    def _mark_failed(self, error_code=None, error_message=None):
        """Failure path: reverse wallet debit + provider deposit debit,
        create reversing journal entries, set state=failed."""
        self.ensure_one()
        self.write(
            {
                "error_code": error_code,
                "error_message": error_message,
                "completed_at": fields.Datetime.now(),
            }
        )
        self._refund_subledgers()
        self.state = "failed"
        return False

    def _refund_subledgers(self):
        """Credit back wallet + bucket, reverse the account.moves linked to the
        debits. Idempotent: does nothing if already refunded. Refund does not
        reverse Input VAT (already reportable); only the COGS <-> bucket leg is
        reversed."""
        self.ensure_one()
        if self.wallet_refund_move_id or self.bucket_refund_move_id:
            return

        if self.wallet_move_id and not self.wallet_refund_move_id:
            refund = self.wallet_id._atomic_credit(
                amount=self.sell_price,
                reason=f"Refund {self.name}",
                counterpart_account=self._get_wallet_debit_counterpart(),
                move_type="refund",
                ppob_transaction_id=self.id,
            )
            self.wallet_refund_move_id = refund.id

        if self.bucket_move_id and not self.bucket_refund_move_id:
            bucket = self.bucket_move_id.bucket_id
            bucket_refund = bucket._atomic_credit(
                dpp_amount=self.cost_price,
                tax_amount=0.0,
                reason=f"Refund {self.name}",
                counterpart_account=self.product_id._get_cogs_account(),
                move_type="refund",
                ppob_transaction_id=self.id,
            )
            self.bucket_refund_move_id = bucket_refund.id

    def action_confirm_inquiry(self):
        """After a successful inquiry, transition pending -> inquiry_ok."""
        for txn in self:
            if txn.state != "pending":
                continue
            txn.state = "inquiry_ok"
        return True

    def action_mark_refunded(self):
        """Manual refund action for ops, after confirming with provider."""
        for txn in self:
            if txn.state not in ("failed", "timeout"):
                raise UserError(_("Only failed/timeout transactions can be manually refunded."))
            txn._refund_subledgers()
            txn.state = "refunded"
        return True

    def action_retry(self):
        """Create a clone with attempt_no + 1, preserving the idempotency family."""
        self.ensure_one()
        clone = self.copy(
            {
                "idempotency_key": f"{self.idempotency_key}/R{self.attempt_no + 1}",
                "attempt_no": self.attempt_no + 1,
                "state": "pending",
                "provider_ref": False,
                "serial_token": False,
                "raw_response": False,
                "error_code": False,
                "error_message": False,
                "move_id": False,
                "wallet_move_id": False,
                "wallet_refund_move_id": False,
                "bucket_id": False,
                "bucket_move_id": False,
                "bucket_refund_move_id": False,
                "dispatched_at": False,
                "completed_at": False,
                "provider_latency_ms": 0,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "custom.ppob.transaction",
            "res_id": clone.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------
    # Stale transaction reaper
    # ------------------------------------------------------------------

    @api.model
    def _cron_reap_stale_inprogress(self, stale_minutes=None):
        """Resolve in_progress transactions stale beyond the provider's
        ``stale_threshold_minutes`` by asking the provider for their status.
        Refund only if the status check confirms failure -- never blindly
        refund. Providers without a status() endpoint are left for manual ops.

        ``stale_minutes`` is an OPTIONAL OVERRIDE for ops backfill scripts;
        when None the per-provider threshold applies.
        """
        now = fields.Datetime.now()
        # Coarse pre-filter (older than 1 minute); per-provider threshold is
        # then checked in the loop below to avoid loading the whole table.
        coarse_cutoff = now - timedelta(minutes=1)
        candidates = self.search(
            [
                ("state", "=", "in_progress"),
                ("dispatched_at", "<", coarse_cutoff),
            ]
        )
        stale = self.env["custom.ppob.transaction"]
        for txn in candidates:
            if stale_minutes is not None:
                threshold = max(int(stale_minutes), 1)
            else:
                threshold = max(int(txn.provider_id.stale_threshold_minutes or 10), 1)
            if txn.dispatched_at and txn.dispatched_at < now - timedelta(minutes=threshold):
                stale |= txn
        for txn in stale:
            if not txn.provider_id:
                continue
            try:
                adapter = txn.provider_id._get_adapter()
            except Exception:
                _logger.exception("Cannot instantiate adapter for %s", txn.name)
                continue
            try:
                result = adapter.status(txn.provider_ref or txn.name)
            except NotImplementedError:
                _logger.warning(
                    "Provider %s has no status() - not auto-refunding %s. Manual ops required.",
                    txn.provider_id.code,
                    txn.name,
                )
                continue
            except Exception as exc:
                _logger.warning("status() failed for %s: %s", txn.name, exc)
                continue
            raw = result.raw or {}
            remote_state = (raw.get("state") or "").lower()
            # ok=None means the provider says it is STILL PROCESSING. Leave it
            # alone: refunding here would return the mitra's money on a sale the
            # provider then completes. `not result.ok` cannot distinguish None
            # from False, so without this guard every in-flight transaction on an
            # async provider gets refunded the moment it goes stale.
            if result.ok is None:
                _logger.info(
                    "Provider %s reports %s still in progress; leaving it alone.",
                    txn.provider_id.code,
                    txn.name,
                )
                continue
            if result.ok and remote_state == "success":
                txn._mark_success(
                    provider_ref=result.provider_ref or txn.provider_ref,
                    serial_token=result.serial_token,
                )
            elif not result.ok or remote_state in ("failed", "rejected"):
                txn.write(
                    {
                        "error_code": result.error_code or "STALE_CONFIRMED_FAIL",
                        "error_message": result.error_message or "Status check confirmed failure.",
                    }
                )
                txn._refund_subledgers()
                txn.state = "timeout"
