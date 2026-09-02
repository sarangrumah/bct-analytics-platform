# -*- coding: utf-8 -*-
"""Friendly wrapper around Odoo's ``base.automation`` + ``ir.actions.server``.

The native base.automation form mixes trigger config, server-action code,
and lifecycle controls in one dense view that's intimidating for
non-developers. This module exposes a slimmer ``studio.automation.rule``
that:

- Picks from a curated list of triggers (create / write / time-based / state).
- Picks from a curated list of action templates (post note, send email,
  send WhatsApp, set field, call HTTP webhook, run Python — last one for
  designers comfortable with the underlying machinery).
- Materialises a matching ``base.automation`` row on apply, with one or
  more ``ir.actions.server`` rows linked.
- Supports an *ordered chain* of actions on a single trigger (Phase 3
  rule chains), each with its own optional condition.

The native ``base.automation`` record remains the source of truth; this
wrapper just makes it approachable.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


TRIGGER_CHOICES = [
    ("on_create", "When a record is created"),
    ("on_write", "When a record is updated"),
    ("on_create_or_write", "When a record is created or updated"),
    ("on_unlink", "When a record is deleted"),
    ("on_time", "On a time delay"),
    ("on_state_set", "When state changes to..."),
    ("on_user_set", "When user is assigned..."),
]


ACTION_TEMPLATES = [
    ("post_note", "Post a note on the record"),
    ("send_email", "Send an email"),
    ("send_whatsapp", "Send a WhatsApp message"),
    ("set_field", "Set a field value"),
    ("call_webhook", "Call an HTTP webhook"),
    ("python_code", "Run Python code (advanced)"),
]


class StudioAutomationRule(models.Model):
    _name = "studio.automation.rule"
    _description = "Studio Automation Rule"
    _inherit = ["pdp.audited.mixin"]
    _order = "model_id, sequence, name"

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    description = fields.Text(help="Plain-language description shown on the rule list.")
    model_id = fields.Many2one(
        "ir.model",
        required=True,
        ondelete="cascade",
        domain="[('transient','=',False)]",
    )
    model_name = fields.Char(related="model_id.model", store=True, readonly=True)
    trigger = fields.Selection(TRIGGER_CHOICES, required=True, default="on_create")
    trigger_field_ids = fields.Many2many(
        "ir.model.fields",
        string="Watched Fields",
        domain="[('model_id','=',model_id), ('store','=',True)]",
        help="For 'on_write' triggers, only fire when one of these fields changes.",
    )
    filter_domain = fields.Char(
        string="Filter",
        default="[]",
        help="Odoo domain that records must match for the rule to fire.",
    )
    delay_minutes = fields.Integer(
        default=0,
        help="For 'on_time' triggers, delay in minutes after the watched datetime.",
    )
    trg_date_id = fields.Many2one(
        "ir.model.fields",
        string="Reference Date Field",
        domain="[('model_id','=',model_id), ('ttype','in',('date','datetime'))]",
    )
    state_value = fields.Char(
        string="Target State Value",
        help="For 'on_state_set' triggers — the state value that should fire the rule.",
    )

    action_ids = fields.One2many(
        "studio.automation.action",
        "rule_id",
        string="Actions to Run",
        copy=True,
    )

    base_automation_id = fields.Many2one(
        "base.automation",
        readonly=True,
        copy=False,
        ondelete="set null",
    )
    state = fields.Selection(
        [("draft", "Draft"), ("applied", "Applied"), ("error", "Error")],
        default="draft",
        required=True,
    )
    last_error = fields.Text(readonly=True)
    last_run = fields.Datetime(related="base_automation_id.last_run", readonly=True)

    def _pdp_audit_classification(self):
        return "internal"

    @api.constrains("trigger", "trg_date_id", "delay_minutes", "state_value")
    def _check_trigger_args(self):
        for rec in self:
            if rec.trigger == "on_time" and not rec.trg_date_id:
                raise ValidationError(_("Time-based trigger requires a Reference Date Field."))
            if rec.trigger == "on_state_set" and not rec.state_value:
                raise ValidationError(_("State-change trigger requires a Target State Value."))

    @api.constrains("action_ids")
    def _check_actions(self):
        for rec in self:
            if not rec.action_ids:
                raise ValidationError(_("Add at least one action."))

    # ---------- Apply ----------

    def action_apply(self):
        Automation = self.env["base.automation"].sudo()
        ServerAction = self.env["ir.actions.server"].sudo()

        for rec in self:
            try:
                auto_vals = rec._build_automation_vals()
                if rec.base_automation_id:
                    rec.base_automation_id.write(auto_vals)
                    automation = rec.base_automation_id
                    # Replace existing server actions to avoid stale duplicates.
                    automation.action_server_ids.sudo().unlink()
                else:
                    automation = Automation.create(auto_vals)
                    rec.base_automation_id = automation.id

                # Materialise the action chain.
                for action in rec.action_ids.sorted("sequence"):
                    sa_vals = action._build_server_action_vals(automation)
                    ServerAction.create(sa_vals)

                rec.write({"state": "applied", "last_error": False})
                rec._pdp_audit_write(
                    "studio_automation_applied",
                    rec.id,
                    {"model": rec.model_name, "trigger": rec.trigger},
                )
            except Exception as e:
                _logger.exception("studio.automation.rule %s apply failed", rec.id)
                rec.write({"state": "error", "last_error": str(e)})
                rec._pdp_audit_write("studio_automation_apply_failed", rec.id, {"error": str(e)})

    def action_revert(self):
        for rec in self:
            if rec.base_automation_id:
                rec.base_automation_id.write({"active": False})
            rec.write({"state": "draft"})

    def _build_automation_vals(self) -> dict:
        self.ensure_one()
        vals = {
            "name": self.name,
            "model_id": self.model_id.id,
            "trigger": self.trigger,
            "active": self.active,
            "filter_domain": self.filter_domain or "[]",
        }
        if self.trigger_field_ids:
            vals["trigger_field_ids"] = [(6, 0, self.trigger_field_ids.ids)]
        if self.trigger == "on_time":
            vals.update(
                {
                    "trg_date_id": self.trg_date_id.id,
                    "trg_date_range": self.delay_minutes or 0,
                    "trg_date_range_type": "minutes",
                }
            )
        if self.trigger == "on_state_set" and self.state_value:
            # Encode the state filter directly into the domain so it
            # fires only when state hits the target value.
            extra = f"('state','=','{self.state_value}')"
            base = (self.filter_domain or "[]").rstrip()
            if base in ("", "[]"):
                vals["filter_domain"] = f"[{extra}]"
            else:
                vals["filter_domain"] = base[:-1] + ", " + extra + "]"
        return vals


class StudioAutomationAction(models.Model):
    _name = "studio.automation.action"
    _description = "Studio Automation Action"
    _order = "rule_id, sequence, id"
    _parent_store = True
    _parent_name = "parent_action_id"

    rule_id = fields.Many2one(
        "studio.automation.rule",
        required=True,
        ondelete="cascade",
        index=True,
    )
    sequence = fields.Integer(default=10)
    name = fields.Char(required=True, default="Action")
    template = fields.Selection(ACTION_TEMPLATES, required=True, default="post_note")
    condition = fields.Char(
        string="When (optional)",
        help="Optional Python expression evaluated against ``record``; "
        "if it returns a falsy value, this action is skipped.",
    )

    # ---- Phase 3: rule-chain nesting ----
    parent_action_id = fields.Many2one(
        "studio.automation.action",
        string="Parent (for nested chains)",
        ondelete="cascade",
        index=True,
        help="When set, this action runs inside the parent's branch.",
    )
    parent_path = fields.Char(index=True)
    branch_type = fields.Selection(
        [("always", "Always run"), ("then", "If parent condition is true"), ("else", "Otherwise (else branch)")],
        default="always",
        required=True,
        help="Used when this action has a parent_action_id — controls which branch it sits on.",
    )
    child_action_ids = fields.One2many("studio.automation.action", "parent_action_id", string="Nested Actions")

    # ---- Phase 3: cross-record trigger ----
    target_relation_field_id = fields.Many2one(
        "ir.model.fields",
        string="Run on Related Records",
        domain="[('model_id','=',parent.model_id), ('ttype','in',('one2many','many2many','many2one'))]",
        help="If set, this action runs on the records reached by following this relation "
        "(instead of the triggering record).",
    )

    # Per-template inputs (kept loose — the renderer picks what it needs).
    body = fields.Text(string="Message body / note text")
    email_template_id = fields.Many2one("mail.template", string="Email Template")
    whatsapp_account_id = fields.Many2one("whatsapp.account", string="WhatsApp Account")
    whatsapp_template_id = fields.Many2one("whatsapp.template", string="WhatsApp Template")
    phone_field_id = fields.Many2one(
        "ir.model.fields",
        string="Phone Field",
        domain="[('model_id','=',parent.model_id), ('ttype','in',('char','text'))]",
    )
    target_field_id = fields.Many2one(
        "ir.model.fields",
        string="Target Field",
        domain="[('model_id','=',parent.model_id), ('store','=',True)]",
    )
    target_value = fields.Char(string="New Value")
    webhook_url = fields.Char(string="Webhook URL")
    python_code = fields.Text(
        string="Python Code",
        help=("Standard server-action code. ``record``, ``records``, ``env``, ``model`` and ``log`` are available."),
    )

    def _build_server_action_vals(self, automation) -> dict:
        """Return the ir.actions.server vals for this action template.

        Top-level actions (no parent_action_id) are rendered as full
        server-action code blocks. Their nested child actions are
        composed into the same Python body via :meth:`_render_full`.
        """
        self.ensure_one()
        common = {
            "name": self.name or self.template,
            "model_id": self.rule_id.model_id.id,
            "base_automation_id": automation.id,
        }
        common["state"] = "code"
        common["code"] = self._render_full()
        return common

    def _render_full(self) -> str:
        """Render the action plus any nested child branches into one Python block."""
        self.ensure_one()
        target_iter = self._target_record_iter()
        body = []
        # Own action body — runs for every target record.
        body.append(self._render_body(indent="    "))
        # Children: a nested if/else evaluating this action's condition.
        if self.child_action_ids:
            then_children = self.child_action_ids.filtered(lambda c: c.branch_type == "then")
            else_children = self.child_action_ids.filtered(lambda c: c.branch_type == "else")
            always_children = self.child_action_ids.filtered(lambda c: c.branch_type == "always")
            cond_expr = (self.condition or "True").strip() or "True"
            if then_children or else_children:
                body.append(f"    if ({cond_expr}):")
                if then_children:
                    for child in then_children.sorted("sequence"):
                        body.append(child._render_full_indented("        "))
                else:
                    body.append("        pass")
                if else_children:
                    body.append("    else:")
                    for child in else_children.sorted("sequence"):
                        body.append(child._render_full_indented("        "))
            for child in always_children.sorted("sequence"):
                body.append(child._render_full_indented("    "))
        return target_iter + "\n".join(body) + "\n"

    def _render_full_indented(self, base_indent: str) -> str:
        """Render this action as a nested block under base_indent."""
        self.ensure_one()
        target_iter = self._target_record_iter(base_indent=base_indent)
        if target_iter:
            body = self._render_body(indent=base_indent + "    ")
            return target_iter + body
        return self._render_body(indent=base_indent)

    def _target_record_iter(self, base_indent: str = "") -> str:
        """Return the ``for record in <iterable>:`` header for this action.

        - Top-level actions iterate over the triggering ``records``.
        - Cross-record actions (target_relation_field_id set) iterate
          over the records reached by following the relation.
        """
        self.ensure_one()
        if self.target_relation_field_id:
            rel = self.target_relation_field_id.name
            return (
                f"{base_indent}for trigger_record in records:\n"
                f"{base_indent}    related = getattr(trigger_record, {rel!r}, False)\n"
                f"{base_indent}    if not related:\n"
                f"{base_indent}        continue\n"
                f"{base_indent}    for record in (related if hasattr(related, '__iter__') else [related]):\n"
            )
        if not self.parent_action_id:
            return f"{base_indent}for record in records:\n"
        return ""  # nested actions inherit the parent's record loop

    def _render_body(self, indent: str = "    ") -> str:
        """Render the action's body lines indented at the given prefix."""
        self.ensure_one()
        cond = (self.condition or "").strip()
        # When the action has children, the condition controls the branches —
        # the body itself should always run.
        skip_inline_cond = bool(self.child_action_ids)
        lines = []
        if cond and not skip_inline_cond:
            lines.append(f"{indent}if not ({cond}):")
            lines.append(f"{indent}    continue")
        lines.append(self._render_template(indent=indent))
        return "\n".join(lines)

    def _render_template(self, indent: str = "    ") -> str:
        """Render the per-template Python statement(s) at the given indent."""
        self.ensure_one()
        i = indent

        def _indent_block(text: str) -> str:
            return "\n".join(i + line if line else "" for line in text.splitlines())

        if self.template == "post_note":
            body = (self.body or "").replace('"""', '\\"\\"\\"')
            return f'{i}record.message_post(body="""{body}""")'
        if self.template == "send_email":
            if not self.email_template_id:
                raise UserError(_("Select an email template."))
            return f"{i}env['mail.template'].browse({self.email_template_id.id}).send_mail(record.id, force_send=True)"
        if self.template == "send_whatsapp":
            if not self.whatsapp_account_id:
                raise UserError(_("Select a WhatsApp account."))
            phone_attr = self.phone_field_id.name if self.phone_field_id else "phone"
            tpl_id = self.whatsapp_template_id.id if self.whatsapp_template_id else "False"
            body = (self.body or "").replace('"""', '\\"\\"\\"')
            return _indent_block(
                f"phone = getattr(record, {phone_attr!r}, False)\n"
                "if phone:\n"
                "    env['whatsapp.message'].sudo().create({\n"
                f"        'account_id': {self.whatsapp_account_id.id},\n"
                f"        'template_id': {tpl_id},\n"
                "        'to_phone': phone,\n"
                "        'to_partner_id': getattr(record, 'partner_id', record).id if hasattr(record, 'partner_id') else False,\n"
                f"        'body': '''{body}''',\n"
                "    }).action_send()"
            )
        if self.template == "set_field":
            if not self.target_field_id:
                raise UserError(_("Select a target field."))
            val = self.target_value or ""
            return f"{i}record.write({{'{self.target_field_id.name}': {val!r}}})"
        if self.template == "call_webhook":
            if not self.webhook_url:
                raise UserError(_("Provide a webhook URL."))
            body = self.body or "{}"
            return _indent_block(
                "import json, urllib.request\n"
                f"req = urllib.request.Request({self.webhook_url!r}, "
                f"data=({body!r}).encode('utf-8'), headers={{'Content-Type':'application/json'}}, method='POST')\n"
                "try:\n"
                "    urllib.request.urlopen(req, timeout=10)\n"
                "except Exception as e:\n"
                "    log(f'webhook failed: {e}')"
            )
        if self.template == "python_code":
            user_code = (self.python_code or "pass").rstrip()
            return _indent_block(user_code)
        return f"{i}pass"
