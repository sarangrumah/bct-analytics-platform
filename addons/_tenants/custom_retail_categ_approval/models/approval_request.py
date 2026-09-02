# -*- coding: utf-8 -*-
"""Make the pending-approval notification actually arrive.

``approval.request._notify_pending`` mails
``custom_approval_engine.mail_template_approval_pending`` — a template that was
never defined (the engine's ``data/mail_template_data.xml`` is a placeholder), so
it returns early and nobody is told anything. Rather than fix the engine for
every consumer at once, this schedules an Odoo activity per pending approver for
category reclassifications only: the bell lights up immediately, no outgoing mail
server required, and the to-do sits in the approver's list until they act.
"""

import logging

from odoo import _, models

_logger = logging.getLogger(__name__)

_TARGET_MODEL = "retail.categ.reclass"

# Above this many approvers the per-user to-do stops being a notification and
# becomes noise: a group tier can easily resolve to several dozen people, and a
# to-do that forty of them are meant to ignore trains everyone to ignore
# approval to-dos. Past the cap the request is announced on its chatter instead
# and stays visible in the approver's portal. Override per database with
# ``custom_retail_categ_approval.activity_fanout_max``.
_FANOUT_PARAM = "custom_retail_categ_approval.activity_fanout_max"
_FANOUT_DEFAULT = 8


class ApprovalRequest(models.Model):
    _inherit = "approval.request"

    def _notify_pending(self):
        result = super()._notify_pending()
        for request in self:
            if request.res_model != _TARGET_MODEL:
                continue
            try:
                request._retail_schedule_categ_activities()
            except Exception:
                # Same contract as the engine's own mail path: the approval
                # decision is already persisted, a notification failure must
                # not roll it back.
                _logger.exception("approval %s: category-change activity failed (non-fatal)", request.id)
        return result

    def _retail_schedule_categ_activities(self):
        self.ensure_one()
        record = self._record()
        if not record or not record.exists():
            return
        activity_type = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        if not activity_type:
            return
        summary = _("Approve category change: %s", record.new_categ_id.display_name)
        note = record._approval_summary().replace("\n", "<br/>")

        approvers = self.pending_approver_ids
        try:
            cap = int(self.env["ir.config_parameter"].sudo().get_param(_FANOUT_PARAM, _FANOUT_DEFAULT))
        except (TypeError, ValueError):
            cap = _FANOUT_DEFAULT
        if len(approvers) > cap:
            self.sudo().message_post(
                body=_(
                    "%(summary)s<br/><br/>%(note)s<br/><br/>"
                    "Pending with %(count)s approvers in tier <b>%(tier)s</b> — too many for "
                    "individual to-dos, so no activity was scheduled. It is waiting in "
                    "Approvals for any of them.",
                    summary=summary,
                    note=note,
                    count=len(approvers),
                    tier=self.current_tier_id.name or "?",
                )
            )
            return

        for user in approvers:
            already = self.activity_ids.filtered(
                lambda act, u=user: act.activity_type_id == activity_type and act.user_id == u
            )
            if already:
                continue
            self.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=user.id,
                summary=summary,
                note=note,
            )
