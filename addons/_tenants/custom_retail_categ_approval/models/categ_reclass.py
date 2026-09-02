# -*- coding: utf-8 -*-
"""Put Finance in front of a product-category reclassification.

``retail.categ.reclass`` already knows how to move a product to another category
and book the correction. What it did not have was a gate: whoever could open the
screen could change the ledger. This adds one, reusing ``custom_approval_engine``
rather than inventing a second workflow.

Pressing *Change Category & Book Correction* now stops at the gate: an
``approval.request`` is raised against the configured matrix (Accounting Manager,
then Finance Manager) and the record parks in **Waiting Approval**. Nothing is
written — not the category, not a single journal line. When the last tier
approves, the engine calls ``_approval_on_granted`` as the original requester and
the same action runs for real.
"""

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

from .product_template import BYPASS_CATEG_GUARD

_logger = logging.getLogger(__name__)


class RetailCategReclass(models.Model):
    _name = "retail.categ.reclass"
    _inherit = ["retail.categ.reclass", "approval.mixin"]

    state = fields.Selection(
        selection_add=[("to_approve", "Waiting Approval"), ("applied",)],
        ondelete={"to_approve": "set default"},
    )

    def _check_tiers_are_staffed(self):
        """Refuse to submit into a tier nobody can sign.

        The Finance Manager group ships empty — somebody has to be put in it per
        database. Submitting anyway would park the request forever with no
        pending approver and no error, which looks exactly like a system that
        swallowed the request.
        """
        self.ensure_one()
        matrix = self.env["approval.matrix"].sudo()._resolve_for(self)
        if not matrix:
            return
        empty = [tier.name for tier in matrix.tier_ids if not tier.sudo()._resolve_approvers(self)]
        if empty:
            raise UserError(
                _(
                    "Approval tier(s) %(tiers)s have no members, so this request "
                    "could never be signed. Add users to the corresponding group "
                    "before submitting a category change.",
                    tiers=", ".join(empty),
                )
            )

    def action_apply(self):
        self.ensure_one()
        if self.state == "draft":
            self.action_compute()
        if self.state != "applied":
            self._check_tiers_are_staffed()
        if not self._approval_request_or_proceed():
            # Park it. Writing the state here rather than inside the engine keeps
            # the statusbar honest even when the matrix is reconfigured later.
            self.state = "to_approve"
            return self._pending_notification()
        return super(RetailCategReclass, self.with_context(**{BYPASS_CATEG_GUARD: True})).action_apply()

    def _approval_on_granted(self):
        """Re-run the gated action once every tier has approved.

        The engine calls this as the *requester*, who is typically master-data
        staff with no rights to create a journal entry. The authority to book
        comes from the completed approval, not from whoever filled the form in,
        so the apply runs elevated.
        """
        for rec in self:
            try:
                rec.sudo().action_apply()
            except UserError as error:
                # The engine swallows anything raised here so a failed
                # auto-proceed cannot roll back the approval decision. That
                # would leave the request "approved" with nothing booked and no
                # trace, so the reason is written where the requester will see
                # it and the record stays parked instead of claiming success.
                _logger.warning("categ reclass %s: apply after approval failed: %s", rec.name, error)
                rec.sudo().write(
                    {
                        "state": "to_approve",
                        "warning_text": _(
                            "Approved, but the correction could not be booked yet:\n%s",
                            error,
                        ),
                    }
                )
        return True

    def action_cancel(self):
        # Only a request still in flight can be withdrawn. A completed approval
        # is a fact — undoing the reclassification afterwards does not unsay it,
        # and the engine refuses to cancel it anyway.
        for rec in self:
            request = rec.x_custom_approval_request_id
            if request and request.state in ("draft", "pending"):
                rec.action_cancel_approval()
        # Undoing puts the previous category back, which is itself a category
        # change on a product with movement — the guard would refuse it.
        return super(RetailCategReclass, self.with_context(**{BYPASS_CATEG_GUARD: True})).action_cancel()

    def _pending_notification(self):
        """Pop-up telling the requester where the request went, with a way in."""
        self.ensure_one()
        request = self.x_custom_approval_request_id
        action = {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Waiting for Finance approval"),
                "message": _(
                    "%(name)s was submitted for approval. The category and its "
                    "correction entries will only be created once Finance approves.",
                    name=self.name,
                ),
                "type": "warning",
                "sticky": False,
            },
        }
        if request:
            action["params"]["next"] = {
                "type": "ir.actions.act_window",
                "res_model": "approval.request",
                "res_id": request.id,
                "view_mode": "form",
            }
        return action

    def _approval_summary(self):
        """One-paragraph description of what an approver is about to allow."""
        self.ensure_one()
        products = ", ".join(self.product_tmpl_ids.mapped("display_name")[:5])
        if len(self.product_tmpl_ids) > 5:
            products += "…"
        accounts = sorted(
            {
                "%s → %s" % (line.source_account_id.display_name, line.target_account_id.display_name)
                for line in self.line_ids
            }
        )
        return _(
            "%(products)s → %(categ)s\n"
            "Revenue reclassified: %(amount)s\n"
            "Accounts: %(accounts)s\n"
            "Lines in a closed period (booked in the current month as a reversal "
            "plus a re-booking): %(closed)s",
            products=products,
            categ=self.new_categ_id.display_name,
            amount=self.total_amount,
            accounts="; ".join(accounts) or _("none"),
            closed=self.closed_period_count,
        )
