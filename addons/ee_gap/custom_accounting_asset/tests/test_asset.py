# -*- coding: utf-8 -*-
from datetime import date


from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install")
class TestCustomFixedAsset(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.Account = cls.env["account.account"]
        cls.Journal = cls.env["account.journal"]

        cls.asset_account = cls.Account.create(
            {
                "name": "FA - Equipment",
                "code": "150100",
                "account_type": "asset_fixed",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.accum_account = cls.Account.create(
            {
                "name": "FA - Accum. Depreciation",
                "code": "150900",
                "account_type": "asset_fixed",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.expense_account = cls.Account.create(
            {
                "name": "Depreciation Expense",
                "code": "610100",
                "account_type": "expense",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.journal = cls.Journal.search(
            [
                ("type", "=", "general"),
                ("company_id", "=", cls.company.id),
            ],
            limit=1,
        ) or cls.Journal.create(
            {
                "name": "Misc Operations",
                "code": "MISC",
                "type": "general",
                "company_id": cls.company.id,
            }
        )

        cls.surplus_account = cls.Account.create(
            {
                "name": "Revaluation Surplus",
                "code": "320100",
                "account_type": "equity",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.reval_loss_account = cls.Account.create(
            {
                "name": "Revaluation Loss",
                "code": "699200",
                "account_type": "expense",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.reval_income_account = cls.Account.create(
            {
                "name": "Revaluation Income",
                "code": "799200",
                "account_type": "income_other",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )
        cls.retained_earnings_account = cls.Account.create(
            {
                "name": "Retained Earnings",
                "code": "330100",
                "account_type": "equity",
                "company_ids": [(6, 0, [cls.company.id])],
            }
        )

        cls.group = cls.env["custom.fixed.asset.group"].create(
            {
                "name": "Equipment",
                "code": "EQ",
                "default_useful_life_months": 12,
                "default_asset_account_id": cls.asset_account.id,
                "default_depreciation_account_id": cls.accum_account.id,
                "default_expense_account_id": cls.expense_account.id,
                "default_journal_id": cls.journal.id,
            }
        )

    def _make_asset(self, **overrides):
        vals = {
            "name": "Test Laptop",
            "group_id": self.group.id,
            "acquisition_date": date(2025, 1, 1),
            "acquisition_value": 12000.0,
            "salvage_value": 0.0,
            "useful_life_months": 12,
            "depreciation_method": "straight_line",
            "asset_account_id": self.asset_account.id,
            "depreciation_account_id": self.accum_account.id,
            "expense_account_id": self.expense_account.id,
            "journal_id": self.journal.id,
        }
        vals.update(overrides)
        return self.env["custom.fixed.asset"].create(vals)

    def test_01_create_and_build_schedule(self):
        asset = self._make_asset()
        self.assertEqual(asset.state, "draft")
        self.assertTrue(asset.code and asset.code != "New")
        asset.action_confirm()
        self.assertEqual(asset.state, "running")
        self.assertEqual(len(asset.depreciation_line_ids), 12)
        # Sum of schedule equals depreciable base.
        total = sum(asset.depreciation_line_ids.mapped("amount"))
        self.assertAlmostEqual(total, 12000.0, places=2)
        # First line dated one month after acquisition.
        first = asset.depreciation_line_ids.sorted("sequence")[0]
        self.assertEqual(first.date, date(2025, 2, 1))

    def test_02_post_three_months(self):
        asset = self._make_asset()
        asset.action_confirm()
        as_of = date(2025, 4, 5)
        posted = asset._post_due_depreciation(as_of=as_of)
        self.assertEqual(posted, 3)
        posted_lines = asset.depreciation_line_ids.filtered("posted")
        self.assertEqual(len(posted_lines), 3)
        for line in posted_lines:
            self.assertTrue(line.move_id)
            self.assertEqual(line.move_id.state, "posted")
        # Accumulated depreciation should be 3 months worth (3000) and NBV 9000.
        self.assertAlmostEqual(asset.accumulated_depreciation, 3000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 9000.0, places=2)

    def test_03_dispose_with_gain(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))

        gain_account = self.Account.create(
            {
                "name": "Gain on disposal",
                "code": "799100",
                "account_type": "income_other",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        loss_account = self.Account.create(
            {
                "name": "Loss on disposal",
                "code": "699100",
                "account_type": "expense",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        proceeds_account = self.Account.create(
            {
                "name": "Disposal proceeds clearing",
                "code": "110900",
                "account_type": "asset_current",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )

        wiz = self.env["custom.fixed.asset.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "disposal_date": date(2025, 4, 30),
                "disposal_value": 10000.0,  # NBV is 9000 -> 1000 gain
                "gain_account_id": gain_account.id,
                "loss_account_id": loss_account.id,
                "receivable_account_id": proceeds_account.id,
                "create_journal_entry": True,
            }
        )
        self.assertAlmostEqual(wiz.gain_loss, 1000.0, places=2)
        wiz.action_dispose()
        self.assertEqual(asset.state, "disposed")
        self.assertAlmostEqual(asset.disposal_gain_loss, 1000.0, places=2)
        self.assertTrue(asset.disposal_move_id)
        # Move balanced.
        move = asset.disposal_move_id
        debits = sum(move.line_ids.mapped("debit"))
        credits = sum(move.line_ids.mapped("credit"))
        self.assertAlmostEqual(debits, credits, places=2)

    def test_04_constraints_and_cancel(self):
        # Salvage > acquisition forbidden.
        with self.assertRaises(ValidationError):
            self._make_asset(salvage_value=20000.0)
        # Useful life zero forbidden.
        with self.assertRaises(ValidationError):
            self._make_asset(useful_life_months=0)
        # Cancel from draft works; reset works.
        a = self._make_asset()
        a.action_cancel()
        self.assertEqual(a.state, "cancelled")
        a.action_reset_draft()
        self.assertEqual(a.state, "draft")
        # Cannot dispose draft asset.
        with self.assertRaises(UserError):
            a.action_open_dispose_wizard()

    def test_05_cron_posts_due_only(self):
        a1 = self._make_asset(name="Asset A")
        a2 = self._make_asset(name="Asset B")
        a1.action_confirm()
        a2.action_confirm()
        # Simulate cron run with a frozen as_of through direct invocation.
        with self._mock_today(date(2025, 3, 15)):
            count = self.env["custom.fixed.asset"]._cron_post_due_depreciation()
        # Two assets, 2 months due each -> 4 lines posted.
        self.assertEqual(count, 4)

    def test_06_asset_register_report(self):
        # 12000 over 12 months from 2025-01-01 -> 1000/month, first line
        # 2025-02-01. So 11 monthly lines (Feb..Dec) fall in 2025.
        asset = self._make_asset()
        asset.action_confirm()

        rep = self.env["custom.report.asset.register"]
        filters = {
            "date_from": date(2025, 1, 1),
            "date_to": date(2025, 12, 31),
            "company_ids": [self.company.id],
            "year": 2025,
        }
        lines = rep._build_lines(filters)
        row = next(l for l in lines if l.get("code") == asset.code)
        self.assertAlmostEqual(row["acq_value"], 12000.0, places=2)
        self.assertAlmostEqual(row["opening"], 0.0, places=2)
        self.assertAlmostEqual(row["ytd"], 11000.0, places=2)
        self.assertAlmostEqual(row["accum_end"], 11000.0, places=2)
        self.assertAlmostEqual(row["book"], 1000.0, places=2)
        # Monthly columns: Jan (m0) = 0, Feb (m1) = 1000.
        self.assertAlmostEqual(row["m0"], 0.0, places=2)
        self.assertAlmostEqual(row["m1"], 1000.0, places=2)

        grand = next(l for l in lines if l.get("type") == "grand_total")
        self.assertAlmostEqual(grand["ytd"], 11000.0, places=2)

    def test_07_posting_date_modes(self):
        # posting_date defaults to acquisition_date; next_month keeps behavior.
        a_next = self._make_asset(depreciation_date_mode="next_month")
        self.assertEqual(a_next.posting_date, date(2025, 1, 1))
        a_next.action_confirm()
        self.assertEqual(a_next.depreciation_line_ids.sorted("sequence")[0].date, date(2025, 2, 1))

        # specific: line 1 lands exactly on the posting date.
        a_spec = self._make_asset(depreciation_date_mode="specific", posting_date=date(2025, 1, 10))
        a_spec.action_confirm()
        spec_lines = a_spec.depreciation_line_ids.sorted("sequence")
        self.assertEqual(spec_lines[0].date, date(2025, 1, 10))
        self.assertEqual(spec_lines[1].date, date(2025, 2, 10))

        # end_following_month: last day of the month following the anchor.
        a_eom = self._make_asset(depreciation_date_mode="end_following_month", posting_date=date(2025, 1, 15))
        a_eom.action_confirm()
        eom_lines = a_eom.depreciation_line_ids.sorted("sequence")
        self.assertEqual(eom_lines[0].date, date(2025, 2, 28))
        self.assertEqual(eom_lines[1].date, date(2025, 3, 31))

    def test_08_bulk_post_wizard_and_action(self):
        a1 = self._make_asset(name="Bulk A")
        a2 = self._make_asset(name="Bulk B")
        a1.action_confirm()
        a2.action_confirm()

        # Wizard posts every running asset's lines due on/before the cutoff.
        wiz = self.env["custom.fixed.asset.post.wizard"].create({"cutoff_date": date(2025, 3, 5)})
        wiz.action_post()
        self.assertEqual(len(a1.depreciation_line_ids.filtered("posted")), 2)
        self.assertEqual(len(a2.depreciation_line_ids.filtered("posted")), 2)

        # Multi-select server-action entry point posts the rest as of today.
        with self._mock_today(date(2025, 6, 5)):
            (a1 | a2).action_post_selected()
        self.assertEqual(len(a1.depreciation_line_ids.filtered("posted")), 5)
        self.assertEqual(len(a2.depreciation_line_ids.filtered("posted")), 5)

    def test_09_revaluation_upward_prospective(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # 3 months posted
        posted_before = asset.depreciation_line_ids.filtered("posted")
        posted_moves = posted_before.mapped("move_id")
        self.assertEqual(len(posted_before), 3)

        wiz = self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 13500.0,  # NBV 9000 -> +4500
                "new_remaining_life": 6,
                "surplus_account_id": self.surplus_account.id,
                "journal_id": self.journal.id,
            }
        )
        self.assertAlmostEqual(wiz.revaluation_amount, 4500.0, places=2)
        wiz.action_revalue()

        # Posted lines and their moves are untouched.
        for line in posted_before:
            self.assertTrue(line.posted)
            self.assertAlmostEqual(line.amount, 1000.0, places=2)
        self.assertEqual(set(posted_before.mapped("move_id").ids), set(posted_moves.ids))
        for mv in posted_moves:
            self.assertEqual(mv.state, "posted")

        # Carrying value and schedule reflect the new value going forward.
        self.assertAlmostEqual(asset.revaluation_value, 4500.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 13500.0, places=2)
        self.assertEqual(asset.useful_life_months, 9)  # 3 posted + 6 remaining
        unposted = asset.depreciation_line_ids.filtered(lambda l: not l.posted)
        self.assertEqual(len(unposted), 6)
        self.assertAlmostEqual(sum(unposted.mapped("amount")), 13500.0, places=2)
        self.assertAlmostEqual(unposted.sorted("sequence")[0].amount, 2250.0, places=2)

        # A balanced revaluation entry exists: DR asset / CR surplus.
        self.assertEqual(asset.revaluation_count, 1)
        reval = asset.revaluation_ids
        move = reval.move_id
        self.assertTrue(move)
        self.assertAlmostEqual(sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")), places=2)
        asset_line = move.line_ids.filtered(lambda l: l.account_id == self.asset_account)
        surplus_line = move.line_ids.filtered(lambda l: l.account_id == self.surplus_account)
        self.assertAlmostEqual(asset_line.debit, 4500.0, places=2)
        self.assertAlmostEqual(surplus_line.credit, 4500.0, places=2)

    def test_10_revaluation_downward(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # NBV 9000

        wiz = self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 6000.0,  # -3000
                "new_remaining_life": 9,
                "loss_account_id": self.reval_loss_account.id,
                "journal_id": self.journal.id,
            }
        )
        wiz.action_revalue()
        self.assertAlmostEqual(asset.revaluation_value, -3000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 6000.0, places=2)
        move = asset.revaluation_ids.move_id
        loss_line = move.line_ids.filtered(lambda l: l.account_id == self.reval_loss_account)
        asset_line = move.line_ids.filtered(lambda l: l.account_id == self.asset_account)
        self.assertAlmostEqual(loss_line.debit, 3000.0, places=2)
        self.assertAlmostEqual(asset_line.credit, 3000.0, places=2)

    def test_11_disposal_after_revaluation_releases_full_carrying(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # accum 3000

        self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 13500.0,  # +4500 -> carrying 16500
                "new_remaining_life": 9,
                "surplus_account_id": self.surplus_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()

        proceeds_account = self.Account.create(
            {
                "name": "Disposal proceeds clearing",
                "code": "110950",
                "account_type": "asset_current",
                "company_ids": [(6, 0, [self.company.id])],
            }
        )
        wiz = self.env["custom.fixed.asset.disposal.wizard"].create(
            {
                "asset_id": asset.id,
                "disposal_date": date(2025, 5, 31),
                "disposal_value": 13500.0,  # equals NBV -> no gain/loss
                "receivable_account_id": proceeds_account.id,
                "surplus_account_id": self.surplus_account.id,
                "retained_earnings_account_id": self.retained_earnings_account.id,
            }
        )
        wiz.action_dispose()
        move = asset.disposal_move_id
        self.assertTrue(move)
        # Full carrying (acquisition 12000 + revaluation 4500) is released.
        asset_line = move.line_ids.filtered(lambda l: l.account_id == self.asset_account)
        self.assertAlmostEqual(asset_line.credit, 16500.0, places=2)
        # Revaluation surplus (4500) transferred to retained earnings and cleared.
        surplus_line = move.line_ids.filtered(lambda l: l.account_id == self.surplus_account)
        re_line = move.line_ids.filtered(lambda l: l.account_id == self.retained_earnings_account)
        self.assertAlmostEqual(surplus_line.debit, 4500.0, places=2)
        self.assertAlmostEqual(re_line.credit, 4500.0, places=2)
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 0.0, places=2)
        self.assertAlmostEqual(sum(move.line_ids.mapped("debit")), sum(move.line_ids.mapped("credit")), places=2)

    def test_12_downward_offsets_existing_surplus(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # NBV 9000

        # Upward first to create a 2000 surplus.
        self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 11000.0,  # +2000
                "new_remaining_life": 9,
                "surplus_account_id": self.surplus_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 2000.0, places=2)

        # Downward by 3000: 2000 offsets the surplus, 1000 goes to P&L loss.
        self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 5, 31),
                "new_value": 8000.0,  # -3000
                "new_remaining_life": 9,
                "surplus_account_id": self.surplus_account.id,
                "loss_account_id": self.reval_loss_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()

        self.assertAlmostEqual(asset.revaluation_surplus_balance, 0.0, places=2)
        self.assertAlmostEqual(asset.revaluation_loss_recognized, 1000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 8000.0, places=2)
        move = asset.revaluation_ids.sorted("id")[-1].move_id
        surplus_line = move.line_ids.filtered(lambda l: l.account_id == self.surplus_account)
        loss_line = move.line_ids.filtered(lambda l: l.account_id == self.reval_loss_account)
        asset_line = move.line_ids.filtered(lambda l: l.account_id == self.asset_account)
        self.assertAlmostEqual(surplus_line.debit, 2000.0, places=2)
        self.assertAlmostEqual(loss_line.debit, 1000.0, places=2)
        self.assertAlmostEqual(asset_line.credit, 3000.0, places=2)

    def test_13_upward_reverses_prior_loss(self):
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # NBV 9000

        # Downward first to expense a 3000 loss.
        self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 4, 30),
                "new_value": 6000.0,  # -3000
                "new_remaining_life": 9,
                "loss_account_id": self.reval_loss_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()
        self.assertAlmostEqual(asset.revaluation_loss_recognized, 3000.0, places=2)

        # Upward by 4000: 3000 reverses the prior loss (income), 1000 to surplus.
        self.env["custom.fixed.asset.revaluation.wizard"].create(
            {
                "asset_id": asset.id,
                "revaluation_date": date(2025, 5, 31),
                "new_value": 10000.0,  # +4000
                "new_remaining_life": 9,
                "surplus_account_id": self.surplus_account.id,
                "income_account_id": self.reval_income_account.id,
                "journal_id": self.journal.id,
            }
        ).action_revalue()

        self.assertAlmostEqual(asset.revaluation_loss_recognized, 0.0, places=2)
        self.assertAlmostEqual(asset.revaluation_surplus_balance, 1000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 10000.0, places=2)
        move = asset.revaluation_ids.sorted("id")[-1].move_id
        income_line = move.line_ids.filtered(lambda l: l.account_id == self.reval_income_account)
        surplus_line = move.line_ids.filtered(lambda l: l.account_id == self.surplus_account)
        asset_line = move.line_ids.filtered(lambda l: l.account_id == self.asset_account)
        self.assertAlmostEqual(income_line.credit, 3000.0, places=2)
        self.assertAlmostEqual(surplus_line.credit, 1000.0, places=2)
        self.assertAlmostEqual(asset_line.debit, 4000.0, places=2)

    def test_14_reverse_depreciation_line(self):
        # Reverse a posted depreciation line: status flips to reversed/unposted,
        # NBV recomputes, and the line is not re-posted by the schedule.
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # 3 posted, NBV 9000
        self.assertAlmostEqual(asset.net_book_value, 9000.0, places=2)
        line = asset.depreciation_line_ids.filtered("posted").sorted("sequence")[-1]

        line.action_reverse()
        self.assertFalse(line.posted)
        self.assertTrue(line.reversed)
        # accumulated drops one month (2000) and NBV restored to 10000
        self.assertAlmostEqual(asset.accumulated_depreciation, 2000.0, places=2)
        self.assertAlmostEqual(asset.net_book_value, 10000.0, places=2)
        # a later schedule run must NOT re-post the reversed line
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.assertFalse(line.posted)
        self.assertTrue(line.reversed)
        # reversing an unposted line is rejected
        with self.assertRaises(UserError):
            line.action_reverse()

    def test_15_external_move_delete_and_draft_self_heal(self):
        # Deleting / drafting the depreciation entry directly in Accounting must
        # un-post the line so NBV recomputes and the period can be re-posted
        # (feedback Accounting #4/#20).
        asset = self._make_asset()
        asset.action_confirm()
        asset._post_due_depreciation(as_of=date(2025, 4, 5))  # 3 posted
        line = asset.depreciation_line_ids.filtered("posted").sorted("sequence")[-1]
        move = line.move_id

        move.button_draft()
        self.assertFalse(line.posted)  # draft self-heals
        self.assertAlmostEqual(asset.accumulated_depreciation, 2000.0, places=2)

        move.unlink()
        self.assertFalse(line.posted)
        self.assertFalse(line.move_id)
        self.assertFalse(line.reversed)  # deletion -> repostable, not reversed

        # schedule reposts the freed line and NBV returns to 9000
        asset._post_due_depreciation(as_of=date(2025, 4, 5))
        self.assertTrue(line.posted)
        self.assertTrue(line.move_id)
        self.assertAlmostEqual(asset.accumulated_depreciation, 3000.0, places=2)

    def _mock_today(self, today):
        """Lightweight context manager that monkey-patches
        fields.Date.context_today for the duration of the with-block.
        """
        from contextlib import contextmanager
        from odoo import fields as odoo_fields

        original = odoo_fields.Date.context_today

        @contextmanager
        def _cm():
            odoo_fields.Date.context_today = staticmethod(lambda *a, **k: today)
            try:
                yield
            finally:
                odoo_fields.Date.context_today = original

        return _cm()
