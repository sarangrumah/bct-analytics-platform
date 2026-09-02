# -*- coding: utf-8 -*-
"""Shared engagement lifecycle for every Finance Portal document.

Odoo is a *system of engagement*: this mixin drives the submission ->
Tax review -> Finance review -> approved -> pushed-to-SAP -> posted/paid
lifecycle, but it NEVER posts a journal entry. The actual GL posting / MIRO /
payment happens in SAP. Two hooks decouple the SAP edge:

* ``_finance_push_to_sap`` — called once the approval matrix grants the
  document. The base implementation is a *local stub* (marks the doc pushed)
  so the engagement layer is usable standalone; ``custom_finance_portal_sap``
  overrides it to enqueue a ``queue_job`` that calls the SAP bridge.
* ``_finance_apply_sap_status`` — called by the inbound bridge webhook to
  mirror the SAP payment-plan date and payment status back onto the doc.

The Tax -> Finance two-stage review is expressed as the two tiers of an
``approval.matrix`` resolved by ``approval.mixin``; the document state stays
``submitted`` while the tiers progress (current tier visible via
``x_custom_approval_state``).
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# ir.config_parameter fallback for the "PR required above" threshold when no
# finance.limitation row matches. Mirrors the spreadsheet rule (Rp 1.000.000).
PR_THRESHOLD_PARAM = "custom_finance_portal.pr_required_threshold"
DEFAULT_PR_THRESHOLD = 1_000_000.0


class FinanceDocumentMixin(models.AbstractModel):
    _name = "finance.document.mixin"
    _description = "Finance Portal Document (engagement lifecycle)"
    _inherit = ["mail.thread", "approval.mixin", "pdp.audited.mixin"]
    _order = "create_date desc"

    # ---- identity ----
    name = fields.Char(
        string="Reference",
        required=True,
        default=lambda self: self.env._("New"),
        copy=False,
        tracking=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company / Business Plant",
        required=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    requester_id = fields.Many2one(
        "hr.employee",
        string="Requester",
        default=lambda self: self.env["hr.employee"].search([("user_id", "=", self.env.uid)], limit=1),
        tracking=True,
    )
    division_id = fields.Many2one(
        "finance.vertical",
        string="Division / Vertical",
        tracking=True,
    )
    amount = fields.Monetary(
        string="Amount",
        currency_field="currency_id",
        tracking=True,
    )

    # ---- engagement state ----
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("pushed", "Pushed to SAP"),
            ("posted", "Posted (SAP)"),
            ("paid", "Paid"),
            ("rejected", "Rejected"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        copy=False,
        tracking=True,
    )

    # ---- PR / budget linkage (PR data sourced realtime from SAP) ----
    pr_number = fields.Char(string="PR Number", tracking=True, copy=False)
    po_number = fields.Char(string="PO Number", tracking=True, copy=False)

    # ---- SAP mirror (read-only; written by the bridge webhook) ----
    sap_document_no = fields.Char(string="SAP Document No", readonly=True, copy=False)
    sap_sync_state = fields.Selection(
        selection=[
            ("not_pushed", "Not Pushed"),
            ("queued", "Queued"),
            ("pushed", "Pushed"),
            ("ack", "Acknowledged"),
            ("error", "Error"),
        ],
        default="not_pushed",
        readonly=True,
        copy=False,
    )
    sap_pushed_at = fields.Datetime(string="Pushed At", readonly=True, copy=False)
    sap_payment_plan_date = fields.Date(string="Planned Payment Date", readonly=True, copy=False)
    sap_payment_status = fields.Selection(
        selection=[
            ("pending", "Pending"),
            ("scheduled", "Scheduled"),
            ("paid", "Paid"),
            ("failed", "Failed"),
        ],
        string="SAP Payment Status",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------
    # Sequence on create — concrete models declare ``_sequence_code``.
    # ------------------------------------------------------------------
    _sequence_code: str | None = None

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if self._sequence_code and (not vals.get("name") or vals.get("name") == _("New")):
                vals["name"] = self.env["ir.sequence"].next_by_code(self._sequence_code) or _("New")
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------
    def action_submit_for_approval(self):
        for rec in self:
            if rec.state != "draft":
                raise UserError(_("Only draft documents can be submitted."))
            rec._finance_validate_submission()
            rec.state = "submitted"
            rec._pdp_audit_write("submit", rec.id, {"state": "submitted"})
            # Auto-create + submit the approval request (Tax -> Finance tiers).
            # No-op when no matrix matches → doc remains submitted and can be
            # approved directly by an authorised user.
            rec._approval_request_or_proceed()
        return True

    def _approval_on_granted(self):
        """Engine hook: matrix granted → approve then push to SAP."""
        return self.action_approve()

    def action_approve(self):
        for rec in self:
            if rec.state not in ("submitted", "draft"):
                raise UserError(_("Only submitted documents can be approved."))
            rec._approval_check_required()
            rec.state = "approved"
            rec._pdp_audit_write("approve", rec.id, {"state": "approved"})
            rec._finance_push_to_sap()
        return True

    def action_reject(self):
        for rec in self:
            if rec.state in ("paid", "posted"):
                raise UserError(_("Cannot reject a document already posted/paid in SAP."))
            rec.state = "rejected"
            rec.action_cancel_approval()
            rec._pdp_audit_write("reject", rec.id, {"state": "rejected"})
        return True

    def action_cancel(self):
        for rec in self:
            if rec.state in ("paid", "posted"):
                raise UserError(_("Cannot cancel a document already posted/paid in SAP."))
            rec.state = "cancelled"
            rec.action_cancel_approval()
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state in ("paid", "posted", "pushed"):
                raise UserError(_("Cannot reset a document already sent to SAP."))
            rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Validation — PR-required threshold + (optional) budget check
    # ------------------------------------------------------------------
    def _finance_pr_threshold(self) -> float:
        """Resolve the PR-required threshold for this document.

        Order: a matching ``finance.limitation`` row (by submission scope) →
        the ``custom_finance_portal.pr_required_threshold`` config param →
        the hard default (Rp 1.000.000).
        """
        self.ensure_one()
        limitation = self.env["finance.limitation"].sudo()._resolve_for(self)
        if limitation and limitation.pr_required_above:
            return limitation.pr_required_above
        param = self.env["ir.config_parameter"].sudo().get_param(PR_THRESHOLD_PARAM)
        try:
            return float(param) if param else DEFAULT_PR_THRESHOLD
        except (TypeError, ValueError):
            return DEFAULT_PR_THRESHOLD

    def _finance_validate_submission(self):
        """Block submission that violates PR / budget rules."""
        self.ensure_one()
        threshold = self._finance_pr_threshold()
        if threshold and (self.amount or 0.0) > threshold and not self.pr_number:
            raise UserError(
                _(
                    "Amount %(amt)s exceeds %(thr)s — a Purchase Request (PR) number is required before submitting.",
                    amt=self.amount,
                    thr=threshold,
                )
            )
        # Soft dependency on custom_finance_budget: enforce remaining budget.
        if "finance.budget" in self.env:
            self.env["finance.budget"].sudo()._check_document_budget(self)
        return True

    # ------------------------------------------------------------------
    # SAP edge — overridden by custom_finance_portal_sap
    # ------------------------------------------------------------------
    def _finance_sap_payload(self) -> dict:
        """Canonical document payload pushed to the SAP bridge.

        Concrete models extend this with type-specific lines/GL coding. Kept
        here so the bridge contract has one shape per document type.
        """
        self.ensure_one()
        return {
            "doc_type": self._name,
            "reference": self.name,
            "company": self.company_id.name,
            "requester": self.requester_id.name or "",
            "division": self.division_id.code or "",
            "amount": float(self.amount or 0.0),
            "currency": self.currency_id.name,
            "pr_number": self.pr_number or "",
            "po_number": self.po_number or "",
        }

    def _finance_push_to_sap(self):
        """Local stub: mark the doc as pushed.

        ``custom_finance_portal_sap`` overrides this to enqueue an async push
        to the SAP bridge (and leaves the state at ``pushed``/``queued`` until
        SAP acknowledges).
        """
        for rec in self:
            rec.write(
                {
                    "sap_sync_state": "pushed",
                    "sap_pushed_at": fields.Datetime.now(),
                    "state": "pushed",
                }
            )
            rec.message_post(
                body=_("Pushed to SAP (stub mode — no bridge configured)."),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def _finance_apply_sap_status(self, vals: dict):
        """Mirror SAP-side status back onto the document.

        Called by the bridge inbound webhook. ``vals`` keys: ``sap_document_no``,
        ``sap_payment_plan_date``, ``sap_payment_status``. Moves the engagement
        state to posted/paid accordingly.
        """
        self.ensure_one()
        write_vals = {}
        for key in ("sap_document_no", "sap_payment_plan_date", "sap_payment_status"):
            if key in vals and vals[key]:
                write_vals[key] = vals[key]
        if vals.get("sap_document_no"):
            write_vals["sap_sync_state"] = "ack"
        status = vals.get("sap_payment_status")
        if status == "paid":
            write_vals["state"] = "paid"
        elif vals.get("sap_document_no") and self.state in ("pushed", "approved"):
            write_vals["state"] = "posted"
        if write_vals:
            self.write(write_vals)
            self._pdp_audit_write("sap_status", self.id, write_vals)
        return True
