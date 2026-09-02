# -*- coding: utf-8 -*-
"""Tests for ``retail.import.mailbox`` — the IMAP -> SFTP-share -> drop-dir bridge.

The mail server is faked (``_FakeIMAP``) so the suite never touches the network. What
we actually assert is the safety contract, because getting it wrong destroys source
data that only exists in the mailbox:

* every attachment is written to the backup directory and re-reads with its SHA256;
* the constant daily filename gets a date suffix, so an unimported file is never
  overwritten by the next day's;
* a message already in the ledger is not fetched twice;
* purge deletes on the strength of the *backup*, and refuses when the backup is
  missing, truncated, or the message is younger than the retention window;
* ``dry_run`` never issues STORE/EXPUNGE.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from datetime import datetime, timedelta
from email.message import EmailMessage
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase, tagged

_SENDER = "XcenterAdmin@levi.com"
_UIDVALIDITY = b"1745000000"


def _build_mail(filename, payload, sent):
    msg = EmailMessage()
    msg["From"] = _SENDER
    msg["To"] = "retail.data@example.invalid"
    msg["Subject"] = "%s was executed" % filename
    msg["Message-ID"] = "<%s@levi.com>" % filename
    msg["Date"] = sent.strftime("%a, %d %b %Y %H:%M:%S +0800")
    msg.set_content("report attached")
    msg.add_attachment(
        payload,
        maintype="application",
        subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )
    return msg.as_bytes()


class _FakeIMAP:
    """Minimal IMAP4 stand-in covering exactly the verbs the model issues."""

    capabilities = ("IMAP4REV1", "UIDPLUS")

    def __init__(self, messages):
        # messages: {uid: raw_bytes}
        self.messages = dict(messages)
        self.stored = []  # [(uid, flags)]
        self.expunged = []
        self.logged_out = False

    # -- imaplib surface -------------------------------------------------
    def response(self, name):
        if name == "UIDVALIDITY":
            # What mail.example.invalid actually returns: the bare value, no bracket.
            return "UIDVALIDITY", [_UIDVALIDITY]
        return "OK", [None]

    def uid(self, command, *args):
        command = command.upper()
        if command == "SEARCH":
            # ("SEARCH", None, "UID", "3")  vs  ("SEARCH", None, "UNDELETED", "FROM", '"x"')
            terms = [a for a in args[1:] if a]
            if terms and terms[0] == "UID":
                uid = int(terms[1])
                return "OK", [str(uid).encode() if uid in self.messages else b""]
            return "OK", [b" ".join(str(u).encode() for u in sorted(self.messages))]
        if command == "FETCH":
            uids = [int(u) for u in args[0].split(",")]
            what = args[1]
            if "RFC822.SIZE" in what:
                return "OK", [b"%d (RFC822.SIZE %d)" % (u, len(self.messages[u])) for u in uids]
            uid = uids[0]
            if uid not in self.messages:
                return "OK", [None]
            return "OK", [(b"%d (BODY[] {%d}" % (uid, len(self.messages[uid])), self.messages[uid])]
        if command == "STORE":
            self.stored.append((int(args[0]), args[2]))
            return "OK", [b""]
        if command == "EXPUNGE":
            uid = int(args[0])
            self.expunged.append(uid)
            self.messages.pop(uid, None)
            return "OK", [b""]
        raise AssertionError("unexpected IMAP command %s" % command)

    def expunge(self):
        raise AssertionError("UIDPLUS is advertised; per-UID EXPUNGE should be used")

    def close(self):
        pass

    def logout(self):
        self.logged_out = True


@tagged("post_install", "-at_install")
class TestRetailImportMailbox(TransactionCase):
    """Filesystem fixtures live in setUp, not setUpClass.

    TransactionCase rolls the database back between tests but not the disk, and several
    tests deliberately corrupt or delete a backup file. Sharing one directory would leak
    that damage into whichever test ran next.
    """

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.mkdtemp(prefix="rimb-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.backup_dir = os.path.join(self.tmp, "mailbox")
        self.drop_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.drop_dir, exist_ok=True)

        self.mailbox = self.env["retail.import.mailbox"].create(
            {
                "name": "Test X-center",
                "host": "mail.example.invalid",
                "username": "retail.data@example.invalid",
                "password_env": "RIMB_TEST_PASSWORD",
                "sender_filter": _SENDER,
                "attachment_glob": "*.xlsx",
                "ingest_glob": "X24*.xlsx,X70D*.xlsx",
                "backup_dir": self.backup_dir,
                "drop_dir": self.drop_dir,
                "retention_days": 30,
                "max_messages": 0,
                "max_bytes_mb": 0,
            }
        )

        sent_old = datetime(2026, 6, 1, 3, 30, 0)
        sent_new = datetime(2026, 7, 9, 3, 30, 0)
        self.x24_payload = b"PK\x03\x04 fake x24 workbook"
        self.x32p_payload = b"PK\x03\x04 fake x32p workbook"
        self.messages = {
            1: _build_mail("X24DN_Retail_Sales_Detail_Report.xlsx", self.x24_payload, sent_old),
            2: _build_mail("X32P_Stock_Movement_Report.xlsx", self.x32p_payload, sent_new),
        }

    # ------------------------------------------------------------------
    def _fake(self, messages=None):
        return _FakeIMAP(self.messages if messages is None else messages)

    def _fetch_with(self, client):
        with patch.object(type(self.mailbox), "_connect", return_value=client):
            return self.mailbox._fetch()

    def _purge_with(self, client):
        with patch.object(type(self.mailbox), "_connect", return_value=client):
            return self.mailbox._purge()

    @staticmethod
    def _sha(raw):
        return hashlib.sha256(raw).hexdigest()

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def test_fetch_backs_up_and_stages(self):
        stored = self._fetch_with(self._fake())
        self.assertEqual(stored, 2)

        rows = self.mailbox.fetched_ids
        self.assertEqual(len(rows), 2)
        x24 = rows.filtered(lambda r: r.filename.startswith("X24DN"))
        x32p = rows.filtered(lambda r: r.filename.startswith("X32P"))

        # X24 matches ingest_glob -> backed up AND staged for the feed.
        self.assertEqual(x24.state, "staged")
        self.assertTrue(os.path.isfile(x24.backup_path))
        self.assertTrue(os.path.isfile(x24.staged_path))
        self.assertEqual(self._sha(self.x24_payload), x24.sha256)
        with open(x24.backup_path, "rb") as fh:
            self.assertEqual(fh.read(), self.x24_payload)

        # X32P is backup-only: no copy lands in the feed's drop directory.
        self.assertEqual(x32p.state, "backed_up")
        self.assertTrue(os.path.isfile(x32p.backup_path))
        self.assertFalse(x32p.staged_path)
        self.assertEqual(os.listdir(self.drop_dir), [os.path.basename(x24.staged_path)])

    def test_staged_name_carries_date_suffix(self):
        """The daily filename is constant; without a suffix day N+1 clobbers day N."""
        self._fetch_with(self._fake())
        x24 = self.mailbox.fetched_ids.filtered(lambda r: r.filename.startswith("X24DN"))
        name = os.path.basename(x24.staged_path)
        # Sent 03:30 +0800 on 1 Jun -> 19:30 UTC on 31 May.
        self.assertEqual(name, "X24DN_Retail_Sales_Detail_Report__20260531T193000Z.xlsx")
        # Still matched by the feed glob, which is why the stamp is a suffix.
        import fnmatch

        self.assertTrue(fnmatch.fnmatch(name, "X24*.xlsx"))

    def test_fetch_is_idempotent(self):
        self.assertEqual(self._fetch_with(self._fake()), 2)
        self.assertEqual(self._fetch_with(self._fake()), 0)
        self.assertEqual(len(self.mailbox.fetched_ids), 2)

    def test_backup_dir_is_filed_by_month(self):
        self._fetch_with(self._fake())
        x24 = self.mailbox.fetched_ids.filtered(lambda r: r.filename.startswith("X24DN"))
        self.assertIn(os.path.join("2026", "05"), x24.backup_path)  # 31 May UTC

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------
    def _arm(self, dry_run=False, retention_days=30):
        self.mailbox.write({"purge_enabled": True, "dry_run": dry_run, "retention_days": retention_days})

    def _age_ledger(self):
        """Pin ledger dates relative to now: UID 1 well past retention, UID 2 fresh.

        The fixture's absolute dates would make these tests start failing once the wall
        clock passes the retention window.
        """
        now = fields.Datetime.now()
        rows = self.mailbox.fetched_ids
        rows.filtered(lambda r: r.uid == 1).email_date = now - timedelta(days=40)
        rows.filtered(lambda r: r.uid == 2).email_date = now - timedelta(hours=1)

    def test_purge_disabled_is_a_noop(self):
        self._fetch_with(self._fake())
        client = self._fake()
        self.assertEqual(self._purge_with(client), 0)
        self.assertFalse(client.stored)

    def test_purge_respects_retention_window(self):
        """UID 1 is 40 days old and goes; UID 2 is an hour old and stays."""
        self._fetch_with(self._fake())
        self._age_ledger()
        self._arm()

        client = self._fake()
        self.assertEqual(self._purge_with(client), 1)
        self.assertEqual(client.expunged, [1])
        self.assertEqual([f for _u, f in client.stored], ["(\\Deleted)"])

        rows = self.mailbox.fetched_ids
        self.assertEqual(rows.filtered(lambda r: r.uid == 1).state, "purged")
        self.assertEqual(rows.filtered(lambda r: r.uid == 2).state, "backed_up")

    def test_purge_refuses_when_backup_is_corrupted(self):
        self._fetch_with(self._fake())
        self._age_ledger()
        x24 = self.mailbox.fetched_ids.filtered(lambda r: r.uid == 1)
        with open(x24.backup_path, "wb") as fh:
            fh.write(b"truncated")

        self._arm()
        client = self._fake()
        self.assertEqual(self._purge_with(client), 0)
        self.assertFalse(client.stored, "a message with an unverifiable backup must never be deleted")

    def test_purge_refuses_when_backup_is_missing(self):
        self._fetch_with(self._fake())
        self._age_ledger()
        x24 = self.mailbox.fetched_ids.filtered(lambda r: r.uid == 1)
        os.unlink(x24.backup_path)

        self._arm()
        client = self._fake()
        self.assertEqual(self._purge_with(client), 0)
        self.assertFalse(client.stored)

    def test_purge_ignores_import_state(self):
        """Deletion keys off the backup, not the import: a failed import still purges."""
        self._fetch_with(self._fake())
        self._age_ledger()
        self._arm()
        client = self._fake()
        # No retail.import.log exists at all — nothing was ever imported.
        self.assertFalse(self.env["retail.import.log"].search([]))
        self.assertEqual(self._purge_with(client), 1)

    def test_dry_run_never_touches_the_server(self):
        self._fetch_with(self._fake())
        self._age_ledger()
        self._arm(dry_run=True)
        client = self._fake()
        self.assertEqual(self._purge_with(client), 0)
        self.assertFalse(client.stored)
        self.assertFalse(client.expunged)
        self.assertIn("Dry run", self.mailbox.last_message)
        self.assertEqual(self.mailbox.fetched_ids.filtered(lambda r: r.uid == 1).state, "staged")

    def test_emergency_drain_ignores_retention(self):
        """Over the size cap, backed-up messages go even inside the retention window."""
        self._fetch_with(self._fake())
        self._arm(retention_days=3650)  # nothing is old enough
        self.mailbox.write({"max_messages": 1})
        client = self._fake()
        purged = self._purge_with(client)
        self.assertEqual(purged, 1, "oldest message should be drained")
        self.assertEqual(client.expunged, [1])

    # ------------------------------------------------------------------
    def test_password_prefers_environment_variable(self):
        self.mailbox.password_param = "retail_import.test_mail_password"
        self.env["ir.config_parameter"].sudo().set_param("retail_import.test_mail_password", "from-db")
        self.assertEqual(self.mailbox._password(), "from-db")
        with patch.dict(os.environ, {"RIMB_TEST_PASSWORD": "from-env"}):
            self.assertEqual(self.mailbox._password(), "from-env")

    def test_uidvalidity_accepts_both_response_shapes(self):
        """imaplib gives the bare number; some servers give the whole bracket."""
        Mailbox = type(self.mailbox)

        class _Bare:
            def response(self, _name):
                return "UIDVALIDITY", [b"1"]

        class _Bracketed:
            def response(self, _name):
                return "OK", [b"[UIDVALIDITY 1745000000]"]

        class _Absent:
            def response(self, _name):
                return "OK", [None]

        self.assertEqual(Mailbox._uidvalidity(_Bare()), "1")
        self.assertEqual(Mailbox._uidvalidity(_Bracketed()), "1745000000")
        self.assertEqual(Mailbox._uidvalidity(_Absent()), "")
