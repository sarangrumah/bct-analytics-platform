# -*- coding: utf-8 -*-
"""Test fixtures + MockOracle.

MockOracle implements the same execute_sp/query surface as the real connection,
backed by in-memory MSG016T/MSG019T dicts. Tests patch the active connection's
methods to delegate to the mock so cron + adapter logic runs end-to-end without
a live Oracle DB.
"""

from unittest.mock import patch

from odoo.tests import TransactionCase


class MockOracle:
    def __init__(self):
        self.msg016t = {}
        self.msg019t = {}
        self.next_msg016t_id = 1
        self.sp_calls = []
        self.query_calls = []
        self.next_sp_outcome = None

    def add_member(self, member_id, member_no="M001", member_group="AREA1", balance=1000000.0):
        self.msg019t[member_id] = {
            "id": member_id,
            "member_no": member_no,
            "member_group": member_group,
            "deposit_balance": balance,
        }

    def add_msg016t(self, member_id, kode_voucher, trx_no, status="P", sales_price=10000.0, msisdn="08123456789"):
        rid = self.next_msg016t_id
        self.next_msg016t_id += 1
        self.msg016t[rid] = {
            "id": rid,
            "trx_number_client": trx_no,
            "member_id": member_id,
            "kode_voucher": kode_voucher,
            "number_buyer": msisdn,
            "nominal_request": sales_price,
            "sales_price": sales_price,
            "fee_amount": 0,
            "is_ppob": "N",
            "status_ussd_2_provider": status,
            "request_date": None,
            "message_result_exec_ussd": "",
            "original_conversation_id": f"CONV_{rid}",
        }
        return rid

    def execute_sp(self, sp_name, params, out_specs):
        self.sp_calls.append({"sp": sp_name, "params": dict(params)})
        if self.next_sp_outcome is not None:
            outcome = self.next_sp_outcome
            self.next_sp_outcome = None
            return outcome
        if "SellWithDenom" in sp_name:
            member_id = list(self.msg019t.keys())[0] if self.msg019t else 0
            rid = self.add_msg016t(
                member_id=member_id,
                kode_voucher=params.get("kodevoucher", ""),
                trx_no=params.get("trxNumber", ""),
                status="P",
                sales_price=params.get("Nominal", 0),
                msisdn=params.get("mdn", ""),
            )
            return {"trxId": rid, "err": 0, "msg": "OK"}
        return {"err": 1, "msg": f"Unknown SP {sp_name}"}

    def query(self, sql, params=None, fetch="all"):
        self.query_calls.append({"sql": sql, "params": dict(params or {})})
        params = params or {}
        sql_low = sql.lower()

        if "from msg016t" in sql_low and "where id =" in sql_low:
            row = self.msg016t.get(params.get("id"))
            if not row:
                return None if fetch == "one" else []
            tup = (row["id"], row["status_ussd_2_provider"], row["message_result_exec_ussd"], row["sales_price"])
            return tup if fetch == "one" else [tup]

        if "from msg016t" in sql_low and "where id in" in sql_low:
            ids = [v for k, v in params.items() if k.startswith("id")]
            return [
                (r["id"], r["status_ussd_2_provider"], r["message_result_exec_ussd"])
                for rid in ids
                for r in [self.msg016t.get(rid)]
                if r
            ]

        if "from msg016t" in sql_low and "where id >" in sql_low:
            last_seen = params.get("last_seen", 0)
            limit = params.get("batch_size", 500)
            rows = sorted((r for r in self.msg016t.values() if r["id"] > last_seen), key=lambda r: r["id"])[:limit]
            return [
                (
                    r["id"],
                    r["trx_number_client"],
                    r["member_id"],
                    r["kode_voucher"],
                    r["number_buyer"],
                    r["nominal_request"],
                    r["sales_price"],
                    r["fee_amount"],
                    r["is_ppob"],
                    r["status_ussd_2_provider"],
                    r["request_date"],
                    r["message_result_exec_ussd"],
                    r["original_conversation_id"],
                )
                for r in rows
            ]

        if "from msg019t" in sql_low and "where id in" in sql_low:
            ids = [v for k, v in params.items() if k.startswith("id")]
            return [(self.msg019t[i]["id"], self.msg019t[i]["deposit_balance"]) for i in ids if i in self.msg019t]

        if "from msg019t" in sql_low and "where id =" in sql_low:
            m = self.msg019t.get(params.get("id"))
            if not m:
                return None
            return (m["id"], m["member_no"], m["member_group"], m["deposit_balance"])

        if "from v$version" in sql_low:
            v = ("Oracle Database 19c (MOCK)",)
            return v if fetch == "one" else [v]

        return [] if fetch == "all" else None


