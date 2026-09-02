# -*- coding: utf-8 -*-
"""Machine-to-machine endpoints.

Used by the BFF for things that must not require a human session: a brand PIC clicking the
verification link in a WhatsApp message, and the BFF reporting back what it managed to
deliver.
"""

import logging

from odoo import SUPERUSER_ID, fields, http
from odoo.http import request

from .http_helpers import err, json_body, ok, verify_hmac

_logger = logging.getLogger(__name__)


class PmoHmacApi(http.Controller):
    @http.route("/pmo/api/hmac/ping", type="http", auth="none", methods=["POST"], csrf=False, save_session=False)
    def ping(self, **kw):
        failure = verify_hmac()
        if failure:
            return failure
        return ok({"pong": True, "db": request.env.cr.dbname})

    @http.route(
        "/pmo/api/hmac/tasks/<int:task_id>/verify",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def task_verify(self, task_id, **kw):
        """The brand says the work is good.

        ``auth='none'`` routes run without ``env.user`` in Odoo 19, so every write here is
        explicitly attributed to the superuser and the reason names the brand contact --
        otherwise the audit trail would show an anonymous close.
        """
        failure = verify_hmac()
        if failure:
            return failure
        body = json_body()
        env = request.env(user=SUPERUSER_ID)
        task = env["project.task"].browse(task_id)
        if not task.exists():
            return err("NOT_FOUND", "No such task", status=404)
        if not task.stage_id.custom_is_waiting_user:
            return err(
                "NOT_WAITING",
                "This task is not waiting for user verification",
                status=409,
            )
        verdict = (body.get("verdict") or "accept").lower()
        actor = body.get("actor") or task.custom_verification_owner_id.name or "brand PIC"

        if verdict == "accept":
            done = env["project.task.type"]._stage_by_code("done")
            if not done:
                return err("NO_STAGE", "No closing stage configured", status=503)
            task.write({"stage_id": done.id})
            task._pdp_audit_write(
                "verify_done",
                task.id,
                None,
                reason=f"Verified by {actor} via WhatsApp link",
            )
            return ok({"stage": done.custom_code, "verified_by": actor})

        # Rejected: back to UAT, and the team is told why.
        uat = env["project.task.type"]._stage_by_code("uat")
        note = body.get("note") or ""
        task.write({"stage_id": uat.id if uat else task.stage_id.id})
        task._pdp_audit_write(
            "verify_rejected",
            task.id,
            None,
            reason=f"Rejected by {actor}: {note}"[:500],
        )
        task._pmo_notify_event("stage_changed", extra={"verify_rejected_note": note})
        return ok({"stage": uat.custom_code if uat else task.stage_id.custom_code, "rejected_by": actor})

    @http.route(
        "/pmo/api/hmac/notify-result", type="http", auth="none", methods=["POST"], csrf=False, save_session=False
    )
    def notify_result(self, **kw):
        """BFF reports a late delivery outcome (e.g. a WhatsApp status callback)."""
        failure = verify_hmac()
        if failure:
            return failure
        body = json_body()
        env = request.env(user=SUPERUSER_ID)
        outbox_id = body.get("outbox_id")
        results = body.get("results") or []
        if not results:
            return err("EMPTY", "No results in payload", status=400)
        log_model = env["custom.project.notify.log"]
        outbox = (
            env["custom.project.notify.outbox"].browse(int(outbox_id))
            if outbox_id
            else env["custom.project.notify.outbox"]
        )
        created = 0
        for item in results:
            log_model.create(
                {
                    "outbox_id": outbox.id if outbox.exists() else False,
                    "event": body.get("event") or (outbox.event if outbox.exists() else "unknown"),
                    "res_model": body.get("model") or (outbox.res_model if outbox.exists() else "unknown"),
                    "res_id": int(body.get("id") or (outbox.res_id if outbox.exists() else 0)),
                    "res_label": outbox.res_label if outbox.exists() else body.get("label"),
                    "channel": item.get("channel") or "wa",
                    "transport": item.get("transport"),
                    "recipient_kind": item.get("kind"),
                    "recipient_name": item.get("name"),
                    "recipient_email": item.get("email"),
                    "recipient_phone_masked": log_model.mask_phone(item.get("phone")),
                    "success": bool(item.get("success")),
                    "skipped_reason": item.get("skipped"),
                    "error_message": (item.get("error") or "")[:200] or False,
                    "sent_at": fields.Datetime.now(),
                }
            )
            created += 1
        return ok({"recorded": created})
