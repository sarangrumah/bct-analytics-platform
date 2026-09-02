# -*- coding: utf-8 -*-
"""POS two-feed join + GL projector.

``custom.ppob.pos.txn`` correlates the POS feed (sales / top-up / refund +
mitra balance) and the H2H feed (fulfillment + cost + deposit) by a shared
``pos_trx_ref``. Each feed is projected to GL independently and idempotently;
the row's ``match_state`` reflects whether both sides have arrived and agree.

GL model (doc S7):
  * POS sale success -> Dr Wallet-liability / Cr Revenue (wallet ``_mirror_debit``)
  * POS top-up       -> Dr Bank/VA clearing / Cr Wallet-liability (``_mirror_credit``)
  * POS refund       -> Dr Revenue / Cr Wallet-liability (``_mirror_credit``)
  * H2H sale success -> Dr COGS / Cr Biller Deposit (direct account.move)

Margin = sell - cost is computed by Odoo on the join, never taken from a feed.
"""

import json
import logging
from datetime import timedelta

from odoo import _, api, fields, models

from .constants import (
    DEFAULT_POS_ONLY_SLA_MIN,
    FEED_H2H,
    FEED_POS,
    PARAM_POS_ONLY_SLA_MIN,
    STATUS_SUCCESS,
    TERMINAL_STATUSES,
)

_logger = logging.getLogger(__name__)


class _IngestSkip(Exception):
    """Raised during ingest to route an event to the skipped queue."""

    def __init__(self, reason, detail=""):
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


