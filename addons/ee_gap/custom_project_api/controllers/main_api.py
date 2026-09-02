# -*- coding: utf-8 -*-
"""Read/write surface for the VAS PMO UI.

Everything here runs as the authenticated user, so Odoo's own record rules and the audit
trail apply exactly as they do in the backend. ``user_id`` in a body is never trusted.
"""

import logging

from odoo import fields, http
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.http import request

from .http_helpers import err, json_body, ok

_logger = logging.getLogger(__name__)

TASK_WRITABLE = {
    "name",
    "description",
    "date_deadline",
    "custom_priority",
    "custom_story_points",
    "custom_task_type",
    "custom_due_sla_date",
    "custom_hold_reason",
    "custom_hold_until",
    "depend_on_ids",
    "custom_verification_owner_id",
    "custom_sprint_id",
    "custom_vertical_id",
    "custom_vertical_override",
    "custom_vertical_override_reason",
    "project_id",
    "stage_id",
    "user_ids",
}

WEEKLY_WRITABLE = {"plan_this_week", "blocker", "next_week", "health", "progress_pct"}


def _vertical(record):
    vertical = getattr(record, "custom_vertical_id", None) or getattr(record, "vertical_id", None)
    if not vertical:
        return None
    return {
        "id": vertical.id,
        "code": vertical.code,
        "name": vertical.name,
        "legal_entity": vertical.legal_entity or "",
    }


def _stage(stage):
    if not stage:
        return None
    return {
        "id": stage.id,
        "name": stage.name,
        "code": stage.custom_code or "",
        "sla_clock": stage.custom_sla_clock,
        "is_hold": stage.custom_is_hold,
        "is_waiting_user": stage.custom_is_waiting_user,
        "is_closed": stage.custom_is_closed_stage,
    }


def _task_dict(task):
    return {
        "id": task.id,
        "name": task.name,
        "project": {"id": task.project_id.id, "name": task.project_id.name} if task.project_id else None,
        "vertical": _vertical(task),
        "stage": _stage(task.stage_id),
        "assignees": [{"id": u.id, "name": u.name} for u in task.user_ids],
        "priority": task.custom_priority,
        "task_type": task.custom_task_type,
        "story_points": task.custom_story_points,
        "source": task.custom_source,
        "cr_code": task.custom_cr_code if "custom_cr_code" in task._fields else None,
        "cr_id": task.change_request_id.id if "change_request_id" in task._fields and task.change_request_id else None,
        "sprint": task.custom_sprint_id.week_code or None,
        "deadline": task.date_deadline,
        "sla_due": task.custom_due_sla_date,
        "blocked": task.custom_is_blocked,
        "carried_over": task.custom_carried_over,
        "hold": {
            "reason": task.custom_hold_reason or "",
            "since": task.custom_hold_since,
            "until": task.custom_hold_until,
            "hours": task.custom_hold_duration_hours,
            "expired_notified": task.custom_hold_expired_notified,
        },
        "verification": {
            "owner": task.custom_verification_owner_id.name or "",
            "requested_at": task.custom_verification_requested_at,
            "due": task.custom_verification_due,
            "hours": task.custom_user_wait_hours,
            "reminders": task.custom_verify_reminders_sent,
            "auto_closed": task.custom_auto_closed,
        },
        "cycle_time_team": task.custom_cycle_time_team,
        "lead_time_total": task.custom_lead_time_total,
        "closed_at": task.custom_closed_at,
    }


def _guard(handler):
    """Translate Odoo exceptions into API errors instead of 500s.

    The `cr.rollback()` is not decoration. Some rules only fire once a write has been
    issued -- a hold with no reason is refused inside the post-write hook, after the stage
    change already hit the database. Returning 422 without rolling back would answer
    "rejected" and save the change anyway.
    """

    def wrapper(*args, **kwargs):
        try:
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


