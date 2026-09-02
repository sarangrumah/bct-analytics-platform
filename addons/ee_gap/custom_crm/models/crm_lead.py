# -*- coding: utf-8 -*-
import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class CrmLead(models.Model):
    _inherit = "crm.lead"

    # ---------- existing AI scoring fields ----------

    x_ai_score = fields.Float(
        string="AI Score",
        help="AI-computed lead score (0.0 - 1.0) reflecting likelihood to convert.",
        tracking=True,
    )
    x_ai_reasoning = fields.Text(
        string="AI Reasoning",
        help="Explanation provided by the AI model for the computed score.",
    )
    x_whatsapp_number = fields.Char(
        string="WhatsApp Number",
        help="WhatsApp contact number (E.164 format recommended, e.g. +6281234567890).",
        tracking=True,
    )
    x_ai_scored_date = fields.Datetime(
        string="AI Scored On",
        readonly=True,
    )

    # ---------- predictive lead scoring (rules-based, EE-equivalent) ----------

    x_predictive_score = fields.Float(
        string="Predictive Score",
        compute="_compute_predictive_score",
        store=True,
        help="Rules-based predictive score (0-100). Heuristic stand-in for Odoo EE Predictive Lead Scoring.",
    )

    # ---------- lead enrichment (stub) ----------

    x_enrichment_data = fields.Text(
        string="Enrichment Data (JSON)",
        help="Raw enrichment payload returned by the AI bridge / mock enrichment.",
    )
    x_enriched_at = fields.Datetime(
        string="Enriched On",
        readonly=True,
    )

    # ---------- predictive score compute ----------

    @api.depends(
        "email_from",
        "phone",
        "partner_id",
        "source_id",
        "medium_id",
        "country_id",
    )
    def _compute_predictive_score(self):
        # historical win rate per source — cached per recordset compute call
        Lead = self.env["crm.lead"].sudo()
        source_winrate_cache: dict[int, float] = {}

        def _source_winrate(source_id: int) -> float:
            if not source_id:
                return 0.0
            if source_id in source_winrate_cache:
                return source_winrate_cache[source_id]
            total = Lead.search_count([("source_id", "=", source_id)])
            if not total:
                source_winrate_cache[source_id] = 0.0
                return 0.0
            won = Lead.search_count(
                [
                    ("source_id", "=", source_id),
                    ("won_status", "=", "won"),
                ]
            )
            rate = won / total
            source_winrate_cache[source_id] = rate
            return rate

        for rec in self:
            score = 30.0
            if rec.email_from:
                score += 10.0
            if rec.phone:
                score += 10.0
            if rec.partner_id:
                score += 10.0
            if rec.source_id:
                score += 10.0
            if rec.medium_id:
                score += 10.0
            if rec.country_id:
                score += 10.0
            # source historical winrate boost
            if rec.source_id and _source_winrate(rec.source_id.id) > 0.5:
                score += 20.0
            rec.x_predictive_score = max(0.0, min(100.0, score))

    # ---------- owner change audit (PDP) ----------

    def write(self, vals):
        old_owners = {}
        if "user_id" in vals:
            old_owners = {r.id: r.user_id.id for r in self}
        res = super().write(vals)
        if "user_id" in vals:
            for rec in self:
                old = old_owners.get(rec.id)
                new = rec.user_id.id
                if old != new:
                    rec._pdp_audit_owner_change(old, new)
        return res

    def _pdp_audit_owner_change(self, old_user_id, new_user_id):
        try:
            user = self.env.user
            payload = {
                "old_user_id": old_user_id,
                "new_user_id": new_user_id,
                "lead_name": self.name,
            }
            self.env.cr.execute(
                """
                INSERT INTO pdp.audit_log (
                    actor_user_id, actor_login, tenant_db,
                    model_name, res_id, action,
                    field_changes, classification
                ) VALUES (%s, %s, %s, %s, %s, 'write', %s::jsonb, 'internal')
                """,
                (
                    user.id if user else None,
                    user.login if user else None,
                    self.env.cr.dbname,
                    self._name,
                    self.id,
                    json.dumps(payload),
                ),
            )
        except Exception as e:  # pragma: no cover
            _logger.warning("crm.lead owner audit log failed: %s", e)

    # ---------- AI scoring ----------

    def _custom_ai_payload(self):
        self.ensure_one()
        return {
            "lead_name": self.name or "",
            "partner": self.partner_name or (self.partner_id.name if self.partner_id else ""),
            "email": self.email_from or "",
            "phone": self.phone or "",
            "whatsapp": self.x_whatsapp_number or "",
            "expected_revenue": self.expected_revenue or 0.0,
            "probability": self.probability or 0.0,
            "stage": self.stage_id.name if self.stage_id else "",
            "description": (self.description or "")[:4000],
            "source": self.source_id.name if self.source_id else "",
            "medium": self.medium_id.name if self.medium_id else "",
            "country": self.country_id.name if self.country_id else "",
            "predictive_score": self.x_predictive_score or 0.0,
        }

    def action_ai_score_lead(self):
        self.ensure_one()
        try:
            result = self.env["custom.ai"]._recommend(
                model="crm.lead",
                res_id=self.id,
                payload=self._custom_ai_payload(),
            )
        except Exception as e:
            _logger.error("AI lead scoring failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }

        score = 0.0
        try:
            raw_score = result.get("score") if isinstance(result, dict) else None
            if raw_score is not None:
                score = float(raw_score)
        except (TypeError, ValueError):
            score = 0.0

        reasoning = (
            (result.get("reasoning") if isinstance(result, dict) else None)
            or (result.get("summary") if isinstance(result, dict) else None)
            or (result.get("text") if isinstance(result, dict) else None)
            or json.dumps(result)[:1000]
        )

        self.write(
            {
                "x_ai_score": score,
                "x_ai_reasoning": reasoning,
                "x_ai_scored_date": fields.Datetime.now(),
            }
        )
        self.message_post(
            body=_("<b>AI Lead Score</b>: %(score).2f<br/>%(reason)s")
            % {
                "score": score,
                "reason": reasoning,
            },
            author_id=self.env.ref("base.partner_root").id,
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ---------- lead enrichment (stub via custom.ai or mock) ----------

    def _enrichment_payload(self):
        self.ensure_one()
        return {
            "lead_name": self.name or "",
            "email": self.email_from or "",
            "phone": self.phone or "",
            "partner_id": self.partner_id.id if self.partner_id else False,
            "partner_name": self.partner_id.name if self.partner_id else "",
            "website": self.website or "",
            "country": self.country_id.name if self.country_id else "",
        }

    def action_enrich_lead(self):
        """Mock enrichment that calls custom.ai when available, otherwise returns a deterministic mock."""
        self.ensure_one()
        enrichment: dict = {}
        try:
            result = self.env["custom.ai"]._recommend(
                model="crm.lead",
                res_id=self.id,
                payload=self._enrichment_payload(),
            )
            if isinstance(result, dict):
                enrichment = result
        except Exception as e:
            _logger.info("Lead enrichment AI bridge unavailable, using mock. (%s)", e)
            enrichment = {
                "source": "mock",
                "industry": "Technology",
                "employees": "11-50",
                "website": self.website
                or f"https://www.example.com/{(self.partner_name or 'lead').lower().replace(' ', '-')}",
                "linkedin": f"https://www.linkedin.com/company/{(self.partner_name or 'lead').lower().replace(' ', '-')}",
            }

        self.write(
            {
                "x_enrichment_data": json.dumps(enrichment, default=str)[:8000],
                "x_enriched_at": fields.Datetime.now(),
            }
        )

        # Optionally enrich the linked partner with mock metadata.
        if self.partner_id:
            partner_vals = {}
            if not self.partner_id.website and enrichment.get("website"):
                partner_vals["website"] = enrichment["website"]
            if partner_vals:
                self.partner_id.sudo().write(partner_vals)

        self.message_post(
            body=_("<b>Lead Enriched</b><br/><pre>%s</pre>") % json.dumps(enrichment, indent=2, default=str)[:2000],
            subtype_xmlid="mail.mt_note",
        )
        return True
