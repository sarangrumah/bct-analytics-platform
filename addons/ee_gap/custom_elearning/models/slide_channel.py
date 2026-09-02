# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SlideChannel(models.Model):
    _inherit = "slide.channel"

    # --- Certificate ---
    x_certificate_template_id = fields.Many2one(
        "ir.actions.report",
        string="Certificate Template",
    )
    x_certificate_generated_count = fields.Integer(
        string="Certificates Generated",
        default=0,
        copy=False,
    )
    x_id_language = fields.Selection(
        [("id", "Bahasa Indonesia"), ("en", "English")],
        string="Certificate Language",
        default="id",
    )

    # --- Catalog filter ---
    x_level = fields.Selection(
        [
            ("beginner", "Beginner"),
            ("intermediate", "Intermediate"),
            ("advanced", "Advanced"),
        ],
        string="Level",
        default="beginner",
    )
    x_duration_hours = fields.Integer(
        string="Duration (Hours)",
        default=0,
    )
    x_certificate_validity_months = fields.Integer(
        string="Certificate Validity (Months)",
        default=12,
        help="Number of months the issued certificate stays valid.",
    )
    x_id_category = fields.Selection(
        [
            ("technical", "Technical"),
            ("softskill", "Soft Skill"),
            ("compliance", "Compliance"),
            ("onboarding", "Onboarding"),
            ("other", "Other"),
        ],
        string="Category (ID)",
        default="other",
    )

    # --- Appraisal / skills bridge ---
    # NOTE: custom_hr_appraisal does not currently expose an `appraisal.skill`
    # model — use a Char placeholder so the completion hook can be wired later
    # without a hard FK dependency.
    x_completion_appraisal_skill_code = fields.Char(
        string="Completion Skill Code",
        help="Code of the skill (hr.skill or appraisal.skill) to award upon course completion.",
    )

    # ---------------- Actions ----------------

    def action_generate_certificate(self, partner_ids=None):
        """Generate certificate PDF(s) for the given partner(s).

        When ``partner_ids`` is omitted, generates one certificate for every
        completed (completion=100) member of the channel.
        Returns the standard ``ir.actions.report`` action when called from UI.
        """
        self.ensure_one()
        SCP = self.env["slide.channel.partner"]
        if partner_ids:
            scp_records = SCP.search(
                [
                    ("channel_id", "=", self.id),
                    ("partner_id", "in", list(partner_ids)),
                ]
            )
        else:
            scp_records = SCP.search(
                [
                    ("channel_id", "=", self.id),
                    ("completion", ">=", 100.0),
                ]
            )

        if not scp_records:
            raise UserError(_("No completed members found for course '%s'.") % self.name)

        # Stamp completion record(s) and increment counter
        report = self.env.ref(
            "custom_elearning.action_report_elearning_certificate",
            raise_if_not_found=False,
        )
        for scp in scp_records:
            scp._stamp_certificate_issued()
        self.x_certificate_generated_count = (self.x_certificate_generated_count or 0) + len(scp_records)
        self.message_post(
            body=_("Generated %s certificate(s).") % len(scp_records),
        )
        if report:
            return report.report_action(scp_records)
        return True

    # ---------------- Cron entry-point ----------------

    @api.model
    def _cron_send_completion_reminders(self):
        """Send reminder emails for every cohort past its mid-point.

        For each active cohort whose elapsed window exceeds 50%, email every
        member whose completion is still below 50%.
        """
        Cohort = self.env["custom.elearning.cohort"]
        cohorts = Cohort.search([("state", "in", ("open", "running"))])
        sent = 0
        for cohort in cohorts:
            sent += cohort.action_send_completion_reminders()
        return sent
