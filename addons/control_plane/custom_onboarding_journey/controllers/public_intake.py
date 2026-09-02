# -*- coding: utf-8 -*-
"""Public-facing controllers for onboarding intake + status polling.

Both endpoints are unauthenticated. The intake endpoint is rate-limited per
IP and (optionally) validated via Cloudflare Turnstile when the secret is
configured. The status endpoint exposes only non-sensitive fields.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Process-local cache of (ip_hash -> [timestamps]) for rate limiting.
# Good enough for a single-worker dev install; production should use Redis,
# but per plan this is allowed to be a soft feature flag.
_RATE_BUCKET: dict[str, list[float]] = {}


def _hash_ip(ip: str | None) -> str:
    if not ip:
        return ""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()


def _rate_limited(ip_hash: str, per_hour: int) -> bool:
    if per_hour <= 0:
        return False
    now = time.time()
    window = 3600.0
    bucket = _RATE_BUCKET.setdefault(ip_hash, [])
    # Prune
    cutoff = now - window
    bucket[:] = [t for t in bucket if t >= cutoff]
    if len(bucket) >= per_hour:
        return True
    bucket.append(now)
    return False


def _verify_turnstile(secret: str, token: str, remote_ip: str | None) -> bool:
    """Best-effort verification against Cloudflare Turnstile. Soft-fails."""
    if not secret:
        return True  # feature disabled
    if not token:
        _logger.warning("turnstile: no token in payload; rejecting")
        return False
    try:
        import requests  # type: ignore
    except ImportError:
        _logger.warning("turnstile: 'requests' not installed; skipping verification")
        return True
    try:
        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={"secret": secret, "response": token, "remoteip": remote_ip or ""},
            timeout=5,
        )
        return bool(resp.json().get("success"))
    except Exception as exc:
        _logger.warning("turnstile: verification call failed (%s); soft-accepting", exc)
        return True


class OnboardingPublicIntake(http.Controller):
    @http.route(
        "/onboarding/public/intake",
        type="json",
        auth="public",
        csrf=False,
        methods=["POST"],
    )
    def public_intake(self, **payload):
        # JSON-RPC unwraps kwargs into ``payload``.
        ICP = request.env["ir.config_parameter"].sudo()
        per_hour = int(ICP.get_param("onboarding.rate_limit_per_ip_per_hour", "5") or "5")
        turnstile_secret = ICP.get_param("onboarding.turnstile.secret", "") or ""

        remote_ip = request.httprequest.remote_addr if request.httprequest else None
        ip_hash = _hash_ip(remote_ip)

        if _rate_limited(ip_hash, per_hour):
            return {"error": "rate_limited", "retry_after_seconds": 3600}

        turnstile_token = payload.pop("turnstile_token", None)
        if not _verify_turnstile(turnstile_secret, turnstile_token, remote_ip):
            return {"error": "turnstile_failed"}

        # Persist the raw payload (whatever was sent — caller responsibility).
        submission = (
            request.env["onboarding.public.submission"]
            .sudo()
            .create(
                {
                    "raw_payload_json": json.dumps(payload, ensure_ascii=False, default=str),
                    "source_ip_hash": ip_hash or False,
                }
            )
        )

        base_url = ICP.get_param("web.base.url", "")
        return {
            "token": submission.public_token,
            "status_url": f"{base_url}/onboarding/public/status/{submission.public_token}",
        }

    @http.route(
        "/onboarding/public/status/<string:token>",
        type="http",
        auth="public",
        csrf=False,
        methods=["GET"],
    )
    def public_status(self, token, **_kwargs):
        Journey = request.env["onboarding.journey"].sudo()
        # First try: journey directly tracking this token.
        journey = Journey.search([("public_status_token", "=", token)], limit=1)
        # Fallback: token belongs to a submission that was promoted.
        if not journey:
            sub = (
                request.env["onboarding.public.submission"]
                .sudo()
                .search(
                    [("public_token", "=", token)],
                    limit=1,
                )
            )
            if sub and sub.journey_id:
                journey = sub.journey_id

        if not journey:
            body = json.dumps({"error": "not_found"})
            return request.make_response(body, headers=[("Content-Type", "application/json")], status=404)

        body = json.dumps(
            {
                "stage": journey.stage,
                "target_go_live": journey.target_go_live.isoformat() if journey.target_go_live else None,
                "progress_pct": journey.progress_pct,
                "last_update": journey.write_date.isoformat() if journey.write_date else None,
            }
        )
        return request.make_response(body, headers=[("Content-Type", "application/json")])
