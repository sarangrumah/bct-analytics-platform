# -*- coding: utf-8 -*-
"""Extend ``hr.expense`` with AI OCR, generic approval engine, PDP audit,
corporate card linkage and mileage tracking."""

from __future__ import annotations

import base64
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)

CONFIG_PARAM_MILEAGE_RATE = "custom_expenses.id_mileage_rate"
DEFAULT_MILEAGE_RATE = 5000.0


class HrExpense(models.Model):
    _name = "hr.expense"
    _inherit = ["hr.expense", "approval.mixin"]

    # ------------------------------------------------------------------
    # AI OCR extraction fields
    # ------------------------------------------------------------------
    x_receipt_ocr_text = fields.Text(
        string="Receipt OCR Text",
        help="Raw OCR text extracted from the attached receipt.",
        copy=False,
    )
    x_ai_extracted_amount = fields.Monetary(
        string="AI Extracted Amount",
        currency_field="currency_id",
        copy=False,
    )
    x_ai_extracted_tax_amount = fields.Monetary(
        string="AI Extracted Tax",
        currency_field="currency_id",
        copy=False,
    )
    x_ai_extracted_date = fields.Date(
        string="AI Extracted Date",
        copy=False,
    )
    x_ai_extracted_vendor = fields.Char(
        string="AI Extracted Vendor",
        copy=False,
    )
    x_ai_extracted_currency_code = fields.Char(
        string="AI Extracted Currency",
        size=8,
        copy=False,
    )
    x_ai_confidence = fields.Float(
        string="AI Confidence",
        digits=(3, 2),
        help="Confidence score in [0.0, 1.0] from the AI extraction step.",
        copy=False,
    )

    # ------------------------------------------------------------------
    # Corporate card linkage
    # ------------------------------------------------------------------
    x_corporate_card_id = fields.Many2one(
        "custom.expense.corporate.card",
        string="Corporate Card",
        ondelete="set null",
        copy=False,
        help="If set, the expense is paid via this corporate card and is "
        "excluded from the employee reimbursement queue.",
    )

    # ------------------------------------------------------------------
    # Mileage tracking
    # ------------------------------------------------------------------
    x_is_mileage = fields.Boolean(
        string="Is Mileage",
        compute="_compute_is_mileage",
        store=True,
    )
    x_mileage_km = fields.Float(
        string="Distance (km)",
        digits=(12, 2),
        copy=False,
    )
    x_mileage_rate = fields.Monetary(
        string="Mileage Rate / km",
        currency_field="currency_id",
        default=lambda self: self._default_mileage_rate(),
        copy=False,
    )

    # ------------------------------------------------------------------
    # Defaults / computes
    # ------------------------------------------------------------------
    @api.model
    def _default_mileage_rate(self) -> float:
        try:
            raw = self.env["ir.config_parameter"].sudo().get_param(CONFIG_PARAM_MILEAGE_RATE, str(DEFAULT_MILEAGE_RATE))
            return float(raw)
        except (TypeError, ValueError):
            return DEFAULT_MILEAGE_RATE

    @api.depends("product_id", "product_id.default_code")
    def _compute_is_mileage(self):
        for exp in self:
            code = (exp.product_id.default_code or "").upper() if exp.product_id else ""
            exp.x_is_mileage = code == "MILEAGE"

    @api.onchange("x_mileage_km", "x_mileage_rate", "x_is_mileage")
    def _onchange_mileage(self):
        for exp in self:
            if exp.x_is_mileage and exp.x_mileage_km and exp.x_mileage_rate:
                exp.total_amount = exp.x_mileage_km * exp.x_mileage_rate
                exp.quantity = exp.x_mileage_km
                exp.unit_amount = exp.x_mileage_rate

    @api.onchange("x_corporate_card_id")
    def _onchange_corporate_card(self):
        for exp in self:
            if exp.x_corporate_card_id:
                # Corporate card → company pays directly, not a reimbursement
                if hasattr(exp, "payment_mode"):
                    exp.payment_mode = "company_account"

    # ------------------------------------------------------------------
    # Create / write — keep mileage total in sync, force company_account
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._apply_corporate_card_payment_mode(vals)
            self._apply_mileage_total(vals)
        records = super().create(vals_list)
        return records

    def write(self, vals):
        self._apply_corporate_card_payment_mode(vals)
        # For mileage, only auto-recompute total when km/rate explicitly change
        if any(k in vals for k in ("x_mileage_km", "x_mileage_rate")):
            for exp in self:
                merged = {
                    **{
                        "x_mileage_km": exp.x_mileage_km,
                        "x_mileage_rate": exp.x_mileage_rate,
                        "x_is_mileage": exp.x_is_mileage,
                    },
                    **vals,
                }
                if merged.get("x_is_mileage") and merged.get("x_mileage_km") and merged.get("x_mileage_rate"):
                    vals.setdefault("total_amount", merged["x_mileage_km"] * merged["x_mileage_rate"])
        return super().write(vals)

    @staticmethod
    def _apply_corporate_card_payment_mode(vals):
        if vals.get("x_corporate_card_id"):
            vals.setdefault("payment_mode", "company_account")

    @staticmethod
    def _apply_mileage_total(vals):
        # Detect mileage either via explicit flag or via MILEAGE product default_code
        # The boolean is a compute(store=True), so it may not be in vals; rely on km/rate presence
        km = vals.get("x_mileage_km")
        rate = vals.get("x_mileage_rate")
        if km and rate and not vals.get("total_amount"):
            vals["total_amount"] = float(km) * float(rate)

    # ------------------------------------------------------------------
    # AI OCR action — proper attachment-based bridge call
    # ------------------------------------------------------------------
    def _get_primary_receipt_attachment(self):
        """Return the most relevant attachment for OCR.

        Preference:
          1. ``message_main_attachment_id`` if present.
          2. Latest attachment in ``attachment_ids``.
          3. Most recent ir.attachment on this record.
        """
        self.ensure_one()
        att = False
        if getattr(self, "message_main_attachment_id", False):
            att = self.message_main_attachment_id
        if not att and getattr(self, "attachment_ids", False):
            att = self.attachment_ids.sorted(key=lambda a: a.create_date or fields.Datetime.now(), reverse=True)[:1]
        if not att:
            att = (
                self.env["ir.attachment"]
                .sudo()
                .search(
                    [("res_model", "=", self._name), ("res_id", "=", self.id)],
                    order="create_date desc",
                    limit=1,
                )
            )
        return att

    def _custom_ai_payload(self):
        """Build payload for ``custom.ai._recommend``.

        Encodes the primary receipt attachment as base64 so the AI gateway
        can run vision OCR. Falls back to metadata-only when nothing is
        attached.
        """
        self.ensure_one()
        att = self._get_primary_receipt_attachment()
        image_b64 = ""
        mimetype = ""
        att_name = ""
        if att:
            try:
                raw = att.raw if hasattr(att, "raw") and att.raw else None
                if raw is None and att.datas:
                    raw = base64.b64decode(att.datas)
                if raw is not None:
                    image_b64 = base64.b64encode(raw).decode("ascii")
                    mimetype = att.mimetype or ""
                    att_name = att.name or ""
            except Exception as e:  # pragma: no cover
                _logger.warning("Receipt attachment encode failed: %s", e)

        return {
            "task": "extract_receipt",
            "expense_ref": self.name or "",
            "employee": self.employee_id.name if self.employee_id else "",
            "current_total": float(self.total_amount or 0.0),
            "current_currency": self.currency_id.name if self.currency_id else "",
            "current_date": fields.Date.to_string(self.date) if self.date else "",
            "description": (self.description or "")[:4000] if hasattr(self, "description") else "",
            "attachment_name": att_name,
            "attachment_mimetype": mimetype,
            "image_base64": image_b64,
        }

    def action_ai_extract_receipt(self):
        """Call the AI bridge to OCR the attached receipt and fill x_ai_* fields.

        Fail-safe: any exception (gateway down, no attachment, malformed
        response) is logged + surfaced as a transient notification, the
        record is never blocked.
        """
        self.ensure_one()
        payload = self._custom_ai_payload()
        if not payload.get("image_base64"):
            self.message_post(
                body=_("<b>AI Receipt OCR</b><br/>No receipt attachment found."),
                subtype_xmlid="mail.mt_note",
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("No Receipt"),
                    "message": _("Attach a receipt image/PDF before running OCR."),
                    "type": "warning",
                },
            }

        try:
            result = self.env["custom.ai"]._recommend(
                model="hr.expense",
                res_id=self.id,
                payload=payload,
            )
        except Exception as e:
            _logger.error("AI receipt extract failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }

        vals = self._parse_ai_receipt_response(result)
        if vals:
            self.write(vals)
            self.message_post(
                body=_(
                    "<b>AI Receipt OCR</b><br/>"
                    "Vendor: %(vendor)s<br/>Amount: %(amount)s %(curr)s<br/>"
                    "Tax: %(tax)s<br/>Date: %(date)s<br/>Confidence: %(conf)s"
                )
                % {
                    "vendor": vals.get("x_ai_extracted_vendor") or "-",
                    "amount": vals.get("x_ai_extracted_amount") or "-",
                    "curr": vals.get("x_ai_extracted_currency_code") or "",
                    "tax": vals.get("x_ai_extracted_tax_amount") or "-",
                    "date": vals.get("x_ai_extracted_date") or "-",
                    "conf": vals.get("x_ai_confidence") or "-",
                },
                subtype_xmlid="mail.mt_note",
            )
        else:
            self.message_post(
                body=_("<b>AI Receipt OCR</b><br/>No structured data extracted.<br/>%s")
                % (json.dumps(result, default=str)[:1000] if result else ""),
                subtype_xmlid="mail.mt_note",
            )
        return True

    @staticmethod
    def _parse_ai_receipt_response(result):
        """Translate the AI gateway response into hr.expense field values."""
        vals = {}
        if not isinstance(result, dict):
            return vals
        ocr_text = result.get("ocr_text") or result.get("text") or result.get("raw") or ""
        if ocr_text:
            vals["x_receipt_ocr_text"] = ocr_text[:65000]
        amount = result.get("amount") or result.get("total")
        if amount is not None:
            try:
                vals["x_ai_extracted_amount"] = float(amount)
            except (TypeError, ValueError):
                pass
        tax = result.get("tax_amount") or result.get("tax")
        if tax is not None:
            try:
                vals["x_ai_extracted_tax_amount"] = float(tax)
            except (TypeError, ValueError):
                pass
        extracted_date = result.get("date")
        if extracted_date:
            vals["x_ai_extracted_date"] = extracted_date
        vendor = result.get("vendor") or result.get("merchant")
        if vendor:
            vals["x_ai_extracted_vendor"] = vendor
        currency_code = result.get("currency_code") or result.get("currency")
        if currency_code:
            vals["x_ai_extracted_currency_code"] = str(currency_code)[:8]
        confidence = result.get("confidence")
        if confidence is not None:
            try:
                vals["x_ai_confidence"] = float(confidence)
            except (TypeError, ValueError):
                pass
        return vals

    # ------------------------------------------------------------------
    # Approval engine integration
    # ------------------------------------------------------------------
    def action_request_approval_expense(self):
        """Open the generic approval workflow for this expense."""
        self.action_request_approval()
        return True

    def action_submit_expenses(self):
        """Submit gated on the approval engine.

        Clicking Submit auto-submits the approval request (when a matrix
        matches) and leaves the expense in Waiting Approval; only expenses
        needing no approval or already approved are actually submitted. The
        engine re-runs this via ``_approval_on_granted`` after final approval.
        """
        proceed = self.browse()
        for expense in self:
            if expense._approval_request_or_proceed():
                proceed |= expense
        for expense in proceed:
            expense._pdp_audit_expense_event("submit")
        if proceed:
            parent = getattr(super(HrExpense, proceed), "action_submit_expenses", None)
            if callable(parent):
                return parent()
        return True

    def _approval_on_granted(self):
        return self.action_submit_expenses()

    # ------------------------------------------------------------------
    # Reimbursement payment flow
    # ------------------------------------------------------------------
    def action_register_reimbursement_payment(self):
        """Generate a single ``account.payment`` reimbursing the employee.

        Only runs when:
          * the matrix-approval state is ``approved``, and
          * payment_mode != company_account (i.e. own_account / reimbursable), and
          * no corporate card is attached.

        Partner = ``employee_id.work_contact_id`` (fallback ``user_id.partner_id``).
        """
        self.ensure_one()
        if self.x_corporate_card_id:
            return False
        payment_mode = getattr(self, "payment_mode", "own_account")
        if payment_mode == "company_account":
            return False
        if self.x_custom_approval_state and self.x_custom_approval_state != "approved":
            return False

        partner = False
        if self.employee_id and getattr(self.employee_id, "work_contact_id", False):
            partner = self.employee_id.work_contact_id
        if not partner and getattr(self, "user_id", False) and self.user_id.partner_id:
            partner = self.user_id.partner_id
        if not partner:
            return False

        journal = (
            self.env["account.journal"]
            .sudo()
            .search(
                [("type", "in", ("bank", "cash")), ("company_id", "=", self.company_id.id)],
                limit=1,
            )
        )
        Payment = self.env["account.payment"].sudo()
        payment = Payment.create(
            {
                "payment_type": "outbound",
                "partner_type": "supplier",
                "partner_id": partner.id,
                "amount": float(self.total_amount or 0.0),
                "currency_id": self.currency_id.id if self.currency_id else self.env.company.currency_id.id,
                "journal_id": journal.id if journal else False,
                "ref": _("Reimbursement: %s") % (self.name or ""),
                "memo": _("Reimbursement: %s") % (self.name or ""),
            }
        )
        self.message_post(
            body=_("Reimbursement payment %s registered for %s.") % (payment.id, partner.display_name),
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.payment",
            "res_id": payment.id,
            "view_mode": "form",
        }

    # ------------------------------------------------------------------
    # PDP audit
    # ------------------------------------------------------------------
    def _pdp_audit_expense_event(self, event: str):
        """Best-effort PDP audit log for state transitions."""
        self.ensure_one()
        try:
            user = self.env.user
            payload = {
                "event": event,
                "ref": self.name,
                "amount": float(self.total_amount or 0.0),
                "currency": self.currency_id.name if self.currency_id else "",
                "employee": self.employee_id.name if self.employee_id else "",
            }
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification
                ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, 'internal')
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    self.id,
                    event,
                    json.dumps(payload),
                ),
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("expense audit log failed: %s", e)
