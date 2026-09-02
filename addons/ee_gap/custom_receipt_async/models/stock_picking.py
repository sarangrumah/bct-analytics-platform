import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

ASYNC_LINE_THRESHOLD = 500


class StockPicking(models.Model):
    _inherit = "stock.picking"

    async_validate_job_uuid = fields.Char(
        string="Async Validate Job",
        copy=False,
        readonly=True,
        help="UUID of the queue.job currently validating this picking, if any.",
    )

    move_line_count = fields.Integer(
        compute="_compute_move_line_count",
        string="Move Lines",
    )

    @api.depends("move_line_ids")
    def _compute_move_line_count(self):
        # Group-by counts in one query instead of per-record len().
        if not self.ids:
            for rec in self:
                rec.move_line_count = 0
            return
        groups = self.env["stock.move.line"]._read_group(
            domain=[("picking_id", "in", self.ids)],
            groupby=["picking_id"],
            aggregates=["__count"],
        )
        counts = {p.id: c for p, c in groups}
        for rec in self:
            rec.move_line_count = counts.get(rec.id, 0)

    def action_validate_async(self):
        """Enqueue button_validate as a queue.job. Returns a notification action."""
        self.ensure_one()
        if self.state in ("done", "cancel"):
            raise UserError(_("Picking %s is already %s.") % (self.name, self.state))
        if self.async_validate_job_uuid:
            existing = self.env["queue.job"].search([("uuid", "=", self.async_validate_job_uuid)], limit=1)
            if existing and existing.state in ("pending", "enqueued", "started"):
                raise UserError(
                    _("A background validate job is already %s for %s (uuid=%s).")
                    % (existing.state, self.name, existing.uuid)
                )

        delayed = self.with_delay(
            channel="root.stock_receipt",
            description=_("Validate %s (%d move lines)") % (self.name, self.move_line_count),
        )._job_validate_picking()

        self.async_validate_job_uuid = delayed.uuid
        self.message_post(
            body=_(
                "Background validate enqueued (job uuid: <code>%s</code>). "
                "You will be notified in the chatter when it finishes."
            )
            % delayed.uuid,
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "info",
                "title": _("Validate Enqueued"),
                "message": _("Job %s queued. Refresh later or watch the chatter.") % delayed.uuid,
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }

    def _job_validate_picking(self):
        """Queue job entry point. Runs button_validate with context that
        disables tracking overhead and commits on success."""
        self.ensure_one()
        t0 = time.time()
        picking = self.with_context(
            tracking_disable=True,
            mail_notrack=True,
            mail_create_nolog=True,
            skip_immediate=True,
            skip_backorder=True,
            queue_job_async_validate=True,
        )
        try:
            result = picking.button_validate()
        except Exception as e:
            elapsed = time.time() - t0
            _logger.exception(
                "custom_receipt_async: button_validate failed for %s after %.1fs",
                self.name,
                elapsed,
            )
            # Re-raise so queue_job marks the job as failed (retryable per its config).
            # Also post to chatter so the user sees the failure without opening queue jobs.
            self.message_post(
                body=_("Background validate FAILED after %.1fs: <code>%s</code>") % (elapsed, e),
            )
            raise

        elapsed = time.time() - t0
        self.message_post(
            body=_("Background validate completed in %.1fs. State: %s.") % (elapsed, self.state),
        )
        _logger.info(
            "custom_receipt_async: validated %s in %.1fs (%d move lines)",
            self.name,
            elapsed,
            self.move_line_count,
        )
        return result