class PmoApi(http.Controller):
    # ------------------------------------------------------------------ meta
    @http.route("/pmo/api/health", type="http", auth="public", methods=["GET"], csrf=False, save_session=False)
    def health(self, **kw):
        return ok({"status": "up", "db": request.env.cr.dbname})

    @http.route(
        "/pmo/api/meta/stages", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    def stages(self, **kw):
        applies = kw.get("applies_to") or "task"
        stages = request.env["project.task.type"].search(
            [
                ("custom_code", "!=", False),
                ("custom_applies_to", "in", [applies, "both"]),
            ]
        )
        return ok([_stage(stage) for stage in stages])

    @http.route(
        "/pmo/api/meta/verticals", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    def verticals(self, **kw):
        verticals = request.env["custom.project.vertical"].search([])
        return ok(
            [
                {
                    "id": v.id,
                    "code": v.code,
                    "name": v.name,
                    "legal_entity": v.legal_entity or "",
                    "brand_group": v.brand_group,
                    "color": v.color,
                    "project_count": v.project_count,
                    "task_count": v.task_count,
                }
                for v in verticals
            ]
        )

    # -------------------------------------------------------------- projects
    @http.route("/pmo/api/projects", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False)
    def projects(self, **kw):
        domain = []
        if kw.get("vertical_id"):
            domain.append(("custom_vertical_id", "=", int(kw["vertical_id"])))
        projects = request.env["project.project"].search(domain, order="custom_health desc")
        return ok(
            [
                {
                    "id": p.id,
                    "code": p.custom_code or "",
                    "name": p.name,
                    "vertical": _vertical(p),
                    "portfolio": p.custom_portfolio_id.name or "",
                    "po": p.custom_po_id.name or "",
                    "ba": p.custom_ba_id.name or "",
                    "health": p.custom_health,
                    "health_note": p.custom_health_note or "",
                    "progress": p.custom_progress,
                    "overdue": p.custom_task_overdue_count,
                    "hold": p.custom_task_hold_count,
                    "waiting_user": p.custom_task_waiting_user_count,
                    "date_start": p.date_start,
                    "date_end": p.date,
                }
                for p in projects
            ]
        )

    # ----------------------------------------------------------------- tasks
    @http.route("/pmo/api/tasks", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False)
    def tasks(self, **kw):
        domain = []
        if kw.get("project_id"):
            domain.append(("project_id", "=", int(kw["project_id"])))
        if kw.get("vertical_id"):
            domain.append(("custom_vertical_id", "=", int(kw["vertical_id"])))
        if kw.get("sprint"):
            domain.append(("custom_sprint_id.week_code", "=", kw["sprint"]))
        if kw.get("open_only") in ("1", "true", "True"):
            domain.append(("stage_id.custom_is_closed_stage", "=", False))
        limit = min(int(kw.get("limit") or 200), 500)
        tasks = request.env["project.task"].search(domain, limit=limit)
        return ok([_task_dict(task) for task in tasks])

    @http.route(
        "/pmo/api/tasks/<int:task_id>",
        type="http",
        auth="jwt_pmo",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def task_get(self, task_id, **kw):
        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return err("NOT_FOUND", "No such task", status=404)
        payload = _task_dict(task)
        payload["description"] = task.description or ""
        payload["log"] = self._audit_for(task)
        return ok(payload)

    def _audit_for(self, record, limit=40):
        rows = (
            request.env["pdp.audit.log"]
            .sudo()
            .search(
                [
                    ("model_name", "=", record._name),
                    ("res_id", "=", record.id),
                ],
                limit=limit,
            )
        )
        return [
            {
                "ts": row.ts,
                "actor": row.actor_login or "",
                "action": row.action or "",
                "changes": row.field_changes or {},
                "reason": row.reason or "",
            }
            for row in rows
        ]

    @http.route("/pmo/api/tasks", type="http", auth="jwt_pmo", methods=["POST"], csrf=False, save_session=False)
    @_guard
    def task_create(self, **kw):
        body = {k: v for k, v in json_body().items() if k in TASK_WRITABLE}
        if not body.get("name"):
            return err("MISSING_NAME", "A task needs a name", status=400)
        task = request.env["project.task"].create(body)
        return ok(_task_dict(task), status=201)

    @http.route(
        "/pmo/api/tasks/<int:task_id>",
        type="http",
        auth="jwt_pmo",
        methods=["POST", "PATCH"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def task_write(self, task_id, **kw):
        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return err("NOT_FOUND", "No such task", status=404)
        body = {k: v for k, v in json_body().items() if k in TASK_WRITABLE}
        if not body:
            return err("NOTHING_TO_DO", "No writable field in the payload", status=400)
        task.write(body)
        return ok(_task_dict(task))

    @http.route(
        "/pmo/api/tasks/<int:task_id>/stage",
        type="http",
        auth="jwt_pmo",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def task_stage(self, task_id, **kw):
        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return err("NOT_FOUND", "No such task", status=404)
        body = json_body()
        stage = None
        if body.get("stage_code"):
            stage = request.env["project.task.type"]._stage_by_code(body["stage_code"])
        elif body.get("stage_id"):
            stage = request.env["project.task.type"].browse(int(body["stage_id"]))
        if not stage or not stage.exists():
            return err("BAD_STAGE", "Unknown stage", status=400)
        values = {"stage_id": stage.id}
        if body.get("hold_reason"):
            values["custom_hold_reason"] = body["hold_reason"]
        if body.get("hold_until"):
            values["custom_hold_until"] = body["hold_until"]
        task.write(values)
        return ok(_task_dict(task))

    @http.route(
        "/pmo/api/tasks/<int:task_id>/comment",
        type="http",
        auth="jwt_pmo",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def task_comment(self, task_id, **kw):
        task = request.env["project.task"].browse(task_id)
        if not task.exists():
            return err("NOT_FOUND", "No such task", status=404)
        body = json_body()
        text = (body.get("body") or "").strip()
        if not text:
            return err("EMPTY_COMMENT", "Nothing to post", status=400)
        task.message_post(body=text)
        return ok({"posted": True})

    # ------------------------------------------------------- change requests
    @http.route(
        "/pmo/api/change-requests", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    def crs(self, **kw):
        domain = []
        if kw.get("state"):
            domain.append(("approval_state", "=", kw["state"]))
        if kw.get("vertical_id"):
            domain.append(("vertical_id", "=", int(kw["vertical_id"])))
        records = request.env["custom.change.request"].search(domain, limit=300)
        return ok(
            [
                {
                    "id": cr.id,
                    "code": cr.code,
                    "name": cr.name,
                    "vertical": _vertical(cr),
                    "cr_type": cr.cr_type,
                    "impact": cr.impact,
                    "priority": cr.priority,
                    "ba": cr.ba_id.name or "",
                    "po": cr.po_id.name or "",
                    "approval_state": cr.approval_state,
                    "approval_progress": cr.approval_progress,
                    "stage": _stage(cr.stage_id),
                    "task_count": cr.task_count,
                    "task_done_count": cr.task_done_count,
                    "sla_response_due": cr.sla_response_due,
                    "sla_response_met": cr.sla_response_met,
                    "effort_days": cr.effort_estimate_days,
                    "need_downtime": cr.need_downtime,
                    "request_date": cr.request_date,
                }
                for cr in records
            ]
        )

    @http.route(
        "/pmo/api/change-requests/<int:cr_id>/action",
        type="http",
        auth="jwt_pmo",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def cr_action(self, cr_id, **kw):
        cr = request.env["custom.change.request"].browse(cr_id)
        if not cr.exists():
            return err("NOT_FOUND", "No such change request", status=404)
        body = json_body()
        action = body.get("action")
        allowed = {
            "triage": "action_start_analysis",
            "submit": "action_submit_for_approval",
            "approve": "action_approve",
            "reject": "action_reject",
            "spawn_task": "action_spawn_tasks",
            "request_verification": "action_request_verification",
        }
        if action not in allowed:
            return err("BAD_ACTION", f"Unknown action {action}", status=400)
        if action == "reject" and body.get("reason"):
            cr.reject_reason = body["reason"]
        getattr(cr, allowed[action])()
        return ok({"approval_state": cr.approval_state, "stage": _stage(cr.stage_id)})

    # ---------------------------------------------------------------- weekly
    @http.route("/pmo/api/weekly", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False)
    def weekly(self, **kw):
        domain = []
        if kw.get("week"):
            domain.append(("week_code", "=", kw["week"]))
        else:
            current = request.env["custom.project.sprint"].current_sprint()
            domain.append(("sprint_id", "=", current.id))
        rows = request.env["custom.weekly.progress"].search(domain)
        return ok(
            [
                {
                    "id": r.id,
                    "week": r.week_code,
                    "vertical": _vertical(r),
                    "project": r.project_id.name or "",
                    "author": r.author_id.name or "",
                    "state": r.state,
                    "health": r.health,
                    "progress": r.progress_pct,
                    "done_count": r.done_count,
                    "done_points": r.done_points,
                    "carry_over": r.carry_over_count,
                    "hours": r.hours_spent,
                    "cycle_time_team": r.cycle_time_team,
                    "lead_time_total": r.lead_time_total,
                    "hold_count": r.hold_count,
                    "waiting_user_count": r.waiting_user_count,
                    "plan_this_week": r.plan_this_week or "",
                    "blocker": r.blocker or "",
                    "next_week": r.next_week or "",
                }
                for r in rows
            ]
        )

    @http.route(
        "/pmo/api/weekly/<int:weekly_id>",
        type="http",
        auth="jwt_pmo",
        methods=["POST", "PATCH"],
        csrf=False,
        save_session=False,
    )
    @_guard
    def weekly_write(self, weekly_id, **kw):
        row = request.env["custom.weekly.progress"].browse(weekly_id)
        if not row.exists():
            return err("NOT_FOUND", "No such weekly report", status=404)
        body = json_body()
        values = {k: v for k, v in body.items() if k in WEEKLY_WRITABLE}
        if values:
            row.write(values)
        if body.get("submit"):
            row.action_submit()
        return ok({"id": row.id, "state": row.state})

    # ------------------------------------------------------------- dashboard
    @http.route(
        "/pmo/api/dashboard/summary", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False
    )
    def dashboard_summary(self, **kw):
        Task = request.env["project.task"]
        CR = request.env["custom.change.request"]
        now = fields.Datetime.now()
        sprint = request.env["custom.project.sprint"].current_sprint()
        return ok(
            {
                "sprint": sprint.week_code,
                "projects_active": request.env["project.project"].search_count([]),
                "projects_at_risk": request.env["project.project"].search_count(
                    [("custom_health", "in", ["at_risk", "blocked"])]
                ),
                "tasks_open": Task.search_count([("stage_id.custom_is_closed_stage", "=", False)]),
                "tasks_hold": Task.search_count([("stage_id.custom_is_hold", "=", True)]),
                "tasks_waiting_user": Task.search_count([("stage_id.custom_is_waiting_user", "=", True)]),
                "tasks_overdue": Task.search_count(
                    [
                        ("custom_due_sla_date", "<", now),
                        ("stage_id.custom_sla_clock", "=", "running"),
                    ]
                ),
                "tasks_unassigned": Task.search_count(
                    [
                        ("user_ids", "=", False),
                        ("stage_id.custom_is_closed_stage", "=", False),
                    ]
                ),
                "cr_intake": CR.search_count([("approval_state", "=", "draft")]),
                "cr_waiting_approval": CR.search_count([("approval_state", "=", "waiting_approval")]),
                "cr_active": CR.search_count([("approval_state", "=", "approved")]),
            }
        )

    @http.route(
        "/pmo/api/dashboard/ba-summary",
        type="http",
        auth="jwt_pmo",
        methods=["GET"],
        csrf=False,
        save_session=False,
    )
    def dashboard_ba(self, **kw):
        """Per-BA summary. Pure aggregates -- no new table, exactly as planned."""
        Task = request.env["project.task"]
        CR = request.env["custom.change.request"]
        Weekly = request.env["custom.weekly.progress"]
        sprint = request.env["custom.project.sprint"].current_sprint()

        # Reached from the group side on purpose: res.users' groups field has been renamed
        # across releases. ``all_user_ids`` (not ``user_ids``) is required here -- in Odoo
        # 19 ``user_ids`` lists direct members only, and every BA reaches this group
        # through implied_ids, so ``user_ids`` returns an empty set.
        bas = request.env.ref("custom_project_portfolio.group_pmo_ba").sudo().all_user_ids
        rows = []
        for ba in bas:
            verticals = request.env["custom.project.vertical"].search([("ba_ids", "in", ba.id)])
            closed = Task.search(
                [
                    ("user_ids", "in", ba.id),
                    ("custom_sprint_id", "=", sprint.id),
                    ("stage_id.custom_is_closed_stage", "=", True),
                ]
            )
            cycle_values = closed.mapped("custom_cycle_time_team")
            rows.append(
                {
                    "id": ba.id,
                    "name": ba.name,
                    "verticals": [{"code": v.code, "name": v.name} for v in verticals],
                    "cr_active": CR.search_count(
                        [
                            ("ba_id", "=", ba.id),
                            ("approval_state", "not in", ["rejected"]),
                        ]
                    ),
                    "cr_waiting_analysis": CR.search_count(
                        [
                            ("ba_id", "=", ba.id),
                            ("approval_state", "=", "draft"),
                        ]
                    ),
                    "tasks_open": Task.search_count(
                        [
                            ("user_ids", "in", ba.id),
                            ("stage_id.custom_is_closed_stage", "=", False),
                        ]
                    ),
                    "done_this_sprint": len(closed),
                    "carry_over": Task.search_count(
                        [
                            ("user_ids", "in", ba.id),
                            ("custom_carried_over", "=", True),
                        ]
                    ),
                    "avg_cycle_time_team": round(sum(cycle_values) / len(cycle_values), 2) if cycle_values else 0.0,
                    "weekly_submitted": Weekly.search_count(
                        [
                            ("author_id", "=", ba.id),
                            ("sprint_id", "=", sprint.id),
                            ("state", "!=", "draft"),
                        ]
                    ),
                    "weekly_pending": Weekly.search_count(
                        [
                            ("author_id", "=", ba.id),
                            ("sprint_id", "=", sprint.id),
                            ("state", "=", "draft"),
                        ]
                    ),
                    "sla_breached": Task.search_count(
                        [
                            ("user_ids", "in", ba.id),
                            ("custom_due_sla_date", "<", fields.Datetime.now()),
                            ("stage_id.custom_sla_clock", "=", "running"),
                        ]
                    ),
                }
            )
        return ok({"sprint": sprint.week_code, "analysts": rows})

    @http.route("/pmo/api/search", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False)
    def search(self, **kw):
        """One query across tasks, change requests and projects — feeds the command palette.

        Deliberately small and capped: this runs on every keystroke, so it returns just
        enough to render a row and navigate.
        """
        term = (kw.get("q") or "").strip()
        limit = min(int(kw.get("limit") or 8), 20)
        results = []

        task_domain = [("stage_id.custom_is_closed_stage", "=", False)]
        cr_domain = []
        if term:
            task_domain = ["|", ("name", "ilike", term), ("id", "=", term if term.isdigit() else 0)]
            cr_domain = ["|", ("name", "ilike", term), ("code", "ilike", term)]

        for task in request.env["project.task"].search(task_domain, limit=limit):
            results.append(
                {
                    "type": "task",
                    "id": task.id,
                    "label": task.name,
                    "hint": task.custom_vertical_id.code or task.project_id.name or "",
                    "stage": task.stage_id.name or "",
                    "url": f"/tasks/{task.id}",
                }
            )
        for cr in request.env["custom.change.request"].search(cr_domain, limit=limit):
            results.append(
                {
                    "type": "cr",
                    "id": cr.id,
                    "label": f"{cr.code} — {cr.name}",
                    "hint": cr.vertical_id.code or "",
                    "stage": cr.stage_id.name or "",
                    "url": "/cr",
                }
            )
        if term:
            for project in request.env["project.project"].search(
                [("name", "ilike", term)],
                limit=limit,
            ):
                results.append(
                    {
                        "type": "project",
                        "id": project.id,
                        "label": project.name,
                        "hint": project.custom_vertical_id.code or "",
                        "stage": project.custom_health,
                        "url": f"/board?project={project.id}",
                    }
                )
        return ok(results[: limit * 2])

    @http.route("/pmo/api/logs", type="http", auth="jwt_pmo", methods=["GET"], csrf=False, save_session=False)
    def logs(self, **kw):
        """Global transaction log, straight off the hash-chained audit view."""
        domain = []
        if kw.get("model"):
            domain.append(("model_name", "=", kw["model"]))
        if kw.get("action"):
            domain.append(("action", "=", kw["action"]))
        limit = min(int(kw.get("limit") or 100), 500)
        rows = request.env["pdp.audit.log"].sudo().search(domain, limit=limit)
        return ok(
            [
                {
                    "ts": row.ts,
                    "actor": row.actor_login or "",
                    "model": row.model_name,
                    "res_id": row.res_id,
                    "action": row.action or "",
                    "changes": row.field_changes or {},
                    "reason": row.reason or "",
                    "hash": (row.hash_hex or "")[:12],
                }
                for row in rows
            ]
        )