class OracleBridgeCommon(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.mock = MockOracle()

        cls.product_class = cls.env["custom.ppob.product.class"].search([], limit=1)
        if not cls.product_class:
            cls.product_class = cls.env["custom.ppob.product.class"].create({"name": "Test Class", "code": "TEST"})

        cls.product = cls.env["custom.ppob.product"].create(
            {
                "name": "Test TSEL10",
                "code": "TEST_TSEL10",
                "class_id": cls.product_class.id,
                "denom": 10000.0,
                "cost_price_default": 9500.0,
            }
        )
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Test Mitra Oracle",
                "x_custom_ppob_is_mitra": True,
                "x_custom_ppob_mitra_code": "TM-ORA-001",
            }
        )
        cls.provider_partner = cls.env["res.partner"].create(
            {
                "name": "Test Provider Oracle",
                "x_custom_ppob_is_provider": True,
            }
        )
        cls.provider = cls.env["custom.ppob.provider"].create(
            {
                "code": "TEST_ORA",
                "name": "Test Oracle Provider",
                "partner_id": cls.provider_partner.id,
                "settlement_mode": "postpaid",  # avoid bucket setup complexity
                "bridge_mode": "oracle_bridge",
                "adapter_class": "ppob_oracle_bridge",
                "oracle_sp_name": "SellWithDenom_HA",
                "journal_id": cls._journal(cls).id,
            }
        )
        cls.sku_map = cls.env["custom.ppob.provider.sku.map"].create(
            {
                "provider_id": cls.provider.id,
                "product_id": cls.product.id,
                "provider_sku": "TSEL10",
                "oracle_kode_voucher": "TSEL10",
                "buy_price": 9500.0,
            }
        )
        cls.connection = cls.env["custom.ppob.oracle.connection"].create(
            {
                "name": "Test Mock Connection",
                "dsn": "mock:1521/MOCK",
                "username": "mock",
                "password": "mock",
                "schema_name": "mobile",
                "sp_default_usr": "TESTUSR",
                "active": True,
            }
        )
        cls.member_map = cls.env["custom.ppob.oracle.member.map"].create(
            {
                "partner_id": cls.partner.id,
                "oracle_member_id": 12345,
            }
        )
        cls.mock.add_member(12345, "M-12345", "AREA1", 1000000.0)

    def setUp(self):
        # The mock is a plain Python object shared via the class fixture; unlike
        # the DB it is NOT rolled back between tests, so its call log / rows
        # accumulate. Reset it to a clean seeded state before each test.
        super().setUp()
        self.mock = MockOracle()
        self.mock.add_member(12345, "M-12345", "AREA1", 1000000.0)

    @staticmethod
    def _journal(cls):
        return cls.env["account.journal"].search([("type", "=", "general")], limit=1) or cls.env[
            "account.journal"
        ].create({"name": "Test General", "type": "general", "code": "TST"})

    def _patch_connection(self):
        return patch.multiple(
            type(self.connection),
            execute_sp=lambda self_, sp_name, params, out_specs: self.mock.execute_sp(sp_name, params, out_specs),
            query=lambda self_, sql, params=None, fetch="all": self.mock.query(sql, params, fetch),
        )

    def _make_transaction(self, idempotency_key="TXN-001", state="pending"):
        return self.env["custom.ppob.transaction"].create(
            {
                "mitra_id": self.partner.id,
                "product_id": self.product.id,
                "provider_id": self.provider.id,
                "idempotency_key": idempotency_key,
                "msisdn": "08123456789",
                "sell_price": 10000.0,
                "cost_price": 9500.0,
                "state": state,
            }
        )
