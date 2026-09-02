# -*- coding: utf-8 -*-
import logging
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Standard working day length used for the simple overtime computation.
STANDARD_DAILY_HOURS = 8.0


class AccountAnalyticLine(models.Model):
    _name = "account.analytic.line"
    _inherit = ["account.analytic.line", "mail.thread", "approval.mixin"]

    x_billable = fields.Boolean(
        string="Billable",
        default=False,
        tracking=True,
        help="If checked, this timesheet line can be invoiced to the customer.",
    )
    x_billing_currency_id = fields.Many2one(
        "res.currency",
        string="Billing Currency",
        default=lambda self: self.env.company.currency_id,
    )
    x_billing_rate = fields.Monetary(
        string="Billing Rate",
        currency_field="x_billing_currency_id",
        help="Hourly billing rate applied when this line is invoiced.",
    )
    x_overtime_hours = fields.Float(
        string="Overtime Hours",
        compute="_compute_overtime_hours",
        store=True,
        help="Hours above the standard daily threshold (currently %s h/day)." % STANDARD_DAILY_HOURS,
    )
    x_billed_invoice_line_id = fields.Many2one(
        "account.move.line",
        string="Billed Invoice Line",
        readonly=True,
        copy=False,
        help="Invoice line that already billed this timesheet entry.",
    )
    x_overtime_work_entry_id = fields.Many2one(
        "hr.work.entry",
        string="Overtime Work Entry",
        readonly=True,
        copy=False,
        help="Generated work entry feeding payroll overtime computations.",
    )
    x_validation_state = fields.Selection(
        [
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("validated", "Validated"),
        ],
        string="Validation",
        default="draft",
        tracking=True,
        copy=False,
        help="Only 'Validated' lines may be invoiced or fed to payroll.",
    )

    @api.depends("unit_amount")
    def _compute_overtime_hours(self):
        for line in self:
            hours = line.unit_amount or 0.0
            line.x_overtime_hours = max(0.0, hours - STANDARD_DAILY_HOURS)

    # ------------------------------------------------------------------
    # Validation workflow
    # ------------------------------------------------------------------

    def action_submit_validation(self):
        for line in self:
            if line.x_validation_state != "draft":
                raise UserError(_("Only draft timesheet lines can be submitted."))
            line.x_validation_state = "submitted"
            # Auto-submit approval when a matrix matches (line stays "submitted"
            # until the engine grants it, then _approval_on_granted validates).
            # When no matrix matches the helper returns True → validate now.
            if line._approval_request_or_proceed():
                line.x_validation_state = "validated"
        return True

    def action_validate(self):
        for line in self:
            if line.x_validation_state == "validated":
                continue
            # If approval matrix is in place, gate through the mixin.
            try:
                line._approval_check_required()
            except UserError:
                raise
            line.x_validation_state = "validated"
        return True

    def _approval_on_granted(self):
        """Engine hook: validate the line once all tiers approve."""
        return self.action_validate()

    def action_reset_to_draft(self):
        for line in self:
            if line.x_billed_invoice_line_id:
                raise UserError(_("Cannot reset a timesheet already invoiced (line %s).") % line.id)
            line.x_validation_state = "draft"
        return True

    # ------------------------------------------------------------------
    # Overtime -> hr.work.entry
    # ------------------------------------------------------------------

    def _ensure_overtime_work_entry_type(self):
        WET = self.env["hr.work.entry.type"].sudo()
        wet = WET.search([("code", "=", "OT")], limit=1)
        if not wet:
            wet = WET.create(
                {
                    "name": "Overtime",
                    "code": "OT",
                    "display_code": "OT",
                }
            )
        return wet

    def action_create_overtime_work_entry(self):
        """Create an hr.work.entry for overtime hours on this timesheet.

        Returns the created/updated work entry record (or False if nothing
        to do). Reverses previous entry if re-run.
        """
        self.ensure_one()
        if self.x_overtime_hours <= 0.0:
            if hasattr(self, "message_post"):
                self.message_post(body=_("No overtime hours to convert into a work entry."))
            return False
        if not self.employee_id:
            raise UserError(_("Cannot create an overtime work entry without an employee."))
        if self.x_validation_state != "validated":
            raise UserError(_("Only validated timesheets can be fed to payroll. Submit/validate the entry first."))

        WorkEntry = self.env["hr.work.entry"].sudo()
        wet = self._ensure_overtime_work_entry_type()

        # Reverse existing one (idempotency).
        if self.x_overtime_work_entry_id:
            self.x_overtime_work_entry_id.state = "cancelled"

        base_date = self.date or fields.Date.context_today(self)
        date_start = datetime.combine(base_date, datetime.min.time()).replace(hour=17)
        date_stop = date_start + timedelta(hours=self.x_overtime_hours)
        vals = {
            "name": _("Overtime %s") % (self.employee_id.name or ""),
            "employee_id": self.employee_id.id,
            "date": base_date,
            "date_start": date_start,
            "date_stop": date_stop,
            "duration": self.x_overtime_hours,
            "work_entry_type_id": wet.id,
            "state": "draft",
        }
        # Optional link back so the source line is discoverable from payroll.
        if "x_source_timesheet_id" in WorkEntry._fields:
            vals["x_source_timesheet_id"] = self.id
        work_entry = WorkEntry.create(vals)
        self.x_overtime_work_entry_id = work_entry.id
        if hasattr(self, "message_post"):
            self.message_post(
                body=_("Created overtime work entry <b>%(name)s</b> (%(hours).2f h).")
                % {"name": work_entry.display_name or work_entry.name, "hours": self.x_overtime_hours}
            )
        return work_entry

    # ------------------------------------------------------------------
    # Cancellation: unlink -> cancel linked work entry
    # ------------------------------------------------------------------

    def unlink(self):
        # Cancel linked overtime work entries before destroying the source.
        for line in self:
            if line.x_overtime_work_entry_id:
                line.x_overtime_work_entry_id.sudo().state = "cancelled"
        return super().unlink()

    # ------------------------------------------------------------------
    # AI payload helper (consumed by weekly summary aggregator)
    # ------------------------------------------------------------------

    def _custom_ai_payload(self):
        self.ensure_one()
        return {
            "date": fields.Date.to_string(self.date) if self.date else None,
            "employee": self.employee_id.name or "",
            "project": self.project_id.name or "",
            "task": getattr(self, "task_id", False) and self.task_id.name or "",
            "hours": self.unit_amount or 0.0,
            "overtime_hours": self.x_overtime_hours or 0.0,
            "billable": bool(self.x_billable),
            "description": (self.name or "")[:500],
        }
