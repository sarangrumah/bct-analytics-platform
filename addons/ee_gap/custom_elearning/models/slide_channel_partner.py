# -*- coding: utf-8 -*-
import logging

from odoo import fields, models

_logger = logging.getLogger(__name__)


class SlideChannelPartner(models.Model):
    _inherit = "slide.channel.partner"

    x_certificate_issued = fields.Boolean(
        string="Certificate Issued",
        copy=False,
    )
    x_certificate_issue_date = fields.Date(
        string="Certificate Issue Date",
        copy=False,
    )
    x_certificate_expiry_date = fields.Date(
        string="Certificate Expiry Date",
        copy=False,
    )
    report_certificate_id = fields.Many2one(
        "ir.actions.report",
        string="Certificate Report",
        compute="_compute_report_certificate_id",
        store=False,
        help="Convenience pointer to the qweb-pdf report used for this channel's certificate.",
    )

    def _compute_report_certificate_id(self):
        default_report = self.env.ref(
            "custom_elearning.action_report_elearning_certificate",
            raise_if_not_found=False,
        )
        for rec in self:
            rec.report_certificate_id = rec.channel_id.x_certificate_template_id or default_report

    # ---------------- Hooks ----------------

    def write(self, vals):
        res = super().write(vals)
        if "completion" in vals:
            for rec in self:
                if rec.completion >= 100.0:
                    rec._on_course_completed()
        return res

    def _stamp_certificate_issued(self):
        """Mark the completion record as having a certificate issued."""
        today = fields.Date.context_today(self)
        for rec in self:
            months = rec.channel_id.x_certificate_validity_months or 12
            expiry = fields.Date.add(today, days=int(months * 30))
            rec.write(
                {
                    "x_certificate_issued": True,
                    "x_certificate_issue_date": today,
                    "x_certificate_expiry_date": expiry,
                }
            )
        return True

    def _on_course_completed(self):
        """Trigger skill assignment when the course completion hits 100%."""
        for rec in self:
            channel = rec.channel_id
            code = channel.x_completion_appraisal_skill_code
            if not code:
                continue
            try:
                rec._assign_hr_skill(code)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning(
                    "custom_elearning: skill assignment failed for partner=%s channel=%s code=%s: %s",
                    rec.partner_id.id,
                    channel.id,
                    code,
                    exc,
                )

    def _assign_hr_skill(self, code):
        """Add the matching hr.skill to the partner's hr.employee.

        Conditional on hr_skills being installed. Silently returns False if
        the bridge cannot be wired (models or fields missing).
        """
        self.ensure_one()
        Skill = self.env.get("hr.skill")
        EmployeeSkill = self.env.get("hr.employee.skill")
        Employee = self.env.get("hr.employee")
        if not Skill or not Employee:
            return False
        skill = Skill.search([("name", "=", code)], limit=1)
        if not skill:
            return False
        employees = Employee.search([("work_contact_id", "=", self.partner_id.id)])
        if not employees:
            employees = Employee.search([("user_id.partner_id", "=", self.partner_id.id)])
        if not employees:
            return False
        # If hr.employee.skill exists, create the relation row.
        if EmployeeSkill is not None and "skill_ids" in Employee._fields:
            for emp in employees:
                if skill not in emp.skill_ids:
                    emp.write({"skill_ids": [(4, skill.id)]})
            return True
        # Fallback: attach to many2many if any compatible field exists.
        if "skill_ids" in Employee._fields:
            for emp in employees:
                emp.write({"skill_ids": [(4, skill.id)]})
            return True
        return False
