# -*- coding: utf-8 -*-
"""Extend custom.ppob.transaction for Oracle Bridge mode.

Adds oracle_msg016t_id + inbound_source, overrides _dispatch_one to short-circuit
oracle_bridge providers (skipping native wallet/bucket debit -- Oracle owns
saldo/inventory), and hosts the three bridge crons.
"""

import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .constants import (
    ORACLE_STATUS_MAP,
    ORACLE_STATUS_TERMINAL,
    PARAM_INBOUND_CURSOR,
)

try:
    import oracledb
except ImportError:
    oracledb = None

_logger = logging.getLogger(__name__)


class PpobTransaction(models.Model):
    _inherit = "custom.ppob.transaction"

    oracle_msg016t_id = fields.Integer(
        string="Oracle MSG016T ID",
        index=True,
        copy=False,
        help="Typed cache of MSG016T.ID (also kept as string in provider_ref).",
    )
    inbound_source = fields.Selection(
        selection=[
            ("odoo", "Native Odoo"),
            ("oracle_legacy", "EVShop Client Legacy"),
        ],
        default="odoo",
        required=True,
        index=True,
        help="Origin: odoo (created via Odoo UI/API) or oracle_legacy (ingested "
        "from MSG016T -- mitra used the EVShop Client desktop app).",
    )

    # ------------------------------------------------------------------
    # Dispatch override
    # ------------------------------------------------------------------

    def _dispatch_one(self):
        self.ensure_one()
        if self.state not in ("pending", "inquiry_ok"):
            raise UserError(_("Transaction %s is already %s; cannot dispatch.") % (self.name, self.state))
        provider, sku_line = self._resolve_provider()
        if provider.bridge_mode == "oracle_bridge":
            return self._dispatch_oracle_bridge(provider, sku_line)
        return super()._dispatch_one()

    def _dispatch_oracle_bridge(self, provider, sku_line):
        """Oracle bridge dispatch: skips native wallet/bucket debit (Oracle owns
        saldo & inventory). GL posting is deferred to the terminal-state cron."""
        self.ensure_one()
        self._check_caps()
        self.write(
            {
                "provider_id": provider.id,
                "provider_sku": sku_line.provider_sku,
                "cost_price": self.cost_price or sku_line.buy_price,
                "dispatched_at": fields.Datetime.now(),
            }
        )
        adapter = provider._get_adapter()
        try:
            result = adapter.pay(self)
        except Exception as exc:
            _logger.exception("Oracle Bridge adapter raised for txn %s", self.name)
            return self._mark_failed(error_code="ORACLE_EXC", error_message=str(exc))

        self.raw_response = json.dumps(result.raw or {})
        if result.ok:
            try:
                msg016t_id = int(result.provider_ref) if result.provider_ref else 0
            except (TypeError, ValueError):
                msg016t_id = 0
            self.write(
                {
                    "state": "in_progress",
                    "provider_ref": result.provider_ref,
                    "oracle_msg016t_id": msg016t_id,
                }
            )
            return True

        # Failure on dispatch -- no Odoo-side debit happened, so no refund.
        self.write(
            {
                "state": "failed",
                "error_code": result.error_code or "ORACLE_FAIL",
                "error_message": result.error_message or "Oracle SP returned error",
                "completed_at": fields.Datetime.now(),
            }
        )
        return False

    # ------------------------------------------------------------------
    # Cron: status sync (Oracle -> Odoo)
    # ------------------------------------------------------------------

    @api.model
    def _cron_oracle_sync_status(self, batch_size=200):
        connection = self.env["custom.ppob.oracle.connection"].search([("active", "=", True)], limit=1)
        if not connection:
            _logger.info("Oracle Bridge: no active connection, skipping status sync")
            return
        txns = self.search(
            [
                ("state", "=", "in_progress"),
                ("provider_id.bridge_mode", "=", "oracle_bridge"),
                ("oracle_msg016t_id", "!=", 0),
                ("oracle_msg016t_id", "!=", False),
            ],
            limit=batch_size,
            order="oracle_msg016t_id",
        )
        if not txns:
            return

        ids = [t.oracle_msg016t_id for t in txns]
        bind_names = {f"id{i}": v for i, v in enumerate(ids)}
        in_clause = ",".join(":id%d" % i for i in range(len(ids)))
        sql = "SELECT id, status_ussd_2_provider, message_result_exec_ussd FROM msg016t WHERE id IN (%s)" % in_clause
        try:
            rows = connection.query(sql, bind_names, fetch="all")
        except Exception:
            _logger.exception("Oracle status sync query failed")
            return

        status_by_id = {r[0]: (r[1], r[2]) for r in rows}
        for txn in txns:
            data = status_by_id.get(txn.oracle_msg016t_id)
            if data is None:
                _logger.warning(
                    "Oracle status sync: MSG016T id=%s for txn %s not found", txn.oracle_msg016t_id, txn.name
                )
                continue
            status, msg = data
            if status not in ORACLE_STATUS_TERMINAL:
                continue
            try:
                with self.env.cr.savepoint():
                    if ORACLE_STATUS_MAP.get(status) == "success":
                        txn._mark_success(provider_ref=str(txn.oracle_msg016t_id), serial_token=None)
                    else:
                        txn._mark_failed(
                            error_code="oracle_provider_failed", error_message=msg or "Failed by Oracle pipeline"
                        )
            except Exception:
                _logger.exception("Failed to transition txn %s on status %s", txn.name, status)

    # ------------------------------------------------------------------
    # Cron: inbound ingest (EVShop Client legacy -> Odoo)
    # ------------------------------------------------------------------

    @api.model
    def _cron_oracle_inbound_ingest(self, batch_size=500):
        connection = self.env["custom.ppob.oracle.connection"].search([("active", "=", True)], limit=1)
        if not connection:
            return

        Param = self.env["ir.config_parameter"].sudo()
        try:
            last_seen = int(Param.get_param(PARAM_INBOUND_CURSOR, "0"))
        except (TypeError, ValueError):
            last_seen = 0

        sql = (
            "SELECT id, trx_number_client, member_id, kode_voucher, number_buyer, "
            "nominal_request, sales_price, fee_amount, is_ppob, status_ussd_2_provider, "
            "request_date, message_result_exec_ussd, original_conversation_id "
            "FROM msg016t WHERE id > :last_seen ORDER BY id "
            "FETCH FIRST :batch_size ROWS ONLY"
        )
        try:
            rows = connection.query(sql, {"last_seen": last_seen, "batch_size": batch_size}, fetch="all")
        except Exception:
            _logger.exception("Oracle inbound ingest query failed")
            return
        if not rows:
            return

        Map = self.env["custom.ppob.oracle.member.map"]
        SkuMap = self.env["custom.ppob.provider.sku.map"]
        Skipped = self.env["custom.ppob.oracle.ingest.skipped"]
        new_cursor = last_seen
        ingested = skipped = 0

        for row in rows:
            (
                msg016t_id,
                trx_no_client,
                member_id,
                kode_voucher,
                msisdn,
                nominal_req,
                sales_price,
                fee_amount,
                is_ppob,
                status,
                req_date,
                exec_msg,
                conv_id,
            ) = row
            if msg016t_id and msg016t_id > new_cursor:
                new_cursor = msg016t_id
            try:
                with self.env.cr.savepoint():
                    self._ingest_one_msg016t_row(
                        Map,
                        SkuMap,
                        Skipped,
                        msg016t_id,
                        trx_no_client,
                        member_id,
                        kode_voucher,
                        msisdn,
                        sales_price,
                        status,
                        exec_msg,
                    )
                ingested += 1
            except _IngestSkipped as skip:
                Skipped.create(
                    {
                        "msg016t_id": msg016t_id,
                        "oracle_member_id": member_id,
                        "kode_voucher": kode_voucher,
                        "trx_number_client": trx_no_client,
                        "skip_reason": skip.reason,
                        "raw_payload": json.dumps(
                            {
                                "msg016t_id": msg016t_id,
                                "trx_number_client": trx_no_client,
                                "member_id": member_id,
                                "kode_voucher": kode_voucher,
                                "msisdn": msisdn,
                                "sales_price": float(sales_price or 0),
                                "status": status,
                            }
                        ),
                    }
                )
                skipped += 1
            except Exception:
                _logger.exception("Oracle ingest failed for MSG016T id=%s", msg016t_id)

        Param.set_param(PARAM_INBOUND_CURSOR, str(new_cursor))
        _logger.info(
            "Oracle inbound ingest: cursor %s -> %s, ingested=%s, skipped=%s", last_seen, new_cursor, ingested, skipped
        )

    def _ingest_one_msg016t_row(
        self,
        Map,
        SkuMap,
        Skipped,
        msg016t_id,
        trx_no_client,
        member_id,
        kode_voucher,
        msisdn,
        sales_price,
        status,
        exec_msg,
    ):
        """Process one MSG016T row; raise _IngestSkipped if it cannot be mapped."""
        member_map = Map.search([("oracle_member_id", "=", member_id)], limit=1)
        if not member_map:
            raise _IngestSkipped("member_not_mapped")
        if not member_map.active:
            raise _IngestSkipped("member_inactive")
        sku_map = SkuMap.search([("oracle_kode_voucher", "=", kode_voucher)], limit=1)
        if not sku_map:
            raise _IngestSkipped("voucher_not_mapped")

        idem_key = trx_no_client or f"MSG016T-{msg016t_id}"
        existing = self.search(
            [
                ("mitra_id", "=", member_map.partner_id.id),
                ("idempotency_key", "=", idem_key),
            ],
            limit=1,
        )
        if existing:
            if not existing.oracle_msg016t_id:
                existing.write({"oracle_msg016t_id": msg016t_id, "provider_ref": str(msg016t_id)})
            return existing

        odoo_state = ORACLE_STATUS_MAP.get(status, "in_progress")
        vals = {
            "mitra_id": member_map.partner_id.id,
            "product_id": sku_map.product_id.id,
            "provider_id": sku_map.provider_id.id,
            "provider_sku": sku_map.provider_sku,
            "idempotency_key": idem_key,
            "provider_ref": str(msg016t_id),
            "oracle_msg016t_id": msg016t_id,
            "inbound_source": "oracle_legacy",
            "msisdn": msisdn or "",
            "sell_price": float(sales_price or 0),
            "cost_price": float(sku_map.buy_price or sales_price or 0),
            "state": odoo_state,
        }
        if odoo_state in ("success", "failed"):
            vals["completed_at"] = fields.Datetime.now()
            if odoo_state == "failed":
                vals["error_code"] = "oracle_provider_failed"
                vals["error_message"] = exec_msg or "Failed in Oracle pipeline"
        return self.create(vals)

    # ------------------------------------------------------------------
    # Cron: balance mirror (MSG019T -> custom.ppob.wallet)
    # ------------------------------------------------------------------

    @api.model
    def _cron_oracle_balance_mirror(self):
        connection = self.env["custom.ppob.oracle.connection"].search([("active", "=", True)], limit=1)
        if not connection:
            return
        Map = self.env["custom.ppob.oracle.member.map"].search([("active", "=", True)])
        if not Map:
            return

        member_ids = Map.mapped("oracle_member_id")
        bind_names = {f"id{i}": v for i, v in enumerate(member_ids)}
        in_clause = ",".join(":id%d" % i for i in range(len(member_ids)))
        sql = "SELECT id, deposit_balance FROM msg019t WHERE id IN (%s)" % in_clause
        try:
            rows = connection.query(sql, bind_names, fetch="all")
        except Exception:
            _logger.exception("Oracle balance mirror query failed")
            return

        balance_by_id = {r[0]: r[1] for r in rows}
        Wallet = self.env["custom.ppob.wallet"]
        WalletMove = self.env["custom.ppob.wallet.move"]
        now = fields.Datetime.now()

        for m in Map:
            oracle_balance = balance_by_id.get(m.oracle_member_id)
            if oracle_balance is None:
                continue
            oracle_balance = float(oracle_balance or 0)
            wallets = Wallet.search(
                [
                    ("partner_id", "=", m.partner_id.id),
                    ("mirror_source", "=", "oracle"),
                ]
            )
            for w in wallets:
                delta = oracle_balance - w.balance
                if abs(delta) > 0.0001:
                    WalletMove.create(
                        {
                            "wallet_id": w.id,
                            "type": "oracle_sync",
                            "amount_signed": delta,
                            "balance_after": oracle_balance,
                            "ref": f"Oracle MSG019T sync {now}",
                            "state": "posted",
                        }
                    )
                    w.write({"balance": oracle_balance})
            m.write({"last_known_balance": oracle_balance, "last_balance_sync": now})


class _IngestSkipped(Exception):
    """Internal signal used during inbound ingest to record skips."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason
