# -*- coding: utf-8 -*-
"""Master-data endpoints — the CMS surface.

Everything a PO Lead might need to change without a deploy lives here: brand verticals,
stages and their SLA clock, notification rules, and who has which role. Writes are
field-allow-listed and gated on the admin group, and every change lands in the audit trail
as ``master_data_change`` because master data is the most dangerous place for an untracked
edit.
"""

import logging

from odoo import http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .http_helpers import err, json_body, ok

_logger = logging.getLogger(__name__)

ADMIN_GROUP = "custom_project_portfolio.group_pmo_admin"

VERTICAL_WRITABLE = {
    "name",
    "code",
    "legal_entity",
    "brand_group",
    "vertical_po_id",
    "ba_ids",
    "pic_partner_ids",
    "color",
    "sequence",
    "active",
}
STAGE_WRITABLE = {
    "name",
    "custom_code",
    "sequence",
    "custom_applies_to",
    "custom_sla_clock",
    "custom_is_hold",
    "custom_is_waiting_user",
    "custom_is_closed_stage",
    "custom_auto_close_days",
    "custom_require_reason",
    "fold",
}
RULE_WRITABLE = {
    "event",
    "recipient_kind",
    "role_group_id",
    "channel_wa",
    "channel_email",
    "channel_odoo",
    "sequence",
    "active",
}


def _guard(handler):
    """Turn business-rule rejections into 4xx — and roll the cursor back when doing it.

    The rollback is the load-bearing part. A model constraint fires at flush, which is
    *after* the UPDATE has already been issued; catching the exception and returning a
    tidy 422 without rolling back leaves that UPDATE to be committed at the end of the
    request. The caller is told "rejected" while the invalid value is quietly saved.
    """

    def wrapper(*args, **kwargs):
        try:
            if not request.env.user.has_group(ADMIN_GROUP):
                return err(
                    "FORBIDDEN",
                    "Master data is limited to the VAS PMO Administrator group.",
                    status=403,
                )
            return handler(*args, **kwargs)
        except (UserError, ValidationError) as exc:
            request.env.cr.rollback()
            return err("RULE_REJECTED", str(exc), status=422)
        except AccessError as exc:
            request.env.cr.rollback()
            return err("FORBIDDEN", str(exc), status=403)

    wrapper.__name__ = handler.__name__
    wrapper.__doc__ = handler.__doc__
    return wrapper


def _filtered(payload, allowed):
    return {key: value for key, value in payload.items() if key in allowed}


def _audit(record, values):
    record._pdp_audit_write(
        "master_data_change",
        record.id,
        values,
        reason=request.httprequest.headers.get("X-Change-Reason") or None,
    )


