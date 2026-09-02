# -*- coding: utf-8 -*-
"""dev.cycle.pr — Pull/Merge Request tracked from GitHub or GitLab."""

from __future__ import annotations

from odoo import _, api, fields, models


class DevCyclePr(models.Model):
    _name = "dev.cycle.pr"
    _description = "Dev Cycle Pull Request"
    _order = "id desc"

    cycle_id = fields.Many2one(
        "dev.cycle",
        string="Dev Cycle",
        required=True,
        ondelete="cascade",
        index=True,
    )
    provider = fields.Selection(
        [("github", "GitHub"), ("gitlab", "GitLab")],
        required=True,
        default="github",
    )
    pr_number = fields.Integer(string="PR #", index=True)
    pr_url = fields.Char(string="PR URL", required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("open", "Open"),
            ("merged", "Merged"),
            ("closed", "Closed"),
        ],
        default="open",
        index=True,
    )
    ci_status = fields.Selection(
        [
            ("pending", "Pending"),
            ("success", "Success"),
            ("failure", "Failure"),
            ("error", "Error"),
        ],
        default="pending",
        index=True,
    )
    reviewers = fields.Char(help="Comma-separated reviewer logins.")
    merged_at = fields.Datetime()
    merged_by = fields.Char()
    last_synced_at = fields.Datetime()

    _cycle_pr_url_uniq = models.Constraint(
        "unique(cycle_id, pr_url)",
        "A PR URL can only be linked once per dev cycle.",
    )

    # ------------------------------------------------------------------
    # Auto-transition cycle
    # ------------------------------------------------------------------

    def _apply_state_to_cycle(self):
        """If PR is merged with successful CI, advance the cycle.

        - merged + success → ``deployed`` (if not already at or past deployed)
        - merged but CI failing → no change
        - open + success → move cycle to ``code_review`` if still in ``in_dev``
        """
        for pr in self:
            cycle = pr.cycle_id
            if not cycle:
                continue
            if pr.state == "merged" and pr.ci_status == "success":
                # Only advance if cycle hasn't already passed `deployed`.
                from .dev_cycle import STATE_SEQUENCE

                try:
                    cur_idx = STATE_SEQUENCE.index(cycle.state)
                    target_idx = STATE_SEQUENCE.index("deployed")
                except ValueError:
                    continue
                if cur_idx < target_idx:
                    cycle.write({"state": "deployed"})
                    cycle.message_post(
                        body=_("Auto-transition: PR %s merged with green CI → deployed.")
                        % (pr.pr_url or pr.pr_number or "")
                    )
            elif pr.state == "open" and cycle.state == "in_dev":
                cycle.write({"state": "code_review"})
                cycle.message_post(
                    body=_("Auto-transition: PR %s opened → code_review.") % (pr.pr_url or pr.pr_number or "")
                )
        return True

    @api.model
    def upsert_from_webhook(self, cycle, provider, pr_url, vals):
        """Upsert a PR row matched by (cycle_id, pr_url)."""
        existing = self.search([("cycle_id", "=", cycle.id), ("pr_url", "=", pr_url)], limit=1)
        vals = dict(vals)
        vals["last_synced_at"] = fields.Datetime.now()
        if existing:
            existing.write(vals)
            pr = existing
        else:
            vals.update({"cycle_id": cycle.id, "provider": provider, "pr_url": pr_url})
            pr = self.create(vals)
        pr._apply_state_to_cycle()
        return pr
