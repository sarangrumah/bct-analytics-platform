# -*- coding: utf-8 -*-
"""Trigger intercompany mirroring on ``_post`` for outbound documents.

Idempotent — if a mirror was already produced for a given source move,
re-posting does not duplicate it (tracked via ``x_custom_ic_mirror_id``).
"""

from __future__ import annotations

import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _name = "account.move"
    _inherit = ["account.move", "pdp.audited.mixin"]

    # The mirrored move in the sister company (set on the SOURCE move).
    x_custom_ic_mirror_id = fields.Many2one(
        "account.move",
        string="Intercompany Mirror",
        readonly=True,
        copy=False,
        help="The auto-generated counterpart move in the sister company.",
    )
    # On the MIRROR move, points back to the source.
    x_custom_ic_source_id = fields.Many2one(
        "account.move",
        string="Intercompany Source",
        readonly=True,
        copy=False,
    )
    x_custom_ic_rule_id = fields.Many2one(
        "account.intercompany.rule",
        string="Intercompany Rule",
        readonly=True,
        copy=False,
    )

    # ------------------------------------------------------------------

    def _post(self, soft=True):
        res = super()._post(soft=soft)
        for move in self:
            move._custom_run_intercompany_mirror()
        return res

    def _custom_run_intercompany_mirror(self):
        """Locate an applicable rule and create / refresh the mirror move."""
        self.ensure_one()
        # Skip mirror moves themselves (avoid recursion)
        if self.x_custom_ic_source_id:
            return
        # Skip non-invoice moves
        if self.move_type not in ("out_invoice", "out_refund", "in_invoice", "in_refund"):
            return
        # Skip if mirror already exists
        if self.x_custom_ic_mirror_id:
            return
        rule = self._custom_find_intercompany_rule()
        if not rule:
            return
        try:
            mirror = self._custom_create_intercompany_mirror(rule)
        except Exception as e:
            _logger.exception("Intercompany mirror failed for move %s: %s", self.id, e)
            self.message_post(
                body=_(
                    "Intercompany mirror FAILED: %s. The original move is posted; "
                    "please mirror manually or fix the rule."
                )
                % e
            )
            return
        if mirror:
            self.write(
                {
                    "x_custom_ic_mirror_id": mirror.id,
                    "x_custom_ic_rule_id": rule.id,
                }
            )
            self._pdp_audit_write(
                "intercompany_mirror_created",
                self.id,
                {"rule": rule.name, "mirror_id": mirror.id, "mirror_company": rule.company_to_id.name},
            )

    def _custom_find_intercompany_rule(self):
        """Return the rule that applies to ``self`` based on partner ↔ company linkage."""
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        # Find the receiving company by partner linkage:
        # `res.company.partner_id` holds the partner that represents that company.
        receiver = self.env["res.company"].sudo().search([("partner_id", "=", partner.id)], limit=1)
        if not receiver or receiver == self.company_id:
            return self.env["account.intercompany.rule"]
        # Map move_type → direction
        if self.move_type in ("out_invoice", "out_refund"):
            wanted = "sale_to_purchase"
        else:
            wanted = "purchase_to_sale"
        return (
            self.env["account.intercompany.rule"]
            .sudo()
            .search(
                [
                    ("active", "=", True),
                    ("company_from_id", "=", self.company_id.id),
                    ("company_to_id", "=", receiver.id),
                    ("direction", "in", (wanted, "both")),
                ],
                limit=1,
            )
        )

    def _custom_create_intercompany_mirror(self, rule):
        """Build a draft move in ``rule.company_to_id`` mirroring this one."""
        self.ensure_one()
        target_type = {
            "out_invoice": "in_invoice",
            "out_refund": "in_refund",
            "in_invoice": "out_invoice",
            "in_refund": "out_refund",
        }[self.move_type]

        target_company = rule.company_to_id
        target_partner = self.company_id.partner_id  # the issuing company's own partner

        # Pick journal (explicit override, otherwise default for type)
        journal = rule.target_journal_id
        if not journal:
            jtype = "purchase" if target_type.startswith("in_") else "sale"
            journal = (
                self.env["account.journal"]
                .sudo()
                .search([("company_id", "=", target_company.id), ("type", "=", jtype)], limit=1)
            )
        if not journal:
            raise UserError(
                _("No journal configured in receiving company '%s' for type '%s'.") % (target_company.name, target_type)
            )

        line_vals = []
        for line in self.invoice_line_ids:
            mapped_account = rule._map_account(line.account_id)
            if not mapped_account:
                _logger.warning("Intercompany: no account mapping for %s; skipping line.", line.account_id.code)
                continue
            line_vals.append(
                (
                    0,
                    0,
                    {
                        "name": line.name,
                        "quantity": line.quantity,
                        "price_unit": line.price_unit,
                        "account_id": mapped_account.id,
                        "product_id": line.product_id.id if line.product_id else False,
                        "tax_ids": [(6, 0, [])],  # Receiver-side taxes resolved by fiscal position later
                    },
                )
            )

        mirror = (
            self.env["account.move"]
            .with_company(target_company)
            .sudo()
            .create(
                {
                    "move_type": target_type,
                    "partner_id": target_partner.id,
                    "journal_id": journal.id,
                    "invoice_date": self.invoice_date or fields.Date.context_today(self),
                    "ref": _("IC mirror of %s/%s") % (self.company_id.name, self.name or self.id),
                    "x_custom_ic_source_id": self.id,
                    "x_custom_ic_rule_id": rule.id,
                    "invoice_line_ids": line_vals,
                    "company_id": target_company.id,
                }
            )
        )
        if rule.auto_validate:
            mirror.action_post()
        return mirror

    # PDP classification — fully-formed business document → internal by default
    def _pdp_audit_classification(self):
        return "financial"
