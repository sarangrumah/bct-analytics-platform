# -*- coding: utf-8 -*-
"""Retail import feed — recurring pull of source files into the importer.

Phase 6 of the apparel brand onboarding plan: answers "can Odoo read directly from the
customer FTP?". A feed binds a source location + glob to a ``retail.import.profile``.
An ``ir.cron`` polls active feeds; each new file (deduplicated by SHA256 against
``retail.import.log``) is stored in ir.attachment and handed to the same
``retail.import.executor`` used by the manual wizard.

Two source types:

* ``sftp`` — Odoo pulls from a remote customer SFTP server (paramiko).
* ``local`` — Odoo reads a local directory that a partner fills over the bundled
  FTPS server (``stilliard/pure-ftpd``). Both containers share the
  ``./data/data_retail`` host volume, so ingestion is a plain filesystem read and
  the archive step is an atomic local rename. After a file is processed it is moved
  from ``local_dir`` to ``archive_dir`` with a ``YYYYMMDD_HHMMSS_`` prefix (the
  processing timestamp). Failed files are left in place for the next poll to retry.

Credentials (SFTP only): the secret lives in ``ir.config_parameter`` under the key
named in ``password_param`` (or a private-key file path). NOTE: the platform does
not yet expose an at-rest decryptor for config params, so the value is stored raw —
restrict read access to the parameter and prefer key-based auth where possible.

Networking: SFTP egress goes out from the Odoo container on ``odoo-net``; the local
feed needs no egress — it relies on the shared ``./data/data_retail`` bind mount.
"""

from __future__ import annotations

