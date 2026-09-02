# -*- coding: utf-8 -*-
"""Backfill wizard -- re-ingest a range of MSG016T rows (recovery / onboarding).

Does NOT touch the main ingest cursor. Idempotent -- already-ingested rows
(matched by mitra + trx_number_client) are skipped.
"""

import json
import logging

from odoo import fields, models

from ..models.constants import ORACLE_STATUS_MAP

_logger = logging.getLogger(__name__)


class OracleBackfillWizard(models.TransientModel):
    _name = "custom.ppob.oracle.backfill.wizard"
    _description = "Oracle Bridge: Backfill Wizard"

    from_msg016t_id = fields.Integer(
        string="From MSG016T ID",
        required=True,
        help="Lower bound (inclusive). Set to MAX(MSG016T.ID) - N to replay the last N rows.",
    )
    to_msg016t_id = fields.Integer(
        string="To MSG016T ID",
        help="Upper bound (inclusive). Leave 0 for no upper limit.",
    )
    dry_run = fields.Boolean(
        default=True,
        help="Preview counts (ingest vs skip) without creating any records.",
    )
    result_summary = fields.Text(readonly=True)

    def action_run(self):
        self.ensure_one()
        connection = self.env["custom.ppob.oracle.connection"].search([("active", "=", True)], limit=1)
        if not connection:
            self.result_summary = "No active Oracle connection."
            return self._reload_form()
        if self.to_msg016t_id and self.to_msg016t_id < self.from_msg016t_id:
            self.result_summary = "to_msg016t_id must be >= from_msg016t_id"
            return self._reload_form()

        sql = (
            "SELECT id, trx_number_client, member_id, kode_voucher, number_buyer, "
            "sales_price, status_ussd_2_provider, message_result_exec_ussd "
            "FROM msg016t WHERE id >= :from_id "
        )
        params = {"from_id": self.from_msg016t_id}
        if self.to_msg016t_id:
            sql += "AND id <= :to_id "
            params["to_id"] = self.to_msg016t_id
        sql += "ORDER BY id"

        try:
            rows = connection.query(sql, params, fetch="all")
        except Exception as exc:
            _logger.exception("Backfill query failed")
            self.result_summary = f"Query failed: {exc}"
            return self._reload_form()

        Txn = self.env["custom.ppob.transaction"]
        Map = self.env["custom.ppob.oracle.member.map"]
        SkuMap = self.env["custom.ppob.provider.sku.map"]
        Skipped = self.env["custom.ppob.oracle.ingest.skipped"]

        total = len(rows)
        will_ingest = will_skip = already_exists = 0

        for row in rows:
            (msg016t_id, trx_no, member_id, kode_voucher, msisdn, sales_price, status, exec_msg) = row
            idem_key = trx_no or f"MSG016T-{msg016t_id}"
            member_map = Map.search([("oracle_member_id", "=", member_id)], limit=1)
            sku_map = SkuMap.search([("oracle_kode_voucher", "=", kode_voucher)], limit=1)

            if not member_map or not sku_map or not member_map.active:
                will_skip += 1
                if not self.dry_run:
                    reason = (
                        "member_not_mapped"
                        if not member_map
                        else ("voucher_not_mapped" if not sku_map else "member_inactive")
                    )
                    Skipped.create(
                        {
                            "msg016t_id": msg016t_id,
                            "oracle_member_id": member_id,
                            "kode_voucher": kode_voucher,
                            "trx_number_client": trx_no,
                            "skip_reason": reason,
                            "raw_payload": json.dumps(
                                {
                                    "msg016t_id": msg016t_id,
                                    "member_id": member_id,
                                    "kode_voucher": kode_voucher,
                                    "status": status,
                                }
                            ),
                        }
                    )
                continue

            existing = Txn.search(
                [
                    ("mitra_id", "=", member_map.partner_id.id),
                    ("idempotency_key", "=", idem_key),
                ],
                limit=1,
            )
            if existing:
                already_exists += 1
                continue

            will_ingest += 1
            if not self.dry_run:
                odoo_state = ORACLE_STATUS_MAP.get(status, "in_progress")
                Txn.create(
                    {
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
                )

        verb = "Would ingest" if self.dry_run else "Ingested"
        self.result_summary = (
            f"Range [{self.from_msg016t_id}..{self.to_msg016t_id or 'MAX'}]\n"
            f"Total rows: {total}\n"
            f"{verb}: {will_ingest}\n"
            f"Skipped (un-mapped): {will_skip}\n"
            f"Already exists (idempotent): {already_exists}\n"
            f"{'(DRY RUN — uncheck dry_run to apply)' if self.dry_run else ''}"
        )
        return self._reload_form()

    def _reload_form(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
