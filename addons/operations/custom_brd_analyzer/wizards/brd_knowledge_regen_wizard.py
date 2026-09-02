# -*- coding: utf-8 -*-
"""Wizard: regenerate or reset MODULE_KNOWLEDGE.md for one module.

Three flavors:
* ``api`` — shell out to ``scripts/generate_module_knowledge.py`` against the
  Anthropic-backed ai-gateway (Sonnet quality by default). Cheap and
  autonomous, but burns API credits.
* ``manual`` — assume the dev already edited the .md by hand; just reset
  ``last_knowledge_source_hash = current source_hash`` so drift clears.
* ``ide`` — display the absolute path so the dev can open it externally.
  Cannot trigger a Windows/macOS IDE from inside Odoo reliably.
"""

from __future__ import annotations

import logging
import os
import subprocess

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Default location of the generator script relative to the Odoo addons root.
# Override with ``custom_brd_analyzer.regen_script_path`` system parameter.
_DEFAULT_SCRIPT = "/opt/odoo/scripts/generate_module_knowledge.py"


class BrdKnowledgeRegenWizard(models.TransientModel):
    _name = "brd.knowledge.regen.wizard"
    _description = "Regenerate MODULE_KNOWLEDGE.md"

    entry_id = fields.Many2one(
        "custom.module.capability.entry",
        required=True,
        ondelete="cascade",
    )
    module_name = fields.Char(related="entry_id.module_name", readonly=True)
    module_path = fields.Char(related="entry_id.module_path", readonly=True)
    knowledge_status = fields.Selection(related="entry_id.knowledge_status", readonly=True)
    mode = fields.Selection(
        [
            ("api", "Trigger generator script (Anthropic API, Sonnet)"),
            ("manual", "Mark as manually edited (no LLM call)"),
            ("ide", "Show file path to open externally"),
        ],
        default="manual",
        required=True,
    )
    result_message = fields.Text(readonly=True)

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        entry_id = self.env.context.get("default_entry_id") or self.env.context.get("active_id")
        if entry_id:
            defaults["entry_id"] = entry_id
        return defaults

    def action_execute(self):
        self.ensure_one()
        if not self.entry_id:
            raise UserError(_("No catalog entry selected."))
        if self.mode == "manual":
            return self._do_manual()
        if self.mode == "api":
            return self._do_api()
        return self._do_ide()

    def _do_manual(self):
        # Reset the drift lock to the current source hash; rescan picks it up.
        self.entry_id.sudo().write(
            {
                "last_knowledge_source_hash": self.entry_id.source_hash or "",
            }
        )
        # If knowledge_md exists, status returns to draft/reviewed based on
        # the file frontmatter; trigger a rescan to refresh.
        self.env["custom.module.capability.entry"].sudo()._scan_all_modules()
        self.result_message = _("Marked as manually clean. Catalog rescanned.")
        return self._reopen()

    def _do_api(self):
        Param = self.env["ir.config_parameter"].sudo()
        script = Param.get_param("custom_brd_analyzer.regen_script_path", default=_DEFAULT_SCRIPT)
        if not os.path.isfile(script):
            raise UserError(
                _(
                    "Generator script not found at %s. Set "
                    "ir.config_parameter custom_brd_analyzer.regen_script_path "
                    "to the correct path inside the Odoo container."
                )
                % script
            )
        if not self.module_name:
            raise UserError(_("Catalog entry has no module_name."))
        secret = os.environ.get("GATEWAY_SHARED_SECRET", "")
        if not secret:
            raise UserError(_("GATEWAY_SHARED_SECRET env var not set inside the Odoo container."))
        gateway = os.environ.get("AI_GATEWAY_URL", "http://ai-gateway:8080")
        try:
            proc = subprocess.run(
                [
                    "python3",
                    script,
                    "--module",
                    self.module_name,
                    "--force",
                    "--gateway",
                    gateway,
                    "--secret",
                    secret,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except subprocess.TimeoutExpired:
            raise UserError(_("Regeneration timed out after 10 minutes.")) from None
        if proc.returncode != 0:
            _logger.error("regen script stderr: %s", proc.stderr[-500:])
            raise UserError(_("Script failed (exit %s). stderr: %s") % (proc.returncode, proc.stderr[-500:]))
        # Rescan so the new knowledge file is picked up + drift cleared.
        self.env["custom.module.capability.entry"].sudo()._scan_all_modules()
        self.result_message = _("Generator ran OK.\n\n%s") % (proc.stdout[-2000:] or "(no stdout)")
        return self._reopen()

    def _do_ide(self):
        if not self.module_path:
            raise UserError(_("Catalog entry has no module_path."))
        md_path = os.path.join(self.module_path, "MODULE_KNOWLEDGE.md")
        self.result_message = _("Open this file in your editor:\n\n%s") % md_path
        return self._reopen()

    def _reopen(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
