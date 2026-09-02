# -*- coding: utf-8 -*-
"""Chat-style natural-language query against allowed Odoo models."""

from __future__ import annotations

import json
import logging
from typing import Any

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Models that NLQ is allowed to query — keep the schema_hint short to fit
# the prompt token budget. PII fields are excluded when user lacks the
# pdp.group_view_pii right (checked at call time).
ALLOWED_SCHEMA: list[dict[str, Any]] = [
    {
        "model": "res.partner",
        "fields": ["id", "name", "email", "phone", "is_company", "country_id", "category_id"],
        "description": "Customers & vendors (contacts).",
    },
    {
        "model": "account.move",
        "fields": [
            "id",
            "name",
            "partner_id",
            "move_type",
            "state",
            "invoice_date",
            "amount_total",
            "amount_residual",
            "currency_id",
        ],
        "description": "Invoices, vendor bills (in_invoice/out_invoice).",
    },
    {
        "model": "purchase.order",
        "fields": ["id", "name", "partner_id", "state", "date_order", "amount_total"],
        "description": "Purchase orders.",
    },
    {
        "model": "sale.order",
        "fields": ["id", "name", "partner_id", "state", "date_order", "amount_total"],
        "description": "Sales orders.",
    },
    {
        "model": "hr.payslip",
        "fields": [
            "id",
            "name",
            "employee_id",
            "period_year",
            "period_month",
            "gross_salary",
            "pph21",
            "take_home_pay",
            "state",
        ],
        "description": "Indonesian payslips (PPh 21).",
    },
    {
        "model": "approval.request",
        "fields": ["id", "state", "requested_by_id", "current_tier_id", "due_at"],
        "description": "Approval workflow requests.",
    },
    {
        "model": "helpdesk.ticket",
        "fields": ["id", "name", "partner_id", "state", "priority", "team_id"],
        "description": "Customer support tickets.",
    },
]

PII_FIELDS = {"email", "phone", "mobile", "vat", "x_custom_npwp", "x_custom_nik"}


class AiNlqSession(models.Model):
    _name = "ai.nlq.session"
    _description = "NLQ Chat Session"
    _inherit = ["pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(default="New", readonly=True)
    user_id = fields.Many2one("res.users", default=lambda s: s.env.user, required=True, index=True)
    message_ids = fields.One2many("ai.nlq.message", "session_id")
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company)

    def _pdp_audit_classification(self):
        return "internal"

    @api.model
    def open_or_create_for_user(self):
        rec = self.sudo().search(
            [("user_id", "=", self.env.user.id)],
            order="create_date desc",
            limit=1,
        )
        if not rec:
            rec = self.sudo().create({"name": f"NLQ {self.env.user.login}"})
        return rec

    # -----------------------------------------------------------------

    def _allowed_schema_for_user(self) -> list[dict[str, Any]]:
        """Mask PII fields when the user lacks the view-PII group."""
        try:
            can_pii = self.env.user.has_group("custom_pdp_masking.group_view_pii")
        except Exception:
            can_pii = False
        if can_pii:
            return ALLOWED_SCHEMA
        masked = []
        for s in ALLOWED_SCHEMA:
            masked.append(
                {
                    "model": s["model"],
                    "fields": [f for f in s["fields"] if f not in PII_FIELDS],
                    "description": s.get("description"),
                }
            )
        return masked

    def ask(self, question: str) -> "models.Model":
        """Send a question, run the AI plan, persist result."""
        self.ensure_one()
        AI = self.env["custom.ai"].sudo()
        Message = self.env["ai.nlq.message"].sudo()

        # Persist the user message
        Message.create(
            {
                "session_id": self.id,
                "role": "user",
                "content": question,
            }
        )

        try:
            plan = AI._nlq(
                question=question,
                schema_hint=self._allowed_schema_for_user(),
                locale=self.env.user.lang or "id_ID",
                user_can_view_pii=self.env.user.has_group("custom_pdp_masking.group_view_pii"),
            )
        except Exception as e:
            return Message.create(
                {
                    "session_id": self.id,
                    "role": "assistant",
                    "content": f"AI error: {e}",
                    "is_error": True,
                }
            )

        if plan.get("error"):
            return Message.create(
                {
                    "session_id": self.id,
                    "role": "assistant",
                    "content": f"⚠ {plan.get('rationale') or plan['error']}",
                    "is_error": True,
                }
            )

        result_rows = self._execute_plan(plan)
        msg = Message.create(
            {
                "session_id": self.id,
                "role": "assistant",
                "content": plan.get("rationale", ""),
                "plan_json": json.dumps(plan, ensure_ascii=False),
                "result_json": json.dumps(result_rows, ensure_ascii=False, default=str),
            }
        )
        self._pdp_audit_write(
            "nlq_question_answered",
            self.id,
            {"model": plan.get("model"), "row_count": len(result_rows)},
        )
        return msg

    def _execute_plan(self, plan: dict[str, Any]) -> list[dict]:
        """Run the AI-proposed query — strictly read-only + cap limit."""
        model_name = plan.get("model")
        if not model_name or model_name not in self.env:
            return []
        # Whitelist guard: must be in allowed schema
        allowed = {s["model"] for s in self._allowed_schema_for_user()}
        if model_name not in allowed:
            _logger.warning("NLQ rejected: model %s not in allowed schema", model_name)
            return []
        Model = self.env[model_name].sudo()
        domain = plan.get("domain") or []
        # Sanity guard: domain must be a list
        if not isinstance(domain, list):
            return []
        fields_req = plan.get("fields") or ["id", "display_name"]
        # Strip any field not in the allowed list (LLM may hallucinate)
        allowed_fields = next(
            (s["fields"] for s in self._allowed_schema_for_user() if s["model"] == model_name),
            [],
        )
        fields_req = [f for f in fields_req if f in allowed_fields] or ["id", "display_name"]
        limit = min(int(plan.get("limit") or 25), 100)
        order = plan.get("order") or None
        try:
            return Model.search_read(domain, fields_req, limit=limit, order=order)
        except Exception as e:
            _logger.warning("NLQ execute failed: %s", e)
            return []