class PmoAdminApi(http.Controller):
    # ------------------------------------------------------------- verticals
    @http.route(
        "/pmo/api/admin/verticals", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    @_guard
    def verticals(self, **kw):
        records = request.env["custom.project.vertical"].with_context(active_test=False).search([])
        return ok(
            [
                {
                    "id": v.id,
                    "code": v.code,
                    "name": v.name,
                    "legal_entity": v.legal_entity or "",
                    "brand_group": v.brand_group,
                    "vertical_po": {"id": v.vertical_po_id.id, "name": v.vertical_po_id.name}
                    if v.vertical_po_id
                    else None,
                    "ba": [{"id": u.id, "name": u.name} for u in v.ba_ids],
                    "pic": [{"id": p.id, "name": p.name} for p in v.pic_partner_ids],
                    "color": v.color,
                    "sequence": v.sequence,
                    "active": v.active,
                    "project_count": v.project_count,
                    "task_count": v.task_count,
                }
                for v in records
            ]
        )

    @http.route(
        "/pmo/api/admin/verticals/<int:vertical_id>",
        type="http",
        auth="jwt_pmo",
        methods=["POST", "PATCH"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def vertical_write(self, vertical_id, **kw):
        record = request.env["custom.project.vertical"].with_context(active_test=False).browse(vertical_id)
        if not record.exists():
            return err("NOT_FOUND", "No such vertical", status=404)
        values = _filtered(json_body(), VERTICAL_WRITABLE)
        if not values:
            return err("NOTHING_TO_DO", "No writable field in the payload", status=400)
        record.write(values)
        _audit(record, values)
        return ok({"id": record.id, "code": record.code, "active": record.active})

    @http.route(
        "/pmo/api/admin/verticals", type="http", auth="jwt_pmo", methods=["POST"], csrf=False, save_session=False
    )
    @_guard
    def vertical_create(self, **kw):
        values = _filtered(json_body(), VERTICAL_WRITABLE)
        if not values.get("code") or not values.get("name"):
            return err("MISSING_FIELDS", "A vertical needs a code and a name", status=400)
        record = request.env["custom.project.vertical"].create(values)
        _audit(record, values)
        return ok({"id": record.id, "code": record.code}, status=201)

    # ---------------------------------------------------------------- stages
    @http.route(
        "/pmo/api/admin/stages", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    @_guard
    def stages(self, **kw):
        records = request.env["project.task.type"].search([("custom_code", "!=", False)])
        return ok(
            [
                {
                    "id": s.id,
                    "name": s.name,
                    "code": s.custom_code,
                    "sequence": s.sequence,
                    "applies_to": s.custom_applies_to,
                    "sla_clock": s.custom_sla_clock,
                    "is_hold": s.custom_is_hold,
                    "is_waiting_user": s.custom_is_waiting_user,
                    "is_closed": s.custom_is_closed_stage,
                    "auto_close_days": s.custom_auto_close_days,
                    "require_reason": s.custom_require_reason,
                    "fold": s.fold,
                    "next_stages": [{"id": n.id, "name": n.name} for n in s.custom_next_stage_ids],
                }
                for s in records
            ]
        )

    @http.route(
        "/pmo/api/admin/stages/<int:stage_id>",
        type="http",
        auth="jwt_pmo",
        methods=["POST", "PATCH"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def stage_write(self, stage_id, **kw):
        record = request.env["project.task.type"].browse(stage_id)
        if not record.exists():
            return err("NOT_FOUND", "No such stage", status=404)
        values = _filtered(json_body(), STAGE_WRITABLE)
        if not values:
            return err("NOTHING_TO_DO", "No writable field in the payload", status=400)
        # The model's own constraint keeps the flags and the clock coherent; a bad
        # combination comes back as 422 rather than being silently accepted.
        record.write(values)
        _audit(record, values)
        return ok(
            {
                "id": record.id,
                "code": record.custom_code,
                "sla_clock": record.custom_sla_clock,
                "auto_close_days": record.custom_auto_close_days,
            }
        )

    # --------------------------------------------------------- notify rules
    @http.route(
        "/pmo/api/admin/notify-rules",
        type="http",
        auth="jwt_pmo",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def notify_rules(self, **kw):
        records = request.env["custom.project.notify.rule"].with_context(active_test=False).search([])
        return ok(
            [
                {
                    "id": r.id,
                    "event": r.event,
                    "recipient_kind": r.recipient_kind,
                    "group": r.role_group_id.name or "",
                    "channel_wa": r.channel_wa,
                    "channel_email": r.channel_email,
                    "channel_odoo": r.channel_odoo,
                    "active": r.active,
                }
                for r in records
            ]
        )

    @http.route(
        "/pmo/api/admin/notify-rules/<int:rule_id>",
        type="http",
        auth="jwt_pmo",
        methods=["POST", "PATCH"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def notify_rule_write(self, rule_id, **kw):
        record = request.env["custom.project.notify.rule"].with_context(active_test=False).browse(rule_id)
        if not record.exists():
            return err("NOT_FOUND", "No such rule", status=404)
        values = _filtered(json_body(), RULE_WRITABLE)
        if not values:
            return err("NOTHING_TO_DO", "No writable field in the payload", status=400)
        record.write(values)
        _audit(record, values)
        return ok(
            {
                "id": record.id,
                "event": record.event,
                "channel_wa": record.channel_wa,
                "channel_email": record.channel_email,
                "channel_odoo": record.channel_odoo,
                "active": record.active,
            }
        )

    # ----------------------------------------------------------------- users
    @http.route(
        "/pmo/api/admin/users", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    @_guard
    def users(self, **kw):
        groups = {
            "admin": "custom_project_portfolio.group_pmo_admin",
            "lead": "custom_project_portfolio.group_pmo_lead",
            "po": "custom_project_portfolio.group_pmo_po",
            "ba": "custom_project_portfolio.group_pmo_ba",
            "member": "custom_project_portfolio.group_pmo_user",
            "brand_pic": "custom_project_portfolio.group_pmo_vertical_pic",
        }
        # all_user_ids, not user_ids: in Odoo 19 the latter is direct members only and
        # every role here is reached through implied_ids.
        members = request.env["res.users"].browse([])
        for xmlid in groups.values():
            members |= request.env.ref(xmlid).sudo().all_user_ids
        Log = request.env["custom.project.notify.log"]
        rows = []
        for user in members:
            partner = user.partner_id
            rows.append(
                {
                    "id": user.id,
                    "name": user.name,
                    "login": user.login,
                    "email": user.email or "",
                    # Masked on the way out: this list is readable by every admin, and the
                    # number is PII.
                    "phone_masked": Log.mask_phone(partner.phone or ""),
                    "has_phone": bool(partner.phone),
                    "roles": [key for key, xmlid in groups.items() if user.has_group(xmlid)],
                    "verticals": request.env["custom.project.vertical"]
                    .search(
                        [
                            "|",
                            ("ba_ids", "in", user.id),
                            ("vertical_po_id", "=", user.id),
                        ]
                    )
                    .mapped("code"),
                    "active": user.active,
                }
            )
        return ok(rows)
