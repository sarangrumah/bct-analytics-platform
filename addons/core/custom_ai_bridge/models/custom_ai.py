# -*- coding: utf-8 -*-
"""AI gateway client (HMAC-signed) as an Odoo service.

Usage from downstream modules::

    result = self.env["custom.ai"]._chat(
        messages=[{"role": "user", "content": "..."}],
        system="...",
        quality="fast",
    )

    rec = self.env["custom.ai"]._recommend(
        model="helpdesk.ticket",
        res_id=ticket.id,
        payload=ticket._custom_ai_payload(),
    )
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from odoo import api, models
from odoo.exceptions import UserError

try:
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore

_logger = logging.getLogger(__name__)


def _gateway_url() -> str:
    return os.environ.get("AI_GATEWAY_URL", "http://ai-gateway:8080").rstrip("/")


class CustomAI(models.AbstractModel):
    _name = "custom.ai"
    _description = "AI Gateway client"

    # ---------- public API ----------

    @api.model
    def _enabled(self) -> bool:
        return self.env["ir.config_parameter"].sudo().get_param("custom_ai.enabled", "True") == "True"

    @api.model
    def _chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        model: str | None = None,
        quality: str = "fast",
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        body = {
            "messages": messages,
            "system": system,
            "model": model,
            "quality": quality,
            "tools": tools,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "cache_system": True,
        }
        return self._call("/v1/chat", body)

    @api.model
    def _recommend(
        self,
        model: str,
        res_id: int,
        payload: dict[str, Any],
        history: list[dict[str, Any]] | None = None,
        locale: str = "id_ID",
    ) -> dict[str, Any]:
        body = {
            "model": model,
            "res_id": res_id,
            "payload": payload,
            "history": history,
            "locale": locale,
        }
        return self._call("/v1/workflow/recommend", body)

    # ---------- internals ----------

    @api.model
    def _call(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self._enabled():
            raise UserError("AI is disabled. Enable it in Settings > Custom Platform > AI Intelligence.")
        if httpx is None:
            raise UserError("httpx not installed in this Odoo runtime")
        raw = json.dumps(body, separators=(",", ":"), default=str).encode()
        header, _ts = self.env["custom.security"].sign_payload(raw)
        url = f"{_gateway_url()}{path}"
        tenant = self.env.cr.dbname
        try:
            # 5-minute read timeout: Opus 4.7 with 16k output tokens can take
            # 2-3 minutes per batch. Connect/write stay short to surface real
            # network failures quickly.
            timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
            with httpx.Client(timeout=timeout) as c:
                r = c.post(
                    url,
                    content=raw,
                    headers={
                        "Content-Type": "application/json",
                        "X-Custom-Signature": header,
                        "X-Tenant-Id": tenant,
                    },
                )
        except httpx.HTTPError as e:
            _logger.error("custom.ai: gateway call failed: %s", e)
            raise UserError(f"AI gateway unreachable: {e}") from e

        if r.status_code != 200:
            _logger.warning("custom.ai: gateway %s returned %s: %s", path, r.status_code, r.text[:500])
            raise UserError(f"AI gateway error {r.status_code}: {r.text[:200]}")
        return r.json()
