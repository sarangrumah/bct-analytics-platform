# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
from decimal import Decimal

from odoo.tests import TransactionCase, tagged

from odoo.addons.custom_adapter_framework.models.adapter_base import (
    AdapterResponse,
    BaseAdapter,
)
from odoo.addons.custom_adapter_framework.models.adapter_registry import (
    register_adapter,
)


@register_adapter("bank_test_mock_h2h")
class _MockH2HAdapter(BaseAdapter):
    fixture_lines: list[dict] = []

    def inquiry_balance(self, account_number):
        return AdapterResponse(ok=True, status_code=200, data={"balance": 1000.0})

    def inquiry_statement(self, account_number, date_from, date_to):
        return AdapterResponse(
            ok=True,
            status_code=200,
            data={"lines": list(self.fixture_lines)},
        )


@tagged("post_install", "-at_install")
class TestBankImport(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Template = cls.env["custom.bank.import.template"]
        cls.Wizard = cls.env["custom.bank.import.csv.wizard"]
        cls.Log = cls.env["custom.bank.import.log"]
        cls.Conn = cls.env["custom.bank.h2h.connection"]
        cls.AdapterConfig = cls.env["custom.adapter.config"]

        cls.journal = cls.env["account.journal"].create(
            {
                "name": "Test Bank",
                "type": "bank",
                "code": "TBNK",
            }
        )
        cls.template = cls.Template.create(
            {
                "name": "Test CSV",
                "code": "test_csv",
                "encoding": "utf-8",
                "delimiter": ",",
                "has_header": True,
                "date_format": "%Y-%m-%d",
                "date_column_index": 1,
                "ref_column_index": 2,
                "signed_amount_column_index": 3,
                "decimal_separator": ".",
                "thousand_separator": "",
            }
        )

    # ----- CSV happy path -----

    def test_csv_happy_path(self):
        csv = b"date,ref,amount\n2026-05-01,REF001,1500.00\n2026-05-02,REF002,-250.00\n2026-05-03,REF003,75.50\n"
        wiz = self.Wizard.create(
            {
                "journal_id": self.journal.id,
                "template_id": self.template.id,
                "file": base64.b64encode(csv).decode(),
                "filename": "happy.csv",
            }
        )
        action = wiz.action_import()
        self.assertEqual(action["res_model"], "custom.bank.import.log")
        log = self.Log.browse(action["res_id"])
        self.assertEqual(log.state, "imported")
        self.assertEqual(log.line_count, 3)
        self.assertEqual(log.error_count, 0)
        statement = log.statement_id
        self.assertTrue(statement)
        self.assertEqual(len(statement.line_ids), 3)
        amounts = sorted(statement.line_ids.mapped("amount"))
        self.assertEqual(amounts, [-250.0, 75.5, 1500.0])

    # ----- CSV bad date format -----

    def test_csv_bad_date_format_graceful(self):
        csv = (
            b"date,ref,amount\n"
            b"NOT-A-DATE,REF001,1500.00\n"
            b"2026/13/40,REF002,200.00\n"  # bad day
            b"2026-05-03,REF003,75.50\n"  # one good row
        )
        wiz = self.Wizard.create(
            {
                "journal_id": self.journal.id,
                "template_id": self.template.id,
                "file": base64.b64encode(csv).decode(),
                "filename": "bad-dates.csv",
            }
        )
        action = wiz.action_import()
        log = self.Log.browse(action["res_id"])
        self.assertEqual(log.state, "partial")
        self.assertEqual(log.line_count, 1)
        self.assertGreaterEqual(log.error_count, 2)
        self.assertIn("Bad/missing date", log.raw_payload or "")

    # ----- BCA corporate (KlikBCA Bisnis / CorpAcctTrxn) auto-detect -----

    def test_bca_corp_autodetect(self):
        # Real-world layout: preamble + DD/MM dates (no year) + fused Jumlah+CR/DB
        # column + Saldo Awal/Mutasi/Saldo Akhir footer. Any template routes to the
        # BCA-corp handler once the signature is detected.
        csv = (
            b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
            b"\n"
            b'"No. rekening : 2685151268"\n'
            b'"Nama : ERA BUSANA RETAILINDO PT"\n'
            b'"Periode : 01/06/2026 - 30/06/2026"\n'
            b'"Kode Mata Uang : Rp"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"01/06","BIAYA ADM       ","0000","30,000.00 DB","2,313,178.27"\n'
            b'"13/06","KR OTOMATIS MID : 885004608391 RETAIL PIM 2   ","0000","10,930,941.82 CR","13,244,120.09"\n'
            b'"Saldo Awal : 2,343,178.27"\n'
            b'"Mutasi Debet : 30,000.00","1"\n'
            b'"Mutasi Kredit : 10,930,941.82","1"\n'
            b'"Saldo Akhir : 13,244,120.09"\n'
        )
        res = self.template.parse_csv(base64.b64encode(csv).decode())
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 2)
        by_amount = sorted(res["lines"], key=lambda ln: ln["amount"])
        debit, credit = by_amount[0], by_amount[1]
        # DB -> negative (money out), CR -> positive (money in)
        self.assertEqual(debit["amount"], Decimal("-30000.00"))
        self.assertEqual(credit["amount"], Decimal("10930941.82"))
        # DD/MM resolved against the period year, whitespace-collapsed description
        self.assertEqual(debit["date"].year, 2026)
        self.assertEqual(debit["date"].month, 6)
        self.assertEqual(debit["date"].day, 1)
        self.assertEqual(debit["ref"], "BIAYA ADM")
        self.assertIn("RETAIL PIM 2", credit["ref"])

    def test_bca_corp_full_date_variant(self):
        # Second real-world variant: "Tanggal Transaksi" carries the full
        # DD/MM/YYYY date instead of year-less DD/MM.
        csv = (
            b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
            b'"Periode : 01/07/2026 - 20/07/2026"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"01/07/2026","KR OTOMATIS MID : 885004608387 RETAIL SENAYAN CITY  ","0998","4,689,356.40 CR","7,273,422.02"\n'
            b'"20/07/2026","TRSF E-BANKING DB","0000","960,064,168.95 DB","2,500,000.00"\n'
            b'"Saldo Akhir : 2,500,000.00"\n'
        )
        res = self.template.parse_csv(base64.b64encode(csv).decode())
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 2)
        credit, debit = res["lines"]
        self.assertEqual(credit["amount"], Decimal("4689356.40"))
        self.assertEqual(credit["date"], debit["date"].replace(day=1))
        self.assertEqual(debit["date"].isoformat(), "2026-07-20")
        self.assertEqual(debit["amount"], Decimal("-960064168.95"))

    def test_bca_corp_via_wizard_imports(self):
        csv = (
            b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
            b'"Periode : 01/06/2026 - 30/06/2026"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"01/06","BIAYA ADM","0000","30,000.00 DB","2,313,178.27"\n'
            b'"13/06","SETORAN","0000","1,000,000.00 CR","3,313,178.27"\n'
            b'"Saldo Akhir : 3,313,178.27"\n'
        )
        wiz = self.Wizard.create(
            {
                "journal_id": self.journal.id,
                "template_id": self.template.id,
                "file": base64.b64encode(csv).decode(),
                "filename": "bca-corp.csv",
            }
        )
        action = wiz.action_import()
        log = self.Log.browse(action["res_id"])
        self.assertEqual(log.state, "imported")
        self.assertEqual(log.line_count, 2)
        self.assertEqual(log.error_count, 0)
        amounts = sorted(log.statement_id.line_ids.mapped("amount"))
        self.assertEqual(amounts, [-30000.0, 1000000.0])

    def test_brisim_xlsx_autodetect(self):
        # BRI "BRISIM" export: only Tanggal + Uraian cells are trustworthy;
        # debet/kredit/saldo live inside the Uraian text, and one Uraian can
        # embed a second timestamped transaction.
        import io as _io

        import openpyxl

        wb = openpyxl.Workbook()
        sh = wb.active
        sh.append(["Tanggal", "Uraian", "Teller", "Debet", "Kredit", "Saldo"])
        sh.append(
            [
                "01/07/26 03:23:24",
                "OnUs 1 260630 001999664883 RETAIL GANDARI BRIMTXDT 0.00 "
                "1,099,249.00 278,074,877.00 AMT:1.100.900,00MDR:1.651,00",
                "01/07/26",
                "03:24:00",
                "OffUs",
                "1",
            ]
        )
        sh.append(
            [
                "14/07/26 08:37:41",
                "QRISOffUs_3_260713_001999660763_RETAIL GA BRIMTXDT 0.00 449,900.00 "
                "1,076,852,637.00 AMT:449.900,00MDR:0,00 14/07/26 13:39:49 DEBET BY CEK "
                "CGZ 006326 SD 6350 1BK 0019052 275,000.00 0.00 1,076,577,637.00 ESB:INDS:0002B00F",
                "15/07/26",
                "03:23:47",
                "OffUs",
                "1",
            ]
        )
        buf = _io.BytesIO()
        wb.save(buf)
        res = self.template.parse_csv(base64.b64encode(buf.getvalue()).decode())
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 3)
        first, qris, cek = res["lines"]
        self.assertEqual(first["date"].isoformat(), "2026-07-01")
        self.assertEqual(first["amount"], Decimal("1099249.00"))
        self.assertEqual(first["balance"], Decimal("278074877.00"))
        self.assertEqual(qris["amount"], Decimal("449900.00"))
        # embedded transaction gets its own timestamp's date and negative amount
        self.assertEqual(cek["date"].isoformat(), "2026-07-14")
        self.assertEqual(cek["amount"], Decimal("-275000.00"))
        self.assertIn("DEBET BY CEK", cek["ref"])

    def test_bni_trx_inquiry_xls_rows(self):
        # BNIDirect layout is asserted at row level (building a real BIFF .xls in
        # a test needs xlwt, which isn't shipped); _read_xls yields exactly this
        # row shape with native datetimes for Post Date cells.
        from datetime import datetime as dt

        rows = [
            [""] * 25,
            ["", "", "TRANSACTION INQUIRY"] + [""] * 22,
            [
                "",
                "",
                "No.",
                "",
                "",
                "",
                "",
                "Post Date",
                "Branch",
                "",
                "",
                "Journal No.",
                "Description",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "Amount",
                "",
                "Db/Cr",
                "Balance",
            ],
            [
                "",
                "",
                1.0,
                "",
                "",
                "",
                "",
                dt(2026, 7, 2, 5, 28, 6),
                "Unit E-Banking",
                "",
                "",
                "424575",
                "TRANSFER DARI | X",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                1.0,
                "",
                "C",
                204656297.0,
            ],
            [
                "",
                "",
                2.0,
                "",
                "",
                "",
                "",
                dt(2026, 7, 8, 8, 33, 54),
                "HARMONI",
                "",
                "",
                "418141",
                "TRANSFER KE | Y",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                275000.0,
                "",
                "D",
                204381299.0,
            ],
        ]
        self.assertTrue(self.template._is_trx_inquiry(rows))
        res = self.template._parse_trx_inquiry(rows)
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 2)
        credit, debit = res["lines"]
        self.assertEqual(credit["date"].isoformat(), "2026-07-02")
        self.assertEqual(credit["amount"], Decimal("1"))
        self.assertEqual(debit["amount"], Decimal("-275000"))
        self.assertEqual(debit["ref"], "TRANSFER KE | Y")

    def test_mandiri_acc_statement_xls_rows(self):
        rows = [
            [""] * 16,
            ["", "Account No", "", "", "", "", "1680008812008"] + [""] * 9,
            ["", "Opening Balance", "", "", "", "", "1,171,282,721.98"] + [""] * 9,
            [
                "",
                "Posting Date",
                "",
                "",
                "Remark",
                "",
                "",
                "Reference No",
                "",
                "Debit",
                "",
                "Credit",
                "",
                "",
                "",
                "Balance",
            ],
            [
                "",
                "02/07/2026 04:30:58",
                "",
                "",
                "QR RETAIL",
                "",
                "",
                "-",
                "",
                "0.00",
                "",
                "1.00",
                "",
                "",
                "",
                "1,171,282,722.98",
            ],
            [
                "",
                "10/07/2026 23:59:00",
                "",
                "",
                "Buku Cek 00263001",
                "",
                "",
                "-",
                "",
                "25,000.00",
                "",
                "0.00",
                "",
                "",
                "",
                "1,171,257,722.98",
            ],
            [""] * 16,
            ["", "", "No of Debit", "", "", "", "2"] + [""] * 9,
        ]
        self.assertTrue(self.template._is_acc_statement(rows))
        res = self.template._parse_acc_statement(rows)
        self.assertEqual(res["errors"], [])
        self.assertEqual(len(res["lines"]), 2)
        credit, debit = res["lines"]
        self.assertEqual(credit["date"].isoformat(), "2026-07-02")
        self.assertEqual(credit["amount"], Decimal("1.00"))
        self.assertEqual(debit["date"].isoformat(), "2026-07-10")
        self.assertEqual(debit["amount"], Decimal("-25000.00"))
        self.assertEqual(debit["ref"], "Buku Cek 00263001")

    def test_wizard_restores_dates_shifted_by_fiscal_lock(self):
        # Posting shifts locked-period dates to today; the wizard must write the
        # bank transaction date back (soft lock only — hard lock refuses import).
        self.env.company.fiscalyear_lock_date = "2026-06-30"
        csv = (
            b'"Informasi Rekening - Mutasi Rekening"," "," "," "," ",\n'
            b'"Periode : 01/06/2026 - 30/06/2026"\n'
            b'"Tanggal Transaksi","Keterangan","Cabang","Jumlah","Saldo"\n'
            b'"05/06","BIAYA ADM LOCK","0000","30,000.00 DB","2,313,178.27"\n'
            b'"Saldo Akhir : 2,313,178.27"\n'
        )
        wiz = self.Wizard.create(
            {
                "journal_id": self.journal.id,
                "template_id": self.template.id,
                "file": base64.b64encode(csv).decode(),
                "filename": "bca-corp-locked.csv",
            }
        )
        action = wiz.action_import()
        log = self.Log.browse(action["res_id"])
        self.assertEqual(log.state, "imported")
        line = log.statement_id.line_ids
        self.assertEqual(line.date.isoformat(), "2026-06-05")
        self.assertEqual(line.move_id.date.isoformat(), "2026-06-05")

    # ----- H2H mock adapter -----

    def test_h2h_mock_adapter_creates_lines(self):
        _MockH2HAdapter.fixture_lines = [
            {"date": "2026-05-01", "description": "Salary", "ref": "SAL001", "amount": 5000.0},
            {"date": "2026-05-02", "description": "Vendor pay", "ref": "VND001", "amount": -1200.0},
        ]
        cfg = self.AdapterConfig.create(
            {
                "name": "test-h2h",
                "adapter_type": "bank_test_mock_h2h",
                "base_url": "http://localhost/none",
                "auth_method": "none",
                "timeout_s": 1,
                "retry_count": 1,
                "circuit_breaker_threshold": 5,
                "circuit_breaker_cooldown_s": 60,
            }
        )
        conn = self.Conn.create(
            {
                "name": "Mock Conn",
                "bank_code": "Other",
                "adapter_config_id": cfg.id,
                "account_number": "1234567890",
                "journal_id": self.journal.id,
                "sync_interval_minutes": 1,
            }
        )
        conn._do_sync()
        log = self.Log.search(
            [("journal_id", "=", self.journal.id), ("filename", "like", "h2h-Other-%")], limit=1, order="id desc"
        )
        self.assertTrue(log)
        self.assertEqual(log.line_count, 2)
        self.assertTrue(log.statement_id)
        self.assertEqual(len(log.statement_id.line_ids), 2)
        self.assertEqual(conn.status, "active")
        self.assertTrue(conn.last_sync_at)
