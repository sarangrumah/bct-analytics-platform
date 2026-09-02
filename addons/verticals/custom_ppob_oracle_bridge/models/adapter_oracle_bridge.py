# -*- coding: utf-8 -*-
"""Oracle Bridge adapter -- implements PPOBProviderAdapter against the Oracle
EVShop pipeline (MSG016T queue + SP SellWithDenom_HA).

Registered as ``ppob_oracle_bridge`` in the PPOB adapter registry. When in use,
the native wallet/bucket atomic debit is SKIPPED (see the transaction extension)
because saldo & inventory are managed by Oracle; Odoo posts GL only when the
transaction reaches a terminal state.
"""

import logging

from odoo.addons.custom_ppob_provider.models.ppob_provider_adapter_base import (
    AdapterResult,
    PPOBProviderAdapter,
    register_adapter,
)

try:
    import oracledb
except ImportError:
    oracledb = None

_logger = logging.getLogger(__name__)


@register_adapter("ppob_oracle_bridge")
class OracleBridgeAdapter(PPOBProviderAdapter):
    def _connection(self):
        return self.provider.env["custom.ppob.oracle.connection"]._get_active()

    def _resolve_member(self, transaction):
        return self.provider.env["custom.ppob.oracle.member.map"].search(
            [
                ("partner_id", "=", transaction.mitra_id.id),
                ("active", "=", True),
            ],
            limit=1,
        )

    def _resolve_sku(self, transaction):
        return self.provider.env["custom.ppob.provider.sku.map"].search(
            [
                ("provider_id", "=", self.provider.id),
                ("product_id", "=", transaction.product_id.id),
            ],
            limit=1,
        )

    # ------------------------------------------------------------------
    # PPOBProviderAdapter interface
    # ------------------------------------------------------------------

    def inquiry(self, transaction):
        # Inquiry is implicit in the EVShop pipeline (pricing cached via SKU map).
        return AdapterResult(
            ok=True,
            amount=transaction.sell_price,
            raw={"note": "oracle_bridge inquiry is implicit"},
        )

    def pay(self, transaction):
        if oracledb is None:
            return AdapterResult(
                ok=False, error_code="oracledb_missing", error_message="oracledb library not installed on Odoo server"
            )

        member_map = self._resolve_member(transaction)
        if not member_map:
            return AdapterResult(
                ok=False,
                error_code="member_not_mapped",
                error_message=(
                    "Mitra %s belum dimap ke Oracle MSG019T. Tambahkan mapping di Oracle Bridge > Member Mapping."
                )
                % transaction.mitra_id.display_name,
            )

        sku_map = self._resolve_sku(transaction)
        if not sku_map or not sku_map.oracle_kode_voucher:
            return AdapterResult(
                ok=False,
                error_code="voucher_not_mapped",
                error_message=(
                    "Produk %s belum dimap ke KODE_VOUCHER Oracle. Set field oracle_kode_voucher di SKU Map."
                )
                % (transaction.product_id.code or transaction.product_id.display_name),
            )

        connection = self._connection()
        sp_name = self.provider.oracle_sp_name or "SellWithDenom_HA"
        params = {
            "usr": connection.sp_default_usr or "",
            "pwd": connection.sp_default_pwd or "",
            "macadd": connection.sp_default_macadd or "",
            "kodevoucher": sku_map.oracle_kode_voucher,
            "mdn": transaction.msisdn or "",
            "idpln": "",
            "trxNumber": transaction.idempotency_key,
            "addr": connection.sp_default_ip or "",
            "GoogleserialNumber": "",
            "Nominal": int(transaction.product_id.denom or 0),
        }
        out_specs = {
            "trxId": oracledb.NUMBER,
            "err": oracledb.NUMBER,
            "msg": oracledb.STRING,
        }
        try:
            result = connection.execute_sp(sp_name, params, out_specs)
        except Exception as exc:
            _logger.exception("Oracle SP %s failed for txn %s", sp_name, transaction.name)
            return AdapterResult(ok=False, error_code="oracle_unreachable", error_message=str(exc)[:500])

        err = int(result.get("err") or 0)
        msg = result.get("msg") or ""
        trx_id = result.get("trxId")
        if err == 0 and trx_id:
            return AdapterResult(
                ok=True,
                provider_ref=str(int(trx_id)),
                amount=transaction.sell_price,
                raw={"sp": sp_name, "msg": msg, "trx_id": int(trx_id)},
            )
        return AdapterResult(
            ok=False,
            error_code="oracle_business_error",
            error_message=msg or "SP returned err != 0",
            raw={"sp": sp_name, "err": err, "msg": msg},
        )

    def status(self, provider_ref):
        if oracledb is None:
            return AdapterResult(ok=False, error_code="oracledb_missing")
        try:
            msg016t_id = int(provider_ref)
        except (TypeError, ValueError):
            return AdapterResult(
                ok=False,
                error_code="invalid_provider_ref",
                error_message="provider_ref is not a valid MSG016T.ID integer",
            )

        connection = self._connection()
        try:
            row = connection.query(
                "SELECT id, status_ussd_2_provider, message_result_exec_ussd, sales_price FROM msg016t WHERE id = :id",
                {"id": msg016t_id},
                fetch="one",
            )
        except Exception as exc:
            _logger.exception("Oracle status query failed for ref %s", provider_ref)
            return AdapterResult(ok=False, error_code="oracle_unreachable", error_message=str(exc)[:500])

        if not row:
            return AdapterResult(
                ok=False, error_code="msg016t_not_found", error_message="MSG016T row %s not found" % provider_ref
            )

        from .constants import ORACLE_STATUS_MAP, ORACLE_STATUS_TERMINAL

        _, status, msg, price = row
        odoo_state = ORACLE_STATUS_MAP.get(status, "in_progress")

        if status in ORACLE_STATUS_TERMINAL:
            ok = odoo_state == "success"
            return AdapterResult(
                ok=ok,
                provider_ref=str(msg016t_id),
                amount=price,
                raw={"status": status, "message": msg},
                error_code=None if ok else "oracle_provider_failed",
                error_message=None if ok else (msg or "Failed by Oracle pipeline"),
            )
        # Still in progress -- ok=None so the caller knows not to terminate.
        return AdapterResult(ok=None, provider_ref=str(msg016t_id), raw={"status": status, "message": msg})

    def topup(self, amount):
        raise NotImplementedError(
            "Topup via Oracle Bridge not implemented in phase 1. Use existing EVShop / VA H2H topup channels."
        )
