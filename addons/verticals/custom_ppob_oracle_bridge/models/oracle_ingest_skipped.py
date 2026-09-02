# -*- coding: utf-8 -*-
"""Audit log for MSG016T rows skipped by inbound ingest (member/voucher
un-mapped). The cursor advances past skipped rows; this table allows replay
once mappings are added.
"""

from odoo import _, fields, models


class OracleIngestSkipped(models.Model):
    _name = "custom.ppob.oracle.ingest.skipped"
    _description = "Oracle Bridge: Inbound Ingest Skipped Rows"
    _order = "create_date desc, msg016t_id desc"

    msg016t_id = fields.Integer(string="MSG016T ID", required=True, index=True)
    oracle_member_id = fields.Integer(string="Oracle Member ID")
    kode_voucher = fields.Char(string="KODE_VOUCHER")
    trx_number_client = fields.Char(string="TRX_NUMBER_CLIENT")
    skip_reason = fields.Selection(
        selection=[
            ("member_not_mapped", "Member ID not mapped to Odoo partner"),
            ("voucher_not_mapped", "KODE_VOUCHER not mapped to Odoo product"),
            ("member_inactive", "Member mapping is inactive"),
            ("other", "Other"),
        ],
        required=True,
        index=True,
    )
    raw_payload = fields.Text(help="JSON dump of the MSG016T row at time of skip, for replay.")
    replayed = fields.Boolean(default=False, index=True)
    replayed_at = fields.Datetime(readonly=True)
    transaction_id = fields.Many2one(
        comodel_name="custom.ppob.transaction",
        string="Resulting Transaction",
        readonly=True,
    )

    _msg016t_id_uniq = models.Constraint(
        "unique(msg016t_id)",
        "A single MSG016T row can only have one skip record.",
    )

    def action_replay(self):
        """Re-fetch the MSG016T row, retry mapping, create a transaction if it
        now resolves. Idempotent -- already-replayed records are no-ops."""
        Map = self.env["custom.ppob.oracle.member.map"]
        SkuMap = self.env["custom.ppob.provider.sku.map"]
        Txn = self.env["custom.ppob.transaction"]
        conn = self.env["custom.ppob.oracle.connection"]._get_active()

        replayed_count = 0
        for rec in self:
            if rec.replayed:
                continue
            row = conn.query(
                "SELECT id, trx_number_client, member_id, kode_voucher, "
                "number_buyer, sales_price, status_ussd_2_provider "
                "FROM msg016t WHERE id = :id",
                {"id": rec.msg016t_id},
                fetch="one",
            )
            if not row:
                continue
            (msg016t_id, trx_no, member_id, kode_voucher, msisdn, sales_price, status) = row

            member_map = Map.search([("oracle_member_id", "=", member_id), ("active", "=", True)], limit=1)
            sku_map = SkuMap.search([("oracle_kode_voucher", "=", kode_voucher)], limit=1)
            if not member_map or not sku_map:
                continue

            from .constants import ORACLE_STATUS_MAP

            odoo_state = ORACLE_STATUS_MAP.get(status, "in_progress")
            txn = Txn.create(
                {
                    "mitra_id": member_map.partner_id.id,
                    "product_id": sku_map.product_id.id,
                    "provider_id": sku_map.provider_id.id,
                    "idempotency_key": trx_no or f"MSG016T-{msg016t_id}",
                    "provider_ref": str(msg016t_id),
                    "oracle_msg016t_id": msg016t_id,
                    "inbound_source": "oracle_legacy",
                    "msisdn": msisdn or "",
                    "sell_price": sales_price or 0.0,
                    "cost_price": sales_price or 0.0,
                    "state": odoo_state,
                }
            )
            rec.write(
                {
                    "replayed": True,
                    "replayed_at": fields.Datetime.now(),
                    "transaction_id": txn.id,
                }
            )
            replayed_count += 1

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Replay Skipped"),
                "message": _("%s rows replayed.") % replayed_count,
                "type": "success" if replayed_count else "warning",
            },
        }
