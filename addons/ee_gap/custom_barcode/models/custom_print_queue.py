# -*- coding: utf-8 -*-
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class CustomPrintQueue(models.Model):
    """Async print-job spool. A cron picks queued jobs and dispatches them
    through the configured `custom.printer.config`."""

    _name = "custom.print.queue"
    _description = "Print Queue"
    _order = "create_date desc, id desc"

    name = fields.Char(default="Print Job", required=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user)
    label_template_id = fields.Many2one("custom.label.template", required=True)
    printer_id = fields.Many2one("custom.printer.config", required=True)
    res_model = fields.Char(required=True)
    res_ids = fields.Text(help="Comma-separated record IDs to render.")
    copies = fields.Integer(default=1)
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("printing", "Printing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    sent_at = fields.Datetime(readonly=True)
    error = fields.Text(readonly=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )

    def _ids_iter(self):
        self.ensure_one()
        raw = (self.res_ids or "").strip()
        if not raw:
            return []
        out = []
        for chunk in raw.split(","):
            chunk = chunk.strip()
            if chunk.isdigit():
                out.append(int(chunk))
        return out

    def action_process(self):
        """Render + send synchronously."""
        for job in self:
            try:
                job.write({"state": "printing"})
                model = job.env[job.res_model]
                rec_ids = job._ids_iter()
                if not rec_ids:
                    raise ValueError("No record IDs provided.")
                records = model.browse(rec_ids).exists()
                payload = b""
                for rec in records:
                    payload += job.label_template_id.render(rec, qty=max(1, job.copies or 1))
                    payload += b"\n"
                job.printer_id.send_raw(payload)
                job.write({"state": "done", "sent_at": fields.Datetime.now(), "error": False})
            except Exception as e:
                _logger.exception("Print job %s failed", job.name)
                job.write({"state": "failed", "error": str(e)})
        return True

    @api.model
    def _cron_process_queue(self):
        jobs = self.search([("state", "=", "queued")], limit=50)
        if jobs:
            jobs.action_process()
        return True