class PosTxn(models.Model):
    _name = "custom.ppob.pos.txn"
    _description = "POS Bridge: Two-Feed Join"
    _order = "create_date desc, id desc"

    name = fields.Char(default="/", copy=False, index=True)
    pos_trx_ref = fields.Char(
        string="POS Trx Ref (correlation)",
        required=True,
        index=True,
        copy=False,
    )
    match_state = fields.Selection(
        selection=[
            ("pos_only", "POS only (H2H pending)"),
            ("h2h_only", "H2H only (POS pending)"),
            ("matched", "Matched"),
            ("mismatch", "Mismatch (exception)"),
        ],
        compute="_compute_match_state",
        store=True,
        index=True,
    )
    mitra_id = fields.Many2one("res.partner", string="Mitra", index=True)
    product_id = fields.Many2one("custom.ppob.product", string="Product", index=True)
    provider_id = fields.Many2one("custom.ppob.provider", string="Biller")
    transaction_id = fields.Many2one(
        "custom.ppob.transaction",
        string="Mirror Transaction",
        copy=False,
    )

    # --- POS feed ---
    pos_ingested = fields.Boolean(default=False, index=True)
    pos_posted = fields.Boolean(default=False)
    pos_ref = fields.Char(string="POS External Ref", index=True, copy=False)
    pos_event_type = fields.Selection(
        selection=[("sale", "Sale"), ("topup", "Top-up"), ("refund", "Refund")],
    )
    pos_status = fields.Selection(
        selection=[("success", "Success"), ("failed", "Failed")],
    )
    sell_price = fields.Monetary(currency_field="currency_id")
    wallet_balance_after = fields.Monetary(
        currency_field="currency_id",
        help="Mitra balance reported by POS POS after this event (recon).",
    )
    outlet_ref = fields.Char()
    customer_no = fields.Char(help="MSISDN/IDPEL, masked.")
    pos_txn_time = fields.Datetime(index=True)
    pos_wallet_move_id = fields.Many2one("custom.ppob.wallet.move", copy=False)

    # --- H2H feed ---
    h2h_ingested = fields.Boolean(default=False, index=True)
    h2h_posted = fields.Boolean(default=False)
    h2h_ref = fields.Char(string="H2H External Ref", index=True, copy=False)
    h2h_trx_id = fields.Char(string="Biller Trx ID")
    biller_code = fields.Char()
    h2h_status = fields.Selection(
        selection=[("success", "Success"), ("failed", "Failed")],
    )
    cost_price = fields.Monetary(currency_field="currency_id")
    serial_token = fields.Char()
    deposit_balance_after = fields.Monetary(
        currency_field="currency_id",
        help="Biller deposit reported by H2H after this event (recon).",
    )
    h2h_txn_time = fields.Datetime()
    h2h_move_id = fields.Many2one("account.move", string="COGS/Deposit Entry", copy=False)

    margin = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_margin",
        store=True,
    )
    recon_flagged = fields.Boolean(default=False, index=True)
    recon_note = fields.Char()
    raw_pos = fields.Text()
    raw_h2h = fields.Text()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
    )

    _pos_ref_uniq = models.Constraint("unique(pos_ref)", "POS feed external ref must be unique.")
    _h2h_ref_uniq = models.Constraint("unique(h2h_ref)", "H2H feed external ref must be unique.")

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("pos_ingested", "h2h_ingested", "pos_status", "h2h_status")
    def _compute_match_state(self):
        for rec in self:
            if rec.pos_ingested and rec.h2h_ingested:
                if rec.pos_status == rec.h2h_status:
                    rec.match_state = "matched"
                else:
                    rec.match_state = "mismatch"
            elif rec.pos_ingested:
                rec.match_state = "pos_only"
            elif rec.h2h_ingested:
                rec.match_state = "h2h_only"
            else:
                rec.match_state = "pos_only"

    @api.depends("sell_price", "cost_price", "pos_ingested", "h2h_ingested")
    def _compute_margin(self):
        for rec in self:
            if rec.pos_ingested and rec.h2h_ingested:
                rec.margin = (rec.sell_price or 0.0) - (rec.cost_price or 0.0)
            else:
                rec.margin = 0.0

    # ------------------------------------------------------------------
    # Ingest entry point (called by controllers + replay)
    # ------------------------------------------------------------------

    @api.model
    def _ingest_event(self, feed, payload, replay=False):
        """Project one feed event. Returns the join record, or False when the
        event is routed to the skipped queue. Idempotent per feed external ref.
        """
        try:
            if feed == FEED_POS:
                return self._ingest_pos_event(payload)
            if feed == FEED_H2H:
                return self._ingest_h2h_event(payload)
            raise _IngestSkip("bad_payload", "unknown feed %s" % feed)
        except _IngestSkip as skip:
            self._record_skip(feed, payload, skip, replay=replay)
            return False

    def _record_skip(self, feed, payload, skip, replay=False):
        Skipped = self.env["custom.ppob.pos.ingest.skipped"]
        pos_trx_ref = payload.get("pos_trx_ref") or ""
        external_ref = payload.get("_external_ref") or (pos_trx_ref + (":pos" if feed == FEED_POS else ":h2h"))
        if replay:
            # Replaying an existing skip row -- don't create a duplicate.
            existing = Skipped.search([("external_ref", "=", external_ref)], limit=1)
            if existing and not existing.replayed:
                return
        if Skipped.search([("external_ref", "=", external_ref)], limit=1):
            return
        Skipped.create(
            {
                "feed": feed,
                "external_ref": external_ref,
                "pos_trx_ref": pos_trx_ref,
                "mitra_ref": payload.get("mitra_ref"),
                "product_code": payload.get("product_code"),
                "skip_reason": skip.reason,
                "error_detail": (skip.detail or "")[:512],
                "raw_payload": json.dumps(payload),
            }
        )

    # ------------------------------------------------------------------
    # POS feed
    # ------------------------------------------------------------------

    def _ingest_pos_event(self, payload):
        pos_trx_ref = payload.get("pos_trx_ref")
        event_type = (payload.get("event_type") or "sale").lower()
        status = (payload.get("status") or "").lower()
        if not pos_trx_ref:
            raise _IngestSkip("bad_payload", "missing pos_trx_ref")
        if status not in TERMINAL_STATUSES:
            raise _IngestSkip("non_terminal_status", "status=%s" % status)
        if event_type not in ("sale", "topup", "refund"):
            raise _IngestSkip("bad_payload", "event_type=%s" % event_type)

        external_ref = payload.get("_external_ref") or (pos_trx_ref + ":pos")
        join = self.search([("pos_ref", "=", external_ref)], limit=1)
        if join and join.pos_ingested:
            return join  # idempotent replay

        mitra = self._resolve_mitra(payload.get("mitra_ref"))
        product = None
        if event_type in ("sale", "refund"):
            product = self._resolve_product(payload.get("product_code"))
        elif payload.get("product_code"):
            # Optional on top-up: used only to derive the wallet class.
            product = self.env["custom.ppob.product"].search([("code", "=", payload["product_code"])], limit=1) or None

        join = join or self._get_or_create_join(pos_trx_ref)
        vals = {
            "pos_ref": external_ref,
            "pos_ingested": True,
            "pos_event_type": event_type,
            "pos_status": status,
            "mitra_id": mitra.id,
            "sell_price": float(payload.get("sell_price") or 0.0),
            "wallet_balance_after": float(payload.get("wallet_balance_after") or 0.0),
            "outlet_ref": payload.get("outlet_ref"),
            "customer_no": payload.get("customer_no"),
            "pos_txn_time": payload.get("txn_time") or fields.Datetime.now(),
            "raw_pos": json.dumps(payload),
        }
        if product:
            vals["product_id"] = product.id
        join.write(vals)

        # GL projection: only successful money-moving events post.
        if status == STATUS_SUCCESS:
            klass = product.class_id if product else None
            if event_type == "sale":
                join._project_pos_sale(mitra, product, klass)
            elif event_type == "topup":
                join._project_pos_topup(mitra, klass or self._topup_class(mitra))
            elif event_type == "refund":
                join._project_pos_refund(mitra, product, klass)
        join.pos_posted = status == STATUS_SUCCESS
        return join

    def _project_pos_sale(self, mitra, product, klass):
        self.ensure_one()
        wallet = self._get_or_create_mirror_wallet(mitra, klass)
        # Mirror container transaction (feeds the daily rollup faktur).
        txn = self._ensure_mirror_transaction(mitra, product, state="success")
        revenue_account = product._get_revenue_account()
        wm = wallet._mirror_debit(
            amount=self.sell_price,
            reason=_("POS sale %s") % self.pos_trx_ref,
            counterpart_account=revenue_account,
            move_type="pos_sale",
            ppob_transaction_id=txn.id,
        )
        self.pos_wallet_move_id = wm.id
        txn.write({"wallet_move_id": wm.id})

    def _project_pos_topup(self, mitra, klass):
        self.ensure_one()
        wallet = self._get_or_create_mirror_wallet(mitra, klass)
        counterpart = self._resolve_account(["va_clearing", "cash_bca_escrow"], "top-up bank/clearing")
        wallet._mirror_credit(
            amount=self.sell_price,
            reason=_("POS top-up %s") % self.pos_trx_ref,
            counterpart_account=counterpart,
            move_type="pos_topup",
        )

    def _project_pos_refund(self, mitra, product, klass):
        self.ensure_one()
        wallet = self._get_or_create_mirror_wallet(mitra, klass)
        # Refund of a sale: restore mitra liability, reverse revenue.
        revenue_account = product._get_revenue_account()
        wallet._mirror_credit(
            amount=self.sell_price,
            reason=_("POS refund %s") % self.pos_trx_ref,
            counterpart_account=revenue_account,
            move_type="pos_refund",
        )

    # ------------------------------------------------------------------
    # H2H feed
    # ------------------------------------------------------------------

    def _ingest_h2h_event(self, payload):
        pos_trx_ref = payload.get("pos_trx_ref")
        status = (payload.get("status") or "").lower()
        if not pos_trx_ref:
            raise _IngestSkip("bad_payload", "missing pos_trx_ref")
        if status not in TERMINAL_STATUSES:
            raise _IngestSkip("non_terminal_status", "status=%s" % status)

        external_ref = payload.get("_external_ref") or (pos_trx_ref + ":h2h")
        join = self.search([("h2h_ref", "=", external_ref)], limit=1)
        if join and join.h2h_ingested:
            return join  # idempotent replay

        product = self._resolve_product(payload.get("product_code"))
        provider = self._resolve_provider(payload.get("biller_code"))

        join = join or self._get_or_create_join(pos_trx_ref)
        vals = {
            "h2h_ref": external_ref,
            "h2h_ingested": True,
            "h2h_status": status,
            "h2h_trx_id": payload.get("h2h_trx_id"),
            "biller_code": payload.get("biller_code"),
            "cost_price": float(payload.get("cost_price") or 0.0),
            "serial_token": payload.get("serial") or payload.get("token"),
            "deposit_balance_after": float(payload.get("deposit_balance_after") or 0.0),
            "h2h_txn_time": payload.get("txn_time") or fields.Datetime.now(),
            "raw_h2h": json.dumps(payload),
        }
        if product and not join.product_id:
            vals["product_id"] = product.id
        if provider:
            vals["provider_id"] = provider.id
        join.write(vals)

        if status == STATUS_SUCCESS:
            join._project_h2h_cogs(product)
            join.h2h_posted = True
            # Keep the mirror transaction cost in sync when POS already booked.
            if join.transaction_id:
                join.transaction_id.write({"cost_price": join.cost_price})
        return join

    def _project_h2h_cogs(self, product):
        """Dr COGS / Cr Biller Deposit (direct account.move)."""
        self.ensure_one()
        cogs_account = product._get_cogs_account()
        deposit_account = self._resolve_account(["provider_deposit_default"], "biller deposit")
        journal = self._mirror_journal()
        move = self.env["account.move"].create(
            {
                "journal_id": journal.id,
                "move_type": "entry",
                "ref": _("POS H2H %s") % self.pos_trx_ref,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": cogs_account.id,
                            "name": _("COGS %s") % self.pos_trx_ref,
                            "debit": self.cost_price,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": deposit_account.id,
                            "name": _("Biller deposit %s") % self.pos_trx_ref,
                            "debit": 0.0,
                            "credit": self.cost_price,
                        },
                    ),
                ],
            }
        )
        move.action_post()
        self.h2h_move_id = move.id

    # ------------------------------------------------------------------
    # Resolution helpers
    # ------------------------------------------------------------------

    def _get_or_create_join(self, pos_trx_ref):
        join = self.search([("pos_trx_ref", "=", pos_trx_ref)], limit=1)
        if join:
            return join
        return self.create({"pos_trx_ref": pos_trx_ref, "name": pos_trx_ref})

    @api.model
    def _resolve_mitra(self, mitra_ref):
        if not mitra_ref:
            raise _IngestSkip("mitra_not_mapped", "empty mitra_ref")
        mitra = self.env["res.partner"].search(
            [
                ("x_custom_ppob_mitra_code", "=", mitra_ref),
                ("x_custom_ppob_is_mitra", "=", True),
            ],
            limit=1,
        )
        if not mitra:
            raise _IngestSkip("mitra_not_mapped", "mitra_ref=%s" % mitra_ref)
        return mitra

    @api.model
    def _resolve_product(self, product_code):
        if not product_code:
            raise _IngestSkip("product_not_mapped", "empty product_code")
        product = self.env["custom.ppob.product"].search(
            [
                ("code", "=", product_code),
            ],
            limit=1,
        )
        if not product:
            raise _IngestSkip("product_not_mapped", "product_code=%s" % product_code)
        return product

    @api.model
    def _resolve_provider(self, biller_code):
        if not biller_code:
            return self.env["custom.ppob.provider"]
        return self.env["custom.ppob.provider"].search(
            [
                ("code", "=", biller_code),
            ],
            limit=1,
        )

    def _resolve_account(self, roles, label):
        Mapping = self.env["custom.ppob.account.mapping"]
        for role in roles:
            acc = Mapping._get_account(role, self.company_id)
            if acc:
                return acc
        raise _IngestSkip(
            "post_error",
            "no account mapping for %s (roles tried: %s)" % (label, ", ".join(roles)),
        )

    def _mirror_journal(self):
        journal = self.env["account.journal"].search(
            [
                ("type", "=", "general"),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if not journal:
            raise _IngestSkip("post_error", "no general journal for company")
        return journal

    def _topup_class(self, mitra):
        """Fallback product class for a top-up event without a product_code:
        the mitra's first existing wallet class."""
        wallet = self.env["custom.ppob.wallet"].search(
            [
                ("partner_id", "=", mitra.id),
            ],
            limit=1,
        )
        if not wallet:
            raise _IngestSkip("post_error", "no wallet class for top-up mitra")
        return wallet.class_id

    def _get_or_create_mirror_wallet(self, mitra, klass):
        Wallet = self.env["custom.ppob.wallet"]
        wallet = Wallet.search(
            [
                ("partner_id", "=", mitra.id),
                ("class_id", "=", klass.id),
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )
        if wallet:
            return wallet
        if not klass.default_wallet_account_id:
            raise _IngestSkip("post_error", "class %s has no default wallet account" % klass.code)
        return Wallet.create(
            {
                "partner_id": mitra.id,
                "class_id": klass.id,
                "company_id": self.company_id.id,
                "pos_mirror": True,
            }
        )

    def _ensure_mirror_transaction(self, mitra, product, state):
        """Get-or-create the mirror custom.ppob.transaction for this join."""
        self.ensure_one()
        if self.transaction_id:
            return self.transaction_id
        Txn = self.env["custom.ppob.transaction"]
        existing = Txn.search(
            [
                ("mitra_id", "=", mitra.id),
                ("idempotency_key", "=", self.pos_trx_ref),
            ],
            limit=1,
        )
        if existing:
            self.transaction_id = existing.id
            return existing
        txn = Txn.create(
            {
                "mitra_id": mitra.id,
                "product_id": product.id,
                "msisdn": self.customer_no or "-",
                "idempotency_key": self.pos_trx_ref,
                "sell_price": self.sell_price,
                "cost_price": self.cost_price or product.cost_price_default or 0.0,
                "state": state,
                "pos_txn_id": self.id,
                "provider_id": self.provider_id.id if self.provider_id else False,
                "completed_at": fields.Datetime.now(),
            }
        )
        self.transaction_id = txn.id
        return txn

    # ------------------------------------------------------------------
    # Reconciliation cron
    # ------------------------------------------------------------------

    @api.model
    def _cron_pos_reconcile(self):
        """Control #1: flag pos_only rows older than the SLA (potential feed
        drop / unfulfilled sale). Controls #2/#3: resolve imported settlement
        snapshots against Odoo mirror balances.
        """
        Param = self.env["ir.config_parameter"].sudo()
        try:
            sla_min = int(Param.get_param(PARAM_POS_ONLY_SLA_MIN, DEFAULT_POS_ONLY_SLA_MIN))
        except (TypeError, ValueError):
            sla_min = DEFAULT_POS_ONLY_SLA_MIN
        cutoff = fields.Datetime.now() - timedelta(minutes=sla_min)
        stale = self.search(
            [
                ("match_state", "=", "pos_only"),
                ("pos_status", "=", STATUS_SUCCESS),
                ("recon_flagged", "=", False),
                ("pos_txn_time", "<", cutoff),
            ]
        )
        for rec in stale:
            rec.write(
                {
                    "recon_flagged": True,
                    "recon_note": _("pos_only beyond SLA (%s min) -- H2H feed missing.") % sla_min,
                }
            )
        if stale:
            _logger.warning("POS recon: %s pos_only rows flagged past SLA", len(stale))

        # Mismatch rows are exceptions -- flag once for ops.
        mismatches = self.search(
            [
                ("match_state", "=", "mismatch"),
                ("recon_flagged", "=", False),
            ]
        )
        for rec in mismatches:
            rec.write(
                {
                    "recon_flagged": True,
                    "recon_note": _("Status mismatch POS=%s H2H=%s -- needs review.")
                    % (rec.pos_status, rec.h2h_status),
                }
            )

        self._reconcile_settlements()

    @api.model
    def _reconcile_settlements(self):
        Settlement = self.env["custom.ppob.pos.settlement"]
        Wallet = self.env["custom.ppob.wallet"]
        for st in Settlement.search([("state", "=", "imported")]):
            if st.feed == FEED_POS and st.mitra_id:
                wallets = Wallet.search([("partner_id", "=", st.mitra_id.id)])
                odoo_balance = sum(wallets.mapped("balance"))
            else:
                odoo_balance = 0.0
            st.odoo_balance = odoo_balance
            st.state = "matched" if abs((st.reported_balance or 0.0) - odoo_balance) <= 0.0001 else "variance"
