# -*- coding: utf-8 -*-
"""Retail import mailbox — pull source files out of an IMAP mailbox.

The apparel brand X-center scheduler mails one report per message every night
(``XcenterAdmin@levi.com`` → ``retail.data@example.invalid``, ~10.4 MB/day across six
attachments). Nobody empties that mailbox, so it fills up. This model closes the
loop: fetch the attachments, keep a durable copy, hand the interesting ones to the
existing ``retail.import.feed`` drop directory, then delete the message.

Design
------
This is a **transport bridge, not a second importer**. It writes files into the
same ``local_dir`` that ``retail.import.feed`` (``source_type="local"``) already
polls, so the parse/import/archive pipeline is untouched. Two destinations:

* ``backup_dir`` — every matching attachment, filed under ``YYYY/MM/``. This is the
  durable copy and lives on the SFTP share (in production ``/mnt/data_retail`` is a
  bind mount of ``/srv/sftp-share/files``, so "back up to SFTP" is a plain write).
* ``drop_dir`` — only the attachments matching ``ingest_glob``; the feed picks them
  up on its own cron and archives them on success.

The X-center attachments carry the **same filename every day**
(``X24DN_Retail_Sales_Detail_Report.xlsx``), so a file that failed to import and is
still sitting in ``drop_dir`` would be silently overwritten by tomorrow's. Both
destinations therefore get a ``__<sent-date>`` suffix. It is a *suffix* so the feed
globs (``X24*.xlsx``) still match, and it records provenance only — the business
dates are read from inside the file.

Retention invariant
-------------------
**A message is deleted on the strength of its BACKUP, never its IMPORT.** A failed
import still has raw bytes in ``backup_dir`` and ``drop_dir`` and will be retried by
the feed; coupling deletion to import success would mean a parser bug could destroy
source data. ``_purge`` re-reads each backup off disk and re-checks its SHA256
before it touches the server.

Credentials never live in the database or in git: ``_password()`` reads
``password_env`` from the container environment first and only falls back to
``ir.config_parameter``.
"""

from __future__ import annotations

import email
import fnmatch
import hashlib
import imaplib
import logging
import os
import re
from datetime import timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from odoo import _, api, fields, models, modules, tools
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

# X-center ships attachments up to ~7 MB; a base64 literal for one arrives as a
# single IMAP line, far past imaplib's stock 10k _MAXLINE.
_IMAP_MAXLINE = 50 * 1024 * 1024

_UIDVALIDITY_RE = re.compile(rb"UIDVALIDITY\s+(\d+)", re.IGNORECASE)
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Arbitrary namespace for pg_try_advisory_lock(classid, objid); objid is the mailbox id.
_ADVISORY_LOCK_CLASS = 0x52494D42  # "RIMB"


