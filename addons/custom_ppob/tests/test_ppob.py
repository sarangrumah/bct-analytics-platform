# Part of custom_ppob. Licence: LGPL-3.
"""Tests for the PPOB transaction fact: state machine, SLA clock, dimension and masking."""

from datetime import timedelta

import psycopg2

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger

from odoo.addons.custom_ppob.models.ppob_transaction import (
    PPOB_STATES,
    PPOB_TRANSITIONS,
)


@tagged("post_install", "-at_install", "ppob")
class TestPpobTransaction(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref("base.main_company")
        cls.biller = cls.env["ppob.biller"].create({
            "name": "PLN Prabayar (uji)",
            "code": "TEST-PLN-PRE",
            "category": "electricity",
            "sla_target_seconds": 30,
        })
        cls.unit = cls.env["operating.unit"].create({
            "name": "Cabang PPOB Uji",
            "code": "OU-PPOB-TEST",
            "company_id": cls.company.id,
        })

    def _txn(self, **overrides):
        values = {
            "biller_id": self.biller.id,
            "operating_unit_id": self.unit.id,
            "customer_ref": "5300000000001",
            "amount": 100000.0,
            "admin_fee": 2500.0,
            "commission": 1500.0,
        }
        values.update(overrides)
        return self.env["ppob.transaction"].create(values)

    # -- schema ---------------------------------------------------------

    def test_reference_comes_from_the_sequence(self):
        txn = self._txn()
        self.assertTrue(txn.name.startswith("PPOB/"), txn.name)
        self.assertNotEqual(txn.name, "New")

    def test_state_selection_matches_the_contract(self):
        keys = [key for key, _label in PPOB_STATES]
        self.assertEqual(keys, ["draft", "pending", "success", "failed", "reversed"])

    def test_operating_unit_is_stored_and_indexed(self):
        field = self.env["ppob.transaction"]._fields["operating_unit_id"]
        self.assertTrue(field.store)
        self.assertTrue(field.index)
        self.env.cr.execute(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' "
            "AND tablename = 'ppob_transaction' AND indexdef LIKE %s",
            ("%operating_unit_id%",),
        )
        self.assertTrue(self.env.cr.fetchall())

    def test_total_amount_is_a_stored_column(self):
        txn = self._txn()
        self.assertEqual(txn.total_amount, 102500.0)
        self.env.flush_all()  # stored computes reach Postgres on flush, not on assignment
        self.env.cr.execute(
            "SELECT total_amount FROM ppob_transaction WHERE id = %s", (txn.id,)
        )
        self.assertEqual(float(self.env.cr.fetchone()[0]), 102500.0)

    # -- state machine --------------------------------------------------

    def test_happy_path(self):
        txn = self._txn()
        self.assertEqual(txn.state, "draft")
        txn.action_submit()
        self.assertEqual(txn.state, "pending")
        txn.action_succeed(biller_reference="BILL-REF-1")
        self.assertEqual(txn.state, "success")
        self.assertEqual(txn.biller_reference, "BILL-REF-1")
        self.assertTrue(txn.settled_at)

    def test_failure_path(self):
        txn = self._txn()
        txn.action_submit()
        txn.action_fail(reason="Saldo biller habis")
        self.assertEqual(txn.state, "failed")
        self.assertEqual(txn.failure_reason, "Saldo biller habis")

    def test_reversal_path(self):
        txn = self._txn()
        txn.action_submit()
        txn.action_succeed()
        txn.action_reverse(reason="Salah nomor")
        self.assertEqual(txn.state, "reversed")

    def test_every_illegal_transition_is_refused(self):
        """Exhaustive: for each state, every target outside the allowed set must raise."""
        all_states = [key for key, _label in PPOB_STATES]
        model = self.env["ppob.transaction"]
        for source, allowed in PPOB_TRANSITIONS.items():
            for target in all_states:
                if target == source or target in allowed:
                    continue
                with self.subTest(source=source, target=target):
                    with self.assertRaises(UserError):
                        model._assert_transition(source, target)

    def test_cannot_skip_pending(self):
        txn = self._txn()
        with self.assertRaises(UserError):
            txn.write({"state": "success"})

    def test_cannot_reopen_a_failed_transaction(self):
        txn = self._txn()
        txn.action_submit()
        txn.action_fail()
        with self.assertRaises(UserError):
            txn.write({"state": "pending"})

    def test_cannot_create_directly_in_a_settled_state(self):
        with self.assertRaises(UserError):
            self._txn(state="success")

    def test_submit_requires_a_customer_reference(self):
        txn = self._txn(customer_ref=False)
        with self.assertRaises(UserError):
            txn.action_submit()

    def test_amounts_are_frozen_once_terminal(self):
        txn = self._txn()
        txn.action_submit()
        txn.action_succeed()
        with self.assertRaises(UserError):
            txn.write({"amount": 1.0})

    # -- money ----------------------------------------------------------

    @mute_logger("odoo.sql_db")
    def test_negative_amounts_refused(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self._txn(amount=-1.0)

    @mute_logger("odoo.sql_db")
    def test_commission_cannot_exceed_admin_fee(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self._txn(admin_fee=1000.0, commission=2000.0)

    # -- SLA clock ------------------------------------------------------

    def test_sla_seconds_is_measured_and_stored(self):
        requested = fields.Datetime.now()
        txn = self._txn(requested_at=requested)
        txn.action_submit()
        txn.action_succeed(settled_at=requested + timedelta(seconds=12))
        self.assertEqual(txn.sla_seconds, 12)
        self.assertFalse(txn.sla_breached)
        self.env.flush_all()
        self.env.cr.execute(
            "SELECT sla_seconds FROM ppob_transaction WHERE id = %s", (txn.id,)
        )
        self.assertEqual(self.env.cr.fetchone()[0], 12)

    def test_sla_breach_is_flagged(self):
        requested = fields.Datetime.now()
        txn = self._txn(requested_at=requested)
        txn.action_submit()
        txn.action_succeed(settled_at=requested + timedelta(seconds=45))
        self.assertEqual(txn.sla_seconds, 45)
        self.assertTrue(txn.sla_breached)

    def test_cannot_settle_before_request(self):
        requested = fields.Datetime.now()
        txn = self._txn(requested_at=requested)
        txn.action_submit()
        with self.assertRaises(ValidationError):
            txn.action_succeed(settled_at=requested - timedelta(seconds=10))

    # -- PDP ------------------------------------------------------------

    def test_customer_ref_is_classified_sensitive(self):
        row = self.env["pdp.field.classification"].get_classification(
            "ppob.transaction", "customer_ref"
        )
        self.assertTrue(row)
        self.assertEqual(row["pdp_class"], "sensitive")

    def test_customer_ref_is_masked_for_a_non_viewer(self):
        txn = self._txn()
        user = self.env["res.users"].create({
            "name": "PPOB Plain",
            "login": "ppob_plain_user",
            "group_ids": [(6, 0, [
                self.env.ref("custom_ppob.group_ppob_user").id,
                self.env.ref("custom_operating_unit.group_operating_unit_all").id,
            ])],
        })
        row = txn.with_user(user).read(["name", "customer_ref", "failure_reason"])[0]
        self.assertNotIn("5300000000001", row["customer_ref"] or "")
        self.assertTrue(row["customer_ref"].startswith("***"))
        # The system-generated reference is deliberately NOT masked.
        self.assertEqual(row["name"], txn.name)


@tagged("post_install", "-at_install", "ppob")
class TestPpobBiller(TransactionCase):

    @mute_logger("odoo.sql_db")
    def test_code_is_unique(self):
        self.env["ppob.biller"].create({"name": "A", "code": "TEST-DUP", "category": "other"})
        with self.assertRaises(psycopg2.errors.UniqueViolation):
            with self.env.cr.savepoint():
                self.env["ppob.biller"].create({
                    "name": "B", "code": "TEST-DUP", "category": "other",
                })

    @mute_logger("odoo.sql_db")
    def test_sla_target_must_be_positive(self):
        with self.assertRaises(psycopg2.errors.CheckViolation):
            with self.env.cr.savepoint():
                self.env["ppob.biller"].create({
                    "name": "C", "code": "TEST-SLA", "category": "other",
                    "sla_target_seconds": 0,
                })
