# -*- coding: utf-8 -*-
"""GitHub / GitLab webhook receivers for dev.cycle.pr."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)


PARAM_GITHUB_SECRET = "dev_cycle.github_webhook_secret"
PARAM_GITLAB_SECRET = "dev_cycle.gitlab_webhook_secret"


def _get_param(env, key):
    return env["ir.config_parameter"].sudo().get_param(key, default="") or ""


def _json_response(payload, status=200):
    return request.make_json_response(payload, status=status)


def _resolve_cycle(env, pr_url, branch_name=None):
    """Find dev.cycle matching the PR.

    Strategy:
    1. Existing dev.cycle.pr row with same pr_url → reuse its cycle.
    2. Match by branch_name on dev.cycle.
    Returns ``dev.cycle`` recordset (possibly empty).
    """
    Pr = env["dev.cycle.pr"].sudo()
    existing = Pr.search([("pr_url", "=", pr_url)], limit=1)
    if existing:
        return existing.cycle_id
    if branch_name:
        Cycle = env["dev.cycle"].sudo()
        c = Cycle.search([("branch_name", "=", branch_name)], limit=1)
        if c:
            return c
    return env["dev.cycle"].sudo().browse()


class DevCycleWebhook(http.Controller):
    # ------------------------------------------------------------------
    # GitHub
    # ------------------------------------------------------------------

    @http.route(
        "/devcycle/webhook/github",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def github_webhook(self, **kw):
        env = request.env
        raw = request.httprequest.get_data() or b""
        secret = _get_param(env, PARAM_GITHUB_SECRET)
        sig_header = request.httprequest.headers.get("X-Hub-Signature-256", "")
        if not secret:
            _logger.warning("dev_cycle: GitHub webhook secret not configured")
            # Return 200 so GitHub does not flag the webhook as failing + retry
            # storm. UAT-friendly hint tells the operator what to configure.
            return _json_response(
                {
                    "ok": False,
                    "ignored": True,
                    "reason": (
                        "github webhook secret not configured — set dev_cycle.github_webhook_secret in Settings"
                    ),
                },
                status=200,
            )
        if not sig_header.startswith("sha256="):
            return _json_response({"status": "error", "reason": "missing_signature"}, status=401)
        expected = "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig_header):
            return _json_response({"status": "error", "reason": "bad_signature"}, status=401)

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return _json_response({"status": "error", "reason": "bad_json"}, status=400)

        event = request.httprequest.headers.get("X-GitHub-Event", "")
        return _json_response(self._handle_github_event(env, event, payload))

    def _handle_github_event(self, env, event, payload):
        if event == "pull_request":
            return self._handle_github_pr(env, payload)
        if event == "pull_request_review":
            return self._handle_github_review(env, payload)
        if event == "check_run":
            return self._handle_github_check_run(env, payload)
        return {"status": "ignored", "event": event}

    def _handle_github_pr(self, env, payload):
        action = payload.get("action") or ""
        pr = payload.get("pull_request") or {}
        pr_url = pr.get("html_url") or ""
        pr_number = pr.get("number") or 0
        branch = (pr.get("head") or {}).get("ref")
        cycle = _resolve_cycle(env, pr_url, branch)
        if not cycle:
            _logger.info("dev_cycle: no cycle for PR %s (branch=%s)", pr_url, branch)
            return {"status": "no_cycle"}

        if pr.get("merged"):
            state = "merged"
        elif pr.get("draft"):
            state = "draft"
        elif pr.get("state") == "closed":
            state = "closed"
        else:
            state = "open"

        merged_at_raw = pr.get("merged_at")
        merged_by = (pr.get("merged_by") or {}).get("login") or False
        reviewers = ",".join(r.get("login") for r in (pr.get("requested_reviewers") or []) if r.get("login"))
        vals = {
            "pr_number": pr_number,
            "state": state,
            "reviewers": reviewers or False,
            "merged_at": merged_at_raw and merged_at_raw.replace("T", " ").rstrip("Z") or False,
            "merged_by": merged_by,
        }
        pr_rec = env["dev.cycle.pr"].sudo().upsert_from_webhook(cycle, "github", pr_url, vals)
        cycle.sudo().message_post(body=f"GitHub PR webhook: action={action} state={state} url={pr_url}")
        return {"status": "ok", "pr_id": pr_rec.id, "cycle_id": cycle.id}

    def _handle_github_review(self, env, payload):
        pr = payload.get("pull_request") or {}
        pr_url = pr.get("html_url") or ""
        branch = (pr.get("head") or {}).get("ref")
        cycle = _resolve_cycle(env, pr_url, branch)
        if not cycle:
            return {"status": "no_cycle"}
        review_state = (payload.get("review") or {}).get("state") or ""
        cycle.sudo().message_post(body=f"GitHub review on PR {pr_url}: {review_state}")
        return {"status": "ok"}

    def _handle_github_check_run(self, env, payload):
        check = payload.get("check_run") or {}
        pull_requests = check.get("pull_requests") or []
        if not pull_requests:
            return {"status": "no_pr"}
        # Best-effort: GitHub check_run includes PR ref id; we match by URL pattern.
        # The webhook payload doesn't always include html_url for the PR — fall back to API URL.
        # Map GitHub conclusion → ci_status.
        conclusion = (check.get("conclusion") or "").lower()
        status = (check.get("status") or "").lower()
        if status != "completed":
            ci = "pending"
        elif conclusion == "success":
            ci = "success"
        elif conclusion in ("failure", "timed_out", "cancelled", "action_required"):
            ci = "failure"
        else:
            ci = "error"

        results = []
        for pr_ref in pull_requests:
            pr_url = pr_ref.get("html_url") or pr_ref.get("url") or ""
            cycle = _resolve_cycle(env, pr_url)
            if not cycle:
                continue
            pr_rec = env["dev.cycle.pr"].sudo().upsert_from_webhook(cycle, "github", pr_url, {"ci_status": ci})
            cycle.sudo().message_post(body=f"GitHub check_run: status={status} conclusion={conclusion} → ci={ci}")
            results.append({"pr_id": pr_rec.id, "cycle_id": cycle.id})
        return {"status": "ok", "results": results}

    # ------------------------------------------------------------------
    # GitLab
    # ------------------------------------------------------------------

    @http.route(
        "/devcycle/webhook/gitlab",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def gitlab_webhook(self, **kw):
        env = request.env
        secret = _get_param(env, PARAM_GITLAB_SECRET)
        token = request.httprequest.headers.get("X-Gitlab-Token", "")
        if not secret:
            _logger.warning("dev_cycle: GitLab webhook secret not configured")
            # 200 with explanatory body — see github_webhook for rationale.
            return _json_response(
                {
                    "ok": False,
                    "ignored": True,
                    "reason": (
                        "gitlab webhook secret not configured — set dev_cycle.gitlab_webhook_secret in Settings"
                    ),
                },
                status=200,
            )
        if not hmac.compare_digest(secret, token):
            return _json_response({"status": "error", "reason": "bad_token"}, status=401)

        raw = request.httprequest.get_data() or b""
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except ValueError:
            return _json_response({"status": "error", "reason": "bad_json"}, status=400)

        event = request.httprequest.headers.get("X-Gitlab-Event", "")
        return _json_response(self._handle_gitlab_event(env, event, payload))

    def _handle_gitlab_event(self, env, event, payload):
        if event == "Merge Request Hook":
            return self._handle_gitlab_mr(env, payload)
        if event == "Pipeline Hook":
            return self._handle_gitlab_pipeline(env, payload)
        return {"status": "ignored", "event": event}

    def _handle_gitlab_mr(self, env, payload):
        attrs = payload.get("object_attributes") or {}
        pr_url = attrs.get("url") or ""
        pr_number = attrs.get("iid") or 0
        branch = attrs.get("source_branch")
        cycle = _resolve_cycle(env, pr_url, branch)
        if not cycle:
            return {"status": "no_cycle"}

        gl_state = (attrs.get("state") or "").lower()
        if gl_state == "merged":
            state = "merged"
        elif gl_state == "closed":
            state = "closed"
        elif attrs.get("work_in_progress") or attrs.get("draft"):
            state = "draft"
        else:
            state = "open"

        merged_at = attrs.get("merged_at")
        if merged_at:
            merged_at = merged_at.replace("T", " ").rstrip("Z")
        vals = {
            "pr_number": pr_number,
            "state": state,
            "merged_at": merged_at or False,
        }
        pr_rec = env["dev.cycle.pr"].sudo().upsert_from_webhook(cycle, "gitlab", pr_url, vals)
        cycle.sudo().message_post(body=f"GitLab MR webhook: state={state} url={pr_url}")
        return {"status": "ok", "pr_id": pr_rec.id, "cycle_id": cycle.id}

    def _handle_gitlab_pipeline(self, env, payload):
        attrs = payload.get("object_attributes") or {}
        mr = payload.get("merge_request") or {}
        pr_url = mr.get("url") or ""
        branch = attrs.get("ref")
        if not pr_url:
            return {"status": "no_pr"}
        cycle = _resolve_cycle(env, pr_url, branch)
        if not cycle:
            return {"status": "no_cycle"}
        status = (attrs.get("status") or "").lower()
        if status in ("success",):
            ci = "success"
        elif status in ("failed",):
            ci = "failure"
        elif status in ("canceled", "skipped"):
            ci = "error"
        else:
            ci = "pending"
        pr_rec = env["dev.cycle.pr"].sudo().upsert_from_webhook(cycle, "gitlab", pr_url, {"ci_status": ci})
        cycle.sudo().message_post(body=f"GitLab pipeline: status={status} → ci={ci}")
        return {"status": "ok", "pr_id": pr_rec.id, "cycle_id": cycle.id}
