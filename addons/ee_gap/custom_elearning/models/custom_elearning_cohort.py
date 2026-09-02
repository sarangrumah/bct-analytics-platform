# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomElearningCohort(models.Model):
    _name = "custom.elearning.cohort"
    _description = "eLearning Cohort / Batch"
    _inherit = ["mail.thread"]
    _order = "start_date desc, name"

    name = fields.Char(required=True, tracking=True)
    channel_id = fields.Many2one(
        "slide.channel",
        string="Course",
        required=True,
        ondelete="cascade",
    )
    start_date = fields.Date(string="Start Date")
    end_date = fields.Date(string="End Date")
    capacity = fields.Integer(default=30)
    member_ids = fields.Many2many(
        "res.partner",
        relation="custom_elearning_cohort_partner_rel",
        column1="cohort_id",
        column2="partner_id",
        string="Members",
    )
    enrolled_count = fields.Integer(
        compute="_compute_enrolled_count",
        store=True,
        string="Enrolled",
    )
    instructor_id = fields.Many2one("res.users", string="Instructor")
    department_id = fields.Many2one(
        "hr.department",
        string="Auto-enrol Department",
        help="Default department used by action_auto_enrol_by_department().",
    )
    last_reminder_date = fields.Date(
        string="Last Reminder Sent",
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("running", "Running"),
            ("completed", "Completed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    @api.depends("member_ids")
    def _compute_enrolled_count(self):
        for rec in self:
            rec.enrolled_count = len(rec.member_ids)

    # ---------------- Auto-enrol ----------------

    def action_auto_enrol_by_department(self, department_id=None):
        """Enrol every employee in the given department as a cohort member.

        Each ``hr.employee`` with a linked ``work_contact_id`` (preferred) or
        ``user_id.partner_id`` is added to ``member_ids``. Channel partners
        rows on ``slide.channel`` are also created when missing.
        """
        self.ensure_one()
        Employee = self.env["hr.employee"]
        dept = department_id or self.department_id.id
        if not dept:
            raise UserError(_("No department supplied for auto-enrolment."))
        if isinstance(dept, models.BaseModel):
            dept_id = dept.id
        else:
            dept_id = int(dept)

        employees = Employee.search([("department_id", "=", dept_id)])
        if not employees:
            return 0

        partners = self.env["res.partner"]
        for emp in employees:
            partner = emp.work_contact_id or emp.user_id.partner_id
            if partner:
                partners |= partner

        new_partners = partners - self.member_ids
        if new_partners:
            self.write({"member_ids": [(4, p.id) for p in new_partners]})
            # Also create the slide.channel.partner enrolment row.
            SCP = self.env["slide.channel.partner"]
            for p in new_partners:
                existing = SCP.search(
                    [
                        ("channel_id", "=", self.channel_id.id),
                        ("partner_id", "=", p.id),
                    ],
                    limit=1,
                )
                if not existing:
                    SCP.create(
                        {
                            "channel_id": self.channel_id.id,
                            "partner_id": p.id,
                        }
                    )
            self.message_post(
                body=_("Auto-enrolled %(count)s member(s) from department %(dept)s.")
                % {
                    "count": len(new_partners),
                    "dept": self.env["hr.department"].browse(dept_id).name,
                }
            )
        return len(new_partners)

    # ---------------- Reminder ----------------

    def _past_midpoint(self, today=None):
        """Return True when the cohort has consumed >=50% of its window."""
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        if not self.start_date or not self.end_date:
            return False
        total = (self.end_date - self.start_date).days
        if total <= 0:
            return False
        elapsed = (today - self.start_date).days
        return (elapsed / total) >= 0.5

    def action_send_completion_reminders(self, force=False):
        """Email members whose completion < 50%.

        Returns the number of reminders dispatched.
        Skips cohorts that are not yet past their mid-point unless
        ``force=True``.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        if not force and not self._past_midpoint(today=today):
            return 0
        template = self.env.ref(
            "custom_elearning.mail_template_cohort_completion_reminder",
            raise_if_not_found=False,
        )
        SCP = self.env["slide.channel.partner"]
        sent = 0
        for partner in self.member_ids:
            scp = SCP.search(
                [
                    ("channel_id", "=", self.channel_id.id),
                    ("partner_id", "=", partner.id),
                ],
                limit=1,
            )
            completion = scp.completion if scp else 0.0
            if completion >= 50.0:
                continue
            if template:
                try:
                    template.with_context(
                        cohort=self,
                        partner=partner,
                        completion=completion,
                    ).send_mail(self.id, force_send=False)
                    sent += 1
                except Exception as exc:  # pragma: no cover - defensive
                    _logger.warning(
                        "custom_elearning: reminder send failed cohort=%s partner=%s: %s",
                        self.id,
                        partner.id,
                        exc,
                    )
            else:
                # Fallback: post a chatter note on the cohort.
                self.message_post(
                    body=_("Reminder owed to %(name)s — completion %(c).0f%%")
                    % {"name": partner.display_name, "c": completion}
                )
                sent += 1
        if sent:
            self.last_reminder_date = today
            self.message_post(body=_("Dispatched %s completion reminder(s).") % sent)
        return sent