class RetailImportMailbox(models.Model):
    _name = "retail.import.mailbox"
    _description = "Retail Import Mailbox (IMAP)"
    _order = "name"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, index=True)
    active = fields.Boolean(
        default=False,
        help="Leave off in every database but the one that owns this mailbox: two "
        "databases polling the same INBOX would race to delete each other's messages.",
    )
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)

    # Connection ---------------------------------------------------------------
    host = fields.Char(required=True)
    port = fields.Integer(default=993, required=True)
    use_ssl = fields.Boolean(default=True)
    username = fields.Char(required=True)
    password_env = fields.Char(
        string="Password Environment Variable",
        default="RETAIL_MAIL_PASSWORD",
        help="Name of an environment variable in the Odoo container holding the password. "
        "Preferred: the secret never reaches the database.",
    )
    password_param = fields.Char(
        string="Password ir.config_parameter Key",
        help="Fallback used only when the environment variable is unset. Stored in clear "
        "text in the database — prefer the environment variable.",
    )
    folder = fields.Char(default="INBOX", required=True)

    # What to take -------------------------------------------------------------
    sender_filter = fields.Char(help="Only messages whose From matches this are considered. Empty = every message.")
    attachment_glob = fields.Char(
        default="*.xlsx",
        required=True,
        help="Attachments matching this are backed up.",
    )
    ingest_glob = fields.Char(
        help="Comma-separated globs. Backed-up attachments also matching one of these are "
        "copied into the drop directory for retail.import.feed. Empty = back up only.",
    )

    drop_dir = fields.Char(
        default="/mnt/data_retail/data",
        help="Directory polled by the matching retail.import.feed records.",
    )
    backup_dir = fields.Char(
        required=True,
        default="/mnt/data_retail/mailbox",
        help="Durable archive of every fetched attachment, filed under YYYY/MM/.",
    )

    # Retention ----------------------------------------------------------------
    purge_enabled = fields.Boolean(
        default=False, help="Master switch. When off, messages are fetched but never deleted."
    )
    dry_run = fields.Boolean(
        default=True,
        help="Report what would be deleted without touching the server. Turn off deliberately.",
    )
    retention_days = fields.Integer(
        default=30,
        required=True,
        help="A backed-up message is deleted once it is older than this.",
    )
    max_messages = fields.Integer(
        default=400,
        help="Emergency drain threshold. Above this, backed-up messages are deleted oldest-first "
        "even before retention_days. 0 disables.",
    )
    max_bytes_mb = fields.Integer(default=600, help="Emergency drain threshold on total mailbox size. 0 disables.")

    # Gauges -------------------------------------------------------------------
    last_run = fields.Datetime(readonly=True)
    last_status = fields.Selection([("ok", "OK"), ("error", "Error"), ("idle", "Idle")], default="idle", readonly=True)
    last_message = fields.Text(readonly=True)
    uidvalidity = fields.Char(readonly=True, help="Last observed IMAP UIDVALIDITY.")
    msg_count = fields.Integer(readonly=True, help="Messages in the folder at the last fetch.")
    mailbox_bytes = fields.Float(readonly=True, digits=(16, 2), string="Mailbox Size (MB)", help="At the last fetch.")
    # NOT `message_ids`: mail.thread already owns that name for the chatter.
    fetched_ids = fields.One2many("retail.import.mail.message", "mailbox_id", string="Fetched")

    @api.constrains("retention_days")
    def _check_retention(self):
        for box in self:
            if box.retention_days < 0:
                raise ValidationError(_("Mailbox %s: retention days cannot be negative.") % box.name)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def _password(self):
        """Environment variable first, ir.config_parameter as a fallback. Never logged."""
        self.ensure_one()
        if self.password_env:
            value = os.environ.get(self.password_env)
            if value:
                return value
        if self.password_param:
            return self.env["ir.config_parameter"].sudo().get_param(self.password_param, "") or ""
        return ""

    def _connect(self):
        """Return a logged-in IMAP client with the folder selected read-write.

        Read-write because ``_purge`` needs ``UID STORE``. Message bodies are always
        fetched with ``BODY.PEEK[]`` so fetching never sets ``\\Seen``.
        """
        self.ensure_one()
        password = self._password()
        if not password:
            raise UserError(
                _(
                    "Mailbox %(name)s: no password. Set the %(env)s environment variable on the "
                    "Odoo container, or configure a fallback ir.config_parameter key."
                )
                % {"name": self.name, "env": self.password_env or "(unset)"}
            )
        # Module-level knob; raise it before any client reads a literal.
        imaplib._MAXLINE = max(imaplib._MAXLINE, _IMAP_MAXLINE)  # noqa: SLF001
        factory = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
        client = factory(self.host, self.port, timeout=60)
        try:
            client.login(self.username, password)
            typ, data = client.select(self.folder)
            if typ != "OK":
                raise UserError(
                    _("Mailbox %(name)s: cannot select folder %(folder)s.") % {"name": self.name, "folder": self.folder}
                )
        except Exception:
            try:
                client.logout()
            except Exception:  # noqa: BLE001 - already failing; don't mask the cause
                pass
            raise
        return client

    @staticmethod
    def _uidvalidity(client):
        """Read UIDVALIDITY out of the SELECT response.

        imaplib hands back the bare value (``[b'1']``) for untagged responses it parsed,
        but some servers surface the whole ``[UIDVALIDITY 1]`` bracket. Accept both: an
        empty result would silently disable the UID bookkeeping that purge depends on.
        """
        typ, data = client.response("UIDVALIDITY")
        for chunk in data or []:
            if not chunk:
                continue
            raw = chunk if isinstance(chunk, bytes) else str(chunk).encode()
            if raw.strip().isdigit():
                return raw.strip().decode()
            match = _UIDVALIDITY_RE.search(raw)
            if match:
                return match.group(1).decode()
        return ""

    def _search_uids(self, client):
        """UIDs of undeleted messages from the configured sender."""
        self.ensure_one()
        criteria = ["UNDELETED"]
        if self.sender_filter:
            criteria += ["FROM", '"%s"' % self.sender_filter]
        typ, data = client.uid("SEARCH", None, *criteria)
        if typ != "OK":
            raise UserError(_("Mailbox %s: IMAP SEARCH failed.") % self.name)
        return [int(u) for u in (data[0] or b"").split()]

    def action_test_connection(self):
        self.ensure_one()
        client = self._connect()
        try:
            uids = self._search_uids(client)
        finally:
            self._safe_logout(client)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection OK"),
                "message": _("%(n)s message(s) match in %(folder)s on %(host)s.")
                % {"n": len(uids), "folder": self.folder, "host": self.host},
                "type": "success",
                "sticky": False,
            },
        }

    @staticmethod
    def _safe_logout(client):
        try:
            client.close()
        except Exception:  # noqa: BLE001 - folder may already be closed
            pass
        try:
            client.logout()
        except Exception:  # noqa: BLE001 - best effort
            pass

    # ------------------------------------------------------------------
    # Filesystem helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _sha256(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _atomic_write(path: str, raw: bytes):
        """Write via a temp file in the same directory, fsync, then rename.

        The rename is atomic on POSIX, so a reader (the feed cron) never observes a
        half-written file, and a crash leaves either nothing or the complete file.
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.part" % path
        with open(tmp, "wb") as fh:
            fh.write(raw)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _verify_backup(self, path: str, sha256: str) -> bool:
        """Re-read the file off disk and confirm its digest. Never trust the DB column."""
        if not path or not os.path.isfile(path):
            return False
        try:
            with open(path, "rb") as fh:
                return self._sha256(fh.read()) == sha256
        except OSError:
            _logger.exception("Mailbox %s: cannot re-read backup %s", self.name, path)
            return False

    @staticmethod
    def _stamp(sent):
        """Compact UTC timestamp used as the filename provenance suffix."""
        return sent.strftime("%Y%m%dT%H%M%SZ") if sent else "nodate"

    @classmethod
    def _suffixed_name(cls, filename: str, sent, sha256: str = "") -> str:
        """``X24DN_Report.xlsx`` -> ``X24DN_Report__20260708T193007Z.xlsx``.

        A *suffix*, so the feed globs (``X24*.xlsx``) still match. Without it the
        constant daily filename would overwrite yesterday's not-yet-imported file.
        """
        stem, ext = os.path.splitext(_SAFE_NAME_RE.sub("_", filename or "attachment"))
        parts = [stem, cls._stamp(sent)]
        if sha256:
            parts.append(sha256[:8])
        return "%s%s" % ("__".join(parts), ext)

    def _matches_ingest(self, filename: str) -> bool:
        self.ensure_one()
        globs = [g.strip() for g in (self.ingest_glob or "").split(",") if g.strip()]
        return any(fnmatch.fnmatch(filename, g) for g in globs)

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------
    def _lock(self) -> bool:
        """Take a session-scoped advisory lock so a second worker skips this mailbox.

        Session-scoped, not row-level: ``_fetch`` commits after every message (so a crash
        never loses the uid -> backup mapping), and a ``FOR UPDATE`` row lock would be
        released by that commit. ``pg_try_advisory_lock`` also returns false rather than
        raising, which keeps a contended run from poisoning the transaction.
        """
        self.ensure_one()
        self.env.cr.execute("SELECT pg_try_advisory_lock(%s, %s)", (_ADVISORY_LOCK_CLASS, self.id))
        if not self.env.cr.fetchone()[0]:
            _logger.info("Mailbox %s: already being polled elsewhere; skipping.", self.name)
            return False
        return True

    def _unlock(self):
        self.ensure_one()
        self.env.cr.execute("SELECT pg_advisory_unlock(%s, %s)", (_ADVISORY_LOCK_CLASS, self.id))

    def _checkpoint(self):
        """Commit progress, except under tests where the cursor forbids it.

        Committing per message is what keeps a crash from losing the uid -> backup
        mapping: without it the next run would re-download the message, and purge could
        not prove the backup exists. Odoo's test cursor raises on commit, so skip it
        there — the tests assert on the ledger rows, not on durability.
        """
        if tools.config["test_enable"] or modules.module.current_test:
            return
        self.env.cr.commit()  # noqa: E8102 - deliberate durability checkpoint

    @staticmethod
    def _decode_header(value):
        if not value:
            return ""
        try:
            return str(make_header(decode_header(value)))
        except Exception:  # noqa: BLE001 - malformed headers must not stop ingestion
            return str(value)

    def _attachments(self, msg):
        """Yield (filename, raw_bytes) for parts matching ``attachment_glob``."""
        self.ensure_one()
        for part in msg.walk():
            filename = part.get_filename()
            if not filename:
                continue
            filename = self._decode_header(filename)
            if not fnmatch.fnmatch(filename, self.attachment_glob):
                continue
            raw = part.get_payload(decode=True)
            if raw:
                yield filename, raw

    def _fetch(self):
        """Back up every matching attachment, stage the ingestible ones. Returns a count."""
        self.ensure_one()
        Message = self.env["retail.import.mail.message"].sudo()
        Log = self.env["retail.import.log"].sudo()
        client = self._connect()
        stored = 0
        try:
            uidvalidity = self._uidvalidity(client)
            if uidvalidity and self.uidvalidity and uidvalidity != self.uidvalidity:
                # The server renumbered the folder: old UIDs point at nothing. Detach them
                # so we never STORE \Deleted against a UID that now means another message.
                stale = Message.search([("mailbox_id", "=", self.id), ("uidvalidity", "!=", uidvalidity)])
                stale.write({"uid": 0})
                _logger.warning(
                    "Mailbox %s: UIDVALIDITY changed %s -> %s; detached %s ledger row(s).",
                    self.name,
                    self.uidvalidity,
                    uidvalidity,
                    len(stale),
                )
            self.uidvalidity = uidvalidity

            uids = self._search_uids(client)
            self._update_gauges(client, uids)

            known = set(
                Message.search(
                    [
                        ("mailbox_id", "=", self.id),
                        ("uidvalidity", "=", uidvalidity),
                        ("state", "in", ("backed_up", "staged", "skipped", "purged")),
                    ]
                ).mapped("uid")
            )
            for uid in uids:
                if uid in known:
                    continue
                typ, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
                if typ != "OK" or not data or not isinstance(data[0], tuple):
                    _logger.warning("Mailbox %s: UID %s fetch returned nothing.", self.name, uid)
                    continue
                msg = email.message_from_bytes(data[0][1])
                stored += self._store_message(msg, uid, uidvalidity, Message, Log)
                self._checkpoint()
        finally:
            self._safe_logout(client)
        return stored

    def _store_message(self, msg, uid, uidvalidity, Message, Log):
        """Back up + stage one message's attachments. Returns attachments stored."""
        self.ensure_one()
        try:
            sent = parsedate_to_datetime(msg.get("Date"))
            if sent and sent.tzinfo:
                # Odoo stores naive UTC; the X-center mails carry +0800.
                sent = sent.astimezone(timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            sent = None
        base = {
            "mailbox_id": self.id,
            "uid": uid,
            "uidvalidity": uidvalidity,
            "message_id": self._decode_header(msg.get("Message-ID"))[:255],
            "subject": self._decode_header(msg.get("Subject"))[:255],
            "from_addr": self._decode_header(msg.get("From"))[:255],
            "email_date": sent or fields.Datetime.now(),
        }
        stored = 0
        for filename, raw in self._attachments(msg):
            sha256 = self._sha256(raw)
            vals = dict(base, filename=filename, sha256=sha256, size=len(raw))
            try:
                backup_path = os.path.join(
                    self.backup_dir,
                    (sent or fields.Datetime.now()).strftime("%Y/%m"),
                    self._suffixed_name(filename, sent, sha256),
                )
                self._atomic_write(backup_path, raw)
                if not self._verify_backup(backup_path, sha256):
                    raise UserError(_("Backup of %(f)s did not verify against its SHA256.") % {"f": filename})
                vals.update(backup_path=backup_path, state="backed_up")

                if self.drop_dir and self._matches_ingest(filename):
                    if Log.find_duplicate(sha256):
                        vals["state"] = "skipped"
                    else:
                        staged = os.path.join(self.drop_dir, self._suffixed_name(filename, sent))
                        self._atomic_write(staged, raw)
                        vals.update(staged_path=staged, state="staged")
                stored += 1
            except Exception as e:  # noqa: BLE001 - one bad attachment must not stop the rest
                _logger.exception("Mailbox %s: UID %s attachment %s failed", self.name, uid, filename)
                vals.update(state="error", error=str(e))
            self._upsert_row(Message, vals)
        if not stored and not Message.search_count([("mailbox_id", "=", self.id), ("uid", "=", uid)]):
            # No matching attachment at all: record the message so we never refetch it,
            # and so retention can eventually clear it.
            self._upsert_row(Message, dict(base, filename="", sha256="", size=0, state="skipped"))
        return stored

    def _upsert_row(self, Message, vals):
        """Write-or-create on the ledger's unique key.

        A row left in ``error`` is not in the "known" set, so the next run refetches that
        UID; a plain create would then trip the unique constraint instead of retrying.
        """
        existing = Message.search(
            [
                ("mailbox_id", "=", vals["mailbox_id"]),
                ("uidvalidity", "=", vals["uidvalidity"]),
                ("uid", "=", vals["uid"]),
                ("filename", "=", vals["filename"]),
            ],
            limit=1,
        )
        if existing:
            existing.write(dict(vals, error=vals.get("error", False)))
            return existing
        return Message.create(vals)

    def _update_gauges(self, client, uids):
        self.ensure_one()
        total = 0
        if uids:
            typ, data = client.uid("FETCH", ",".join(str(u) for u in uids), "(RFC822.SIZE)")
            if typ == "OK":
                for item in data or []:
                    blob = item[0] if isinstance(item, tuple) else item
                    match = re.search(rb"RFC822\.SIZE (\d+)", blob or b"")
                    if match:
                        total += int(match.group(1))
        self.write({"msg_count": len(uids), "mailbox_bytes": total / (1024.0 * 1024.0)})

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------
    def _purge_candidates(self):
        """``[(email_date, uid, rows)]`` for messages safe to delete, oldest first.

        "Safe" means every ledger row of the message re-reads off disk with the SHA256 we
        recorded. Import state is deliberately not consulted — see the module docstring.
        Two gates open the door: age past ``retention_days``, or an emergency drain once
        the mailbox crosses ``max_messages`` / ``max_bytes_mb``.
        """
        self.ensure_one()
        Message = self.env["retail.import.mail.message"].sudo()
        rows = Message.search(
            [("mailbox_id", "=", self.id), ("uidvalidity", "=", self.uidvalidity), ("uid", "!=", 0)],
            order="email_date asc, uid asc",
        )
        by_uid = {}
        for row in rows:
            by_uid[row.uid] = by_uid.get(row.uid, Message) | row

        safe = []  # [(email_date, uid, recordset)], oldest first
        for uid, group in by_uid.items():
            states = set(group.mapped("state"))
            if states & {"error", "purged"} or not states <= {"backed_up", "staged", "skipped"}:
                continue
            if not all(not r.sha256 or self._verify_backup(r.backup_path, r.sha256) for r in group):
                _logger.warning(
                    "Mailbox %s: UID %s has an unverifiable backup; refusing to purge.",
                    self.name,
                    uid,
                )
                continue
            safe.append((min(group.mapped("email_date")), uid, group))
        safe.sort(key=lambda t: (t[0], t[1]))

        cutoff = fields.Datetime.now() - timedelta(days=self.retention_days)
        aged = [t for t in safe if t[0] < cutoff]

        over_count = self.max_messages and self.msg_count > self.max_messages
        over_bytes = self.max_bytes_mb and self.mailbox_bytes > self.max_bytes_mb
        if over_count or over_bytes:
            keep = self.max_messages or 0
            drain = safe[: max(len(safe) - keep, 0)] if keep else safe
            aged_uids = {uid for _d, uid, _r in aged}
            extra = [t for t in drain if t[1] not in aged_uids]
            if extra:
                _logger.warning(
                    "Mailbox %s: emergency drain — %s message(s) younger than retention_days=%s "
                    "will be deleted because msg_count=%s (max %s) / size=%.1f MB (max %s).",
                    self.name,
                    len(extra),
                    self.retention_days,
                    self.msg_count,
                    self.max_messages,
                    self.mailbox_bytes,
                    self.max_bytes_mb,
                )
                aged = sorted(aged + extra, key=lambda t: (t[0], t[1]))
        return aged

    def _purge(self):
        """Delete backed-up messages past retention. Returns the number purged."""
        self.ensure_one()
        if not self.purge_enabled:
            return 0
        candidates = self._purge_candidates()
        if not candidates:
            return 0
        if self.dry_run:
            _logger.info("Mailbox %s: dry run — %s message(s) would be purged.", self.name, len(candidates))
            self.last_message = _("Dry run: %(n)s message(s) eligible for purge (UIDs %(uids)s).") % {
                "n": len(candidates),
                "uids": ", ".join(str(uid) for _d, uid, _r in candidates[:20]),
            }
            return 0

        client = self._connect()
        purged = 0
        try:
            # UIDPLUS lets us expunge exactly the UIDs we verified. Without it, EXPUNGE
            # sweeps every \Deleted message in the folder — still correct here because we
            # only ever flag verified candidates, but it is coarse, so we flag first and
            # expunge once at the end.
            has_uidplus = "UIDPLUS" in (client.capabilities or ())
            for _date, uid, rows in candidates:
                try:
                    client.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
                    if has_uidplus:
                        client.uid("EXPUNGE", str(uid))
                        if not self._gone(client, uid):
                            raise UserError(_("UID %s still present after EXPUNGE.") % uid)
                        rows.write({"state": "purged"})
                        purged += 1
                except Exception as e:  # noqa: BLE001 - one bad UID must not stop the rest
                    _logger.exception("Mailbox %s: purge of UID %s failed", self.name, uid)
                    rows.write({"state": "error", "error": str(e)})
            if not has_uidplus:
                client.expunge()
                for _date, uid, rows in candidates:
                    if self._gone(client, uid):
                        rows.write({"state": "purged"})
                        purged += 1
            # The server state is already gone; make sure the ledger says so too.
            self._checkpoint()
        finally:
            self._safe_logout(client)
        return purged

    @staticmethod
    def _gone(client, uid) -> bool:
        typ, data = client.uid("SEARCH", None, "UID", str(uid))
        return typ == "OK" and not (data[0] or b"").split()

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------
    def action_fetch_now(self):
        for box in self:
            box._run(purge=False)
        return True

    def action_purge_now(self):
        for box in self:
            box._run(fetch=False)
        return True

    def _run(self, fetch=True, purge=True):
        self.ensure_one()
        if not self._lock():
            return
        stored = purged = 0
        try:
            if fetch:
                stored = self._fetch()
            if purge:
                purged = self._purge()
        except Exception as e:  # noqa: BLE001 - surface on the record, never crash the cron
            _logger.exception("Mailbox %s run failed", self.name)
            self.write({"last_run": fields.Datetime.now(), "last_status": "error", "last_message": str(e)})
            return
        finally:
            self._unlock()
        message = _("Stored %(s)s attachment(s); purged %(p)s message(s).") % {
            "s": stored,
            "p": purged,
        }
        if self.purge_enabled and self.dry_run and self.last_message:
            message = "%s %s" % (message, self.last_message)
        self.write({"last_run": fields.Datetime.now(), "last_status": "ok", "last_message": message})

    def _cron_fetch_mailboxes(self):
        """ir.cron entry point: fetch then purge every active mailbox."""
        for box in self.search([("active", "=", True)]):
            box._run()
        return True


class RetailImportMailMessage(models.Model):
    _name = "retail.import.mail.message"
    _description = "Retail Import Fetched Mail Attachment"
    _order = "email_date desc, uid desc, id desc"

    mailbox_id = fields.Many2one("retail.import.mailbox", required=True, ondelete="cascade", index=True)
    uid = fields.Integer(required=True, index=True, help="IMAP UID; 0 once UIDVALIDITY changed.")
    uidvalidity = fields.Char(index=True)
    message_id = fields.Char(index=True)
    subject = fields.Char()
    from_addr = fields.Char(string="From")
    email_date = fields.Datetime(index=True)

    filename = fields.Char()
    sha256 = fields.Char(index=True)
    size = fields.Integer(help="Attachment size in bytes.")
    backup_path = fields.Char(help="Durable copy on the SFTP share.")
    staged_path = fields.Char(help="Copy handed to retail.import.feed's drop directory.")
    state = fields.Selection(
        [
            ("backed_up", "Backed up"),
            ("staged", "Staged for import"),
            ("skipped", "Skipped"),
            ("purged", "Purged from server"),
            ("error", "Error"),
        ],
        required=True,
        default="backed_up",
        index=True,
    )
    error = fields.Text()

    # Odoo 19 silently ignores _sql_constraints — use models.Constraint.
    _uid_filename_uniq = models.Constraint(
        "unique(mailbox_id, uidvalidity, uid, filename)",
        "One ledger row per attachment per IMAP message.",
    )