import base64
import fnmatch
import io
import logging
import os
import shutil

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class RetailImportFeed(models.Model):
    _name = "retail.import.feed"
    _description = "Retail Import SFTP Feed"
    _order = "sequence, name"
    _inherit = ["mail.thread"]

    name = fields.Char(required=True, index=True)
    sequence = fields.Integer(
        default=50,
        help="Poll order. Matters once posting is enabled: X24 creates the POS suspense "
        "entries that X70D reconciles against, so X24 must run first.",
    )
    active = fields.Boolean(default=True)
    profile_id = fields.Many2one("retail.import.profile", required=True, ondelete="restrict")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)

    source_type = fields.Selection(
        [("sftp", "SFTP pull"), ("local", "Local directory (FTPS drop)")],
        default="sftp",
        required=True,
        help="SFTP: Odoo connects out to a remote server. "
        "Local: Odoo reads a shared directory filled over the bundled FTPS server.",
    )

    host = fields.Char()
    port = fields.Integer(default=22)
    username = fields.Char()
    auth_type = fields.Selection(
        [("password", "Password"), ("key", "Private key file")], default="password", required=True
    )
    password_param = fields.Char(
        string="Password ir.config_parameter Key",
        help="Key in ir.config_parameter that holds the SFTP password.",
    )
    private_key_path = fields.Char(help="Path (inside the container) to the SFTP private key file.")

    remote_dir = fields.Char(default="/")

    # Local-directory (FTPS drop) mode -----------------------------------------
    local_dir = fields.Char(help="Directory inside the container to poll, e.g. /mnt/data_retail/data.")
    archive_dir = fields.Char(
        help="Where processed files are moved (with a YYYYMMDD_HHMMSS_ prefix), e.g. /mnt/data_retail/archive."
    )

    file_glob = fields.Char(default="*", required=True, help="e.g. 'X20_*.csv' or 'X24DN_*.xlsx'.")
    run_async = fields.Boolean(string="Process asynchronously", default=True, help="Hand each file to queue_job.")

    last_run = fields.Datetime(readonly=True)
    last_status = fields.Selection([("ok", "OK"), ("error", "Error"), ("idle", "Idle")], default="idle", readonly=True)
    last_message = fields.Text(readonly=True)
    files_imported = fields.Integer(default=0, readonly=True)

    # ------------------------------------------------------------------
    @api.constrains("source_type", "host", "username", "local_dir", "archive_dir")
    def _check_source_config(self):
        for feed in self:
            if feed.source_type == "sftp":
                missing = [f for f in ("host", "username") if not feed[f]]
                if missing:
                    raise ValidationError(_("Feed %s: SFTP source requires %s.") % (feed.name, ", ".join(missing)))
            elif feed.source_type == "local":
                missing = [f for f in ("local_dir", "archive_dir") if not feed[f]]
                if missing:
                    raise ValidationError(_("Feed %s: local source requires %s.") % (feed.name, ", ".join(missing)))

    # ------------------------------------------------------------------
    def _secret(self):
        self.ensure_one()
        if not self.password_param:
            return ""
        return self.env["ir.config_parameter"].sudo().get_param(self.password_param, "") or ""

    def _open_sftp(self):
        self.ensure_one()
        try:
            import paramiko  # noqa: PLC0415 - optional, image-provided
        except ImportError as e:  # pragma: no cover - depends on image
            raise UserError(
                _("paramiko is not installed in this Odoo image. Add it to odoo/requirements.txt and rebuild.")
            ) from e
        transport = paramiko.Transport((self.host, self.port))
        try:
            if self.auth_type == "key":
                if not self.private_key_path:
                    raise UserError(_("Feed %s: private key path is required for key auth.") % self.name)
                pkey = paramiko.RSAKey.from_private_key_file(self.private_key_path)
                transport.connect(username=self.username, pkey=pkey)
            else:
                transport.connect(username=self.username, password=self._secret())
        except Exception:
            transport.close()
            raise
        return paramiko.SFTPClient.from_transport(transport), transport

    def action_test_connection(self):
        self.ensure_one()
        if self.source_type == "local":
            if not self.local_dir or not os.path.isdir(self.local_dir):
                raise UserError(
                    _("Feed %s: local_dir %s does not exist or is not a directory.") % (self.name, self.local_dir or "")
                )
            names = os.listdir(self.local_dir)
            where = self.local_dir
        else:
            sftp, transport = self._open_sftp()
            try:
                names = sftp.listdir(self.remote_dir)
            finally:
                sftp.close()
                transport.close()
            where = self.remote_dir
        matched = [n for n in names if fnmatch.fnmatch(n, self.file_glob)]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Connection OK"),
                "message": _("%s file(s) match %s in %s.") % (len(matched), self.file_glob, where),
                "type": "success",
                "sticky": False,
            },
        }

    # ------------------------------------------------------------------
    def _poll_one(self):
        """Dispatch to the source-specific poller. Returns the imported count."""
        self.ensure_one()
        if self.source_type == "local":
            return self._poll_one_local()
        return self._poll_one_sftp()

    # Local timezone used for the archive-filename prefix. Files are stamped
    # with WIB (Asia/Jakarta) wall-clock time so the prefix matches what the
    # the apparel brand operations team sees, regardless of the server's UTC clock.
    _ARCHIVE_TZ = "Asia/Jakarta"

    def _archive_file(self, src_path, filename):
        """Move a processed source file into ``archive_dir`` with a timestamp prefix.

        Prefix is the processing wall-clock time in WIB (Asia/Jakarta) as
        ``YYYYMMDDHHMMSS_``. A numeric suffix guards against same-second name
        collisions. Returns the destination path.
        """
        self.ensure_one()
        os.makedirs(self.archive_dir, exist_ok=True)
        wib = pytz.utc.localize(fields.Datetime.now()).astimezone(pytz.timezone(self._ARCHIVE_TZ))
        ts = wib.strftime("%Y%m%d%H%M%S")
        dest = os.path.join(self.archive_dir, "%s_%s" % (ts, filename))
        base, ext = os.path.splitext(dest)
        i = 1
        while os.path.exists(dest):
            dest = "%s_%d%s" % (base, i, ext)
            i += 1
        shutil.move(src_path, dest)
        return dest

    def _poll_one_local(self):
        """Read matching files from ``local_dir``, process them, archive on success.

        Runs the executor synchronously (ignoring ``run_async``) so the poll loop
        knows each file's outcome before deciding to archive — this is what makes
        "processed -> archived" exact. The ``started_at``/``finished_at`` fields on
        ``retail.import.log`` record the precise processing window. Failed files are
        left in ``local_dir`` for the next poll to retry (surfaced via last_status).
        """
        self.ensure_one()
        Log = self.env["retail.import.log"].sudo()
        Executor = self.env["retail.import.executor"]
        if not self.local_dir or not os.path.isdir(self.local_dir):
            raise UserError(_("Feed %s: local_dir %s does not exist.") % (self.name, self.local_dir or ""))
        imported = 0
        for name in sorted(os.listdir(self.local_dir)):
            if not fnmatch.fnmatch(name, self.file_glob):
                continue
            src = os.path.join(self.local_dir, name)
            if not os.path.isfile(src):
                continue
            with open(src, "rb") as fh:
                raw = fh.read()
            if not raw:
                continue
            file_hash = Log.compute_hash(raw)
            if Log.find_duplicate(file_hash):
                self._archive_file(src, name)  # already imported earlier; clear it from the drop
                continue
            file_b64 = base64.b64encode(raw).decode("ascii")
            log = Log.create(
                {
                    "profile_id": self.profile_id.id,
                    "filename": name,
                    "file_hash": file_hash,
                    "state": "queued",
                }
            )
            log.store_source(file_b64, name)  # bytes safe in ir.attachment before we touch the file
            Executor.run(log)
            if log.exists() and log.state in ("imported", "partial"):
                self._archive_file(src, name)
                imported += 1
            # state == "failed": leave in local_dir for retry; surfaced via last_status/message
        return imported

    def _poll_one_sftp(self):
        """Download new matching files and feed them to the executor. Returns count."""
        self.ensure_one()
        Log = self.env["retail.import.log"].sudo()
        Executor = self.env["retail.import.executor"]
        sftp, transport = self._open_sftp()
        imported = 0
        try:
            names = [n for n in sftp.listdir(self.remote_dir) if fnmatch.fnmatch(n, self.file_glob)]
            for name in sorted(names):
                remote_path = self.remote_dir.rstrip("/") + "/" + name
                buf = io.BytesIO()
                sftp.getfo(remote_path, buf)
                raw = buf.getvalue()
                if not raw:
                    continue
                file_hash = Log.compute_hash(raw)
                if Log.find_duplicate(file_hash):
                    continue  # already seen this exact file
                file_b64 = base64.b64encode(raw).decode("ascii")
                log = Log.create(
                    {
                        "profile_id": self.profile_id.id,
                        "filename": name,
                        "file_hash": file_hash,
                        "state": "queued",
                    }
                )
                log.store_source(file_b64, name)
                if self.run_async:
                    try:
                        job = Executor.with_delay(
                            channel="root.retail_import",
                            description=f"Feed {self.name}: {name}",
                        ).run(log)
                        log.job_uuid = getattr(job, "uuid", False)
                    except Exception:
                        Executor.run(log)
                else:
                    Executor.run(log)
                imported += 1
        finally:
            sftp.close()
            transport.close()
        return imported

    def action_poll_now(self):
        for feed in self:
            feed._run_feed()
        return True

    def _run_feed(self):
        self.ensure_one()
        try:
            n = self._poll_one()
            self.write(
                {
                    "last_run": fields.Datetime.now(),
                    "last_status": "ok",
                    "last_message": _("Imported %s new file(s).") % n,
                    "files_imported": self.files_imported + n,
                }
            )
        except Exception as e:
            _logger.exception("Feed %s poll failed", self.name)
            self.write(
                {
                    "last_run": fields.Datetime.now(),
                    "last_status": "error",
                    "last_message": str(e),
                }
            )

    def _cron_poll_feeds(self):
        """ir.cron entry point: poll every active feed, in ``sequence`` order."""
        for feed in self.search([("active", "=", True)], order="sequence, name"):
            feed._run_feed()
        return True
