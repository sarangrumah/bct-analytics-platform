# -*- coding: utf-8 -*-
"""Extra ai-gateway calls (anomaly / classify / nlq) added to custom.ai service."""

from __future__ import annotations

from typing import Any

from odoo import api, models


class CustomAIExtra(models.AbstractModel):
    _inherit = "custom.ai"

    @api.model
    def _detect_anomaly(
        self,
        model: str,
        res_id: int,
        metric: str,
        latest_value: float,
        history: list[float],
        context: dict[str, Any] | None = None,
        locale: str = "id_ID",
    ) -> dict[str, Any]:
        return self._call(
            "/v1/workflow/anomaly",
            {
                "model": model,
                "res_id": res_id,
                "metric": metric,
                "latest_value": float(latest_value),
                "history": [float(v) for v in history],
                "context": context or {},
                "locale": locale,
            },
        )

    @api.model
    def _classify_document(
        self,
        filename: str,
        mimetype: str | None = None,
        text_excerpt: str | None = None,
        locale: str = "id_ID",
    ) -> dict[str, Any]:
        return self._call(
            "/v1/workflow/classify-document",
            {
                "filename": filename,
                "mimetype": mimetype,
                "text_excerpt": text_excerpt,
                "locale": locale,
            },
        )

    @api.model
    def _nlq(
        self,
        question: str,
        schema_hint: list[dict[str, Any]],
        locale: str = "id_ID",
        user_can_view_pii: bool = False,
    ) -> dict[str, Any]:
        return self._call(
            "/v1/workflow/nlq",
            {
                "question": question,
                "schema_hint": schema_hint,
                "locale": locale,
                "user_can_view_pii": user_can_view_pii,
            },
        )
