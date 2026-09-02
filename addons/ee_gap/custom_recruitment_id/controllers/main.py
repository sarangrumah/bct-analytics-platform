# -*- coding: utf-8 -*-
"""Webhook intake controller for inbound applications from job boards.

Endpoint: ``POST /custom_recruitment_id/webhook/<source>``

* ``source`` is one of: jobstreet, glints, linkedin, kalibrr, direct
* Body is JSON in the vendor-specific shape (see
  models/custom_recruitment_webhook_log.py for normalization rules).
* Authenticity is verified via the ``X-Signature`` header which must equal
  ``HMAC_SHA256(secret, raw_body).hexdigest()`` where ``secret`` comes
  from the system parameter
  ``custom_recruitment_id.webhook_secret_<source>``.

Responses:
- 200 on accepted / logged
- 401 on missing or bad signature
- 400 on bad JSON
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


_ALLOWED_SOURCES = ("jobstreet", "glints", "linkedin", "kalibrr", "direct")


def _verify_signature(source: str, raw_body: bytes, provided: str) -> bool:
    if not provided:
        return False
    secret = request.env["ir.config_parameter"].sudo().get_param("custom_recruitment_id.webhook_secret_%s" % source, "")
    if not secret:
        # Without a configured secret, reject — fail closed.
        # nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure - logs source name, never the secret value
        _logger.warning(
            "custom_recruitment_id: missing webhook secret for source=%s",
            source,
        )
        return False
    digest = hmac.new(
        secret.encode("utf-8"),
        raw_body or b"",
        hashlib.sha256,
    ).hexdigest()
    # Accept either "sha256=<hex>" or plain hex.
    provided = provided.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1].strip()
    return hmac.compare_digest(digest, provided.lower())


class CustomRecruitmentWebhookController(http.Controller):
    @http.route(
        "/custom_recruitment_id/webhook/<string:source>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def webhook(self, source, **_kw):
        source = (source or "").lower()
        if source not in _ALLOWED_SOURCES:
            return request.make_response("unknown source", status=404)

        raw = request.httprequest.get_data() or b""
        provided = request.httprequest.headers.get("X-Signature", "")
        if not _verify_signature(source, raw, provided):
            _logger.warning(
                "custom_recruitment_id: signature mismatch for source=%s",
                source,
            )
            return request.make_response("unauthorized", status=401)

        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError) as exc:
            _logger.warning(
                "custom_recruitment_id: bad JSON for source=%s: %s",
                source,
                exc,
            )
            return request.make_response("bad json", status=400)

        try:
            request.env["custom.recruitment.webhook.log"].sudo().ingest_payload(
                source,
                data,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "custom_recruitment_id: ingest failed for source=%s: %s",
                source,
                exc,
            )
            return request.make_response("ingest error", status=500)

        return request.make_response("ok", status=200)
