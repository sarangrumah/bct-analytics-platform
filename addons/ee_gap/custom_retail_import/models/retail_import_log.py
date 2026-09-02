# -*- coding: utf-8 -*-
"""Retail import log — audit trail with SHA256 file-hash dedup.

Generalizes ``custom.bank.import.log``. Unlike the bank log, retail imports keep
the source file in ``ir.attachment`` (audit + reprocessing) and may run async via
queue_job, so we track ``job_uuid`` and per-stage record counters.
"""

from __future__ import annotations

import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class RetailImportLog(models.Model):
    _name = "retail.import.log"
    _description = "Retail Import Log"
    _order = "imported_at desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(compute="_compute_name", store=True)
    profile_id = fields.Many2one("retail.import.profile", required=True, ondelete="restrict")
    file_type = fields.Selection(related="profile_id.file_type", store=True, index=True)
    filename = fields.Char()
    file_hash = fields.Char(index=True, help="sha256 of raw bytes, for dedup.")
    attachment_id = fields.Many2one(
        "ir.attachment", ondelete="set null", help="Stored source file for audit / reprocessing."
    )
    job_uuid = fields.Char(index=True, help="queue_job UUID when processed asynchronously.")

    line_count = fields.Integer(default=0, help="Data rows read from the source.")
    records_created = fields.Integer(default=0)
    records_matched = fields.Integer(default=0)
    records_skipped = fields.Integer(default=0)
    error_count = fields.Integer(default=0)

    imported_at = fields.Datetime(default=fields.Datetime.now, help="When the file was submitted.")
    started_at = fields.Datetime(help="When execution began.")
    finished_at = fields.Datetime(help="When execution ended (success or failure).")
    duration = fields.Float(
        compute="_compute_duration",
        # Deliberately NOT stored. This addon is shared by every tenant database, so a
        # stored compute means a new column and a mandatory -u everywhere before any of
        # them can even open the list. The value is display-only; the cost of not
        # storing it is that you cannot sort or filter on it.
        help="Minutes between started_at and finished_at; counts up while running.",
    )
    imported_by_id = fields.Many2one("res.users", default=lambda s: s.env.user, readonly=True)
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("running", "Running"),
            ("imported", "Imported"),
            ("partial", "Partial"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="queued",
        required=True,
        index=True,
        tracking=True,
    )
    error_message = fields.Text()
    raw_payload = fields.Text(help="Row-level error summary (first N rows).")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True)
    line_ids = fields.One2many("retail.import.line", "log_id", string="Source Rows")

    result_summary = fields.Text(
        compute="_compute_result_summary",
        help="Human-readable outcome: counters, throughput and the error breakdown.",
    )

    def _compute_name(self):
        for rec in self:
            rec.name = f"RIL/{rec.id or '?'}/{rec.filename or (rec.profile_id.code or 'import')}"

    @api.depends("started_at", "finished_at")
    def _compute_duration(self):
        now = fields.Datetime.now()
        for rec in self:
            if not rec.started_at:
                rec.duration = 0.0
            else:
                # A running import has no finished_at yet; counting up to "now" is what
                # makes the list answer "is it still going?" without reading the logs.
                end = rec.finished_at or now
                rec.duration = (end - rec.started_at).total_seconds() / 60.0

    @api.depends("state", "line_count", "records_created", "records_matched", "records_skipped", "error_count")
    def _compute_result_summary(self):
        """Put the numbers you would otherwise dig out of SQL on the form."""
        for rec in self:
            if rec.state == "queued":
                rec.result_summary = _("Queued — not started yet.")
                continue
            bits = [
                _("%(rows)s source rows", rows=f"{rec.line_count:,}"),
                _("%(n)s created", n=f"{rec.records_created:,}"),
                _("%(n)s matched", n=f"{rec.records_matched:,}"),
                _("%(n)s skipped", n=f"{rec.records_skipped:,}"),
            ]
            if rec.duration:
                bits.append(_("%(m).1f min", m=rec.duration))
                if rec.line_count and rec.duration > 0:
                    bits.append(_("%(r)s rows/min", r=f"{int(rec.line_count / rec.duration):,}"))
            lines = [" · ".join(bits)]
            if rec.error_count:
                lines.append("")
                lines.append(_("%(n)s rows flagged:", n=f"{rec.error_count:,}"))
                lines.extend(f"  • {msg} — {n:,}" for msg, n in rec._error_breakdown())
            rec.result_summary = "\n".join(lines)

    def _error_breakdown(self):
        """[(message, count)] for this log's error rows, most frequent first."""
        self.ensure_one()
        if not self.id:
            return []
        groups = self.env["retail.import.line"]._read_group(
            [("log_id", "=", self.id), ("state", "=", "error")],
            groupby=["error_message"],
            aggregates=["__count"],
            order="__count desc",
            limit=10,
        )
        return [(msg or _("(no message)"), count) for msg, count in groups]

    @staticmethod
    def compute_hash(raw_bytes: bytes) -> str:
        return hashlib.sha256(raw_bytes).hexdigest()

    def find_duplicate(self, file_hash):
        """Return an existing successful log for this hash, if any (dedup guard)."""
        return self.search(
            [("file_hash", "=", file_hash), ("state", "in", ("imported", "partial", "running"))],
            limit=1,
        )

    def store_source(self, file_b64, filename):
        """Persist the uploaded file as an ir.attachment for audit."""
        self.ensure_one()
        att = self.env["ir.attachment"].create(
            {
                "name": filename or self.name,
                "datas": file_b64,
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "application/octet-stream",
            }
        )
        self.attachment_id = att.id
        return att

    def set_errors(self, errors):
        """errors: list of (row, message). Stores a capped summary."""
        self.ensure_one()
        self.error_count = len(errors)
        if errors:
            self.raw_payload = "\n".join(f"row {n}: {m}" for n, m in errors[:200])

    def source_b64(self):
        """Return the stored source file as base64 (for reprocessing / async jobs)."""
        self.ensure_one()
        if self.attachment_id and self.attachment_id.datas:
            return self.attachment_id.datas
        return False

    def action_cancel(self):
        """Cancel a queued import and close its queue job.

        Only ``queued`` is cancellable. A ``running`` import is already inside a
        worker or an ``odoo shell``, and flipping the row does not stop that process
        -- it keeps writing until it finishes or is killed, which is exactly how two
        loaders once ended up racing on the same database. Stopping one of those is a
        deliberate operation, not a button.
        """
        for rec in self:
            if rec.state != "queued":
                raise UserError(
                    _(
                        "Only a queued import can be cancelled. %(name)s is %(state)s.",
                        name=rec.name,
                        state=rec.state,
                    )
                )
            rec.write(
                {
                    "state": "cancelled",
                    "finished_at": fields.Datetime.now(),
                    "error_message": _("Cancelled by %(user)s.", user=self.env.user.name),
                }
            )
            if rec.job_uuid:
                # Take the job out of the runner's queue; it never started, so there is
                # nothing to roll back.
                self.env.cr.execute(
                    "UPDATE queue_job SET state='done', date_done=(now() at time zone 'utc') "
                    "WHERE uuid=%s AND state IN ('pending','enqueued')",
                    (rec.job_uuid,),
                )
        return True

    def action_sync_feeds(self):
        """Poll every active FTP/SFTP feed once (one-click 'Sync from FTP')."""
        feeds = self.env["retail.import.feed"].search([("active", "=", True)])
        if not feeds:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("No feeds"),
                    "message": _("No active feeds. Add one under Configuration → Import Feeds."),
                    "type": "warning",
                    "sticky": False,
                },
            }
        for feed in feeds:
            feed._run_feed()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Sync complete"),
                "message": _("Synced %(n)s feed(s) from FTP.") % {"n": len(feeds)},
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": "retail.import.log",
                    "view_mode": "list,form",
                    "views": [(False, "list"), (False, "form")],
                    "target": "current",
                    "name": _("Imports"),
                },
            },
        }
