# -*- coding: utf-8 -*-
"""Gate ``account.move`` posting on approval state.

The user-facing Post button (``action_post``) auto-submits the approval
request (when a matrix matches) and leaves the move in Waiting Approval;
only moves that need no approval or are already approved proceed. The
low-level ``_post`` keeps the raising ``_approval_check_required`` safety-net
so any *programmatic* post of a still-unapproved, matrix-matched move raises
rather than silently posting.
"""

from __future__ import annotations

from odoo import models


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "approval.mixin"]

    def action_post(self):
        proceed = self.browse()
        for move in self:
            if move._approval_request_or_proceed():
                proceed |= move
        if proceed:
            return super(AccountMove, proceed).action_post()
        return True

    def _post(self, soft=True):
        for move in self:
            move._approval_check_required()
        return super()._post(soft=soft)

    def _approval_on_granted(self):
        return self.action_post()
