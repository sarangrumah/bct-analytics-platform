# -*- coding: utf-8 -*-
"""Lunch product extensions (Indonesia EE)."""

import logging
from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


# Canonical day tokens used by ``x_available_days`` (lowercase, comma-separated).
DAY_TOKENS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_days_csv(value):
    """Return a normalised tuple of valid day tokens from a CSV string."""
    if not value:
        return ()
    raw = [tok.strip().lower()[:3] for tok in value.split(",") if tok.strip()]
    return tuple(tok for tok in raw if tok in DAY_TOKENS)


class LunchProduct(models.Model):
    _inherit = "lunch.product"

    x_id_halal = fields.Boolean(string="Halal", default=False)
    x_id_vegetarian = fields.Boolean(string="Vegetarian", default=False)
    x_id_spice_level = fields.Selection(
        [
            ("none", "None"),
            ("mild", "Mild"),
            ("medium", "Medium"),
            ("hot", "Hot"),
            ("very_hot", "Very Hot"),
        ],
        string="Spice Level",
        default="none",
    )
    x_id_calories = fields.Integer(string="Calories (kcal)")

    x_available_days = fields.Char(
        string="Available Days",
        help=(
            "Comma-separated weekday tokens when this product should be active. "
            "Use any of: mon,tue,wed,thu,fri,sat,sun. Leave empty for always-on."
        ),
    )

    @api.constrains("x_available_days")
    def _check_available_days(self):
        for rec in self:
            if not rec.x_available_days:
                continue
            raw_tokens = [t.strip().lower() for t in rec.x_available_days.split(",") if t.strip()]
            bad = [t for t in raw_tokens if t[:3] not in DAY_TOKENS]
            if bad:
                raise ValidationError(
                    _("Invalid weekday token(s): %(bad)s. Allowed: mon, tue, wed, thu, fri, sat, sun.")
                    % {"bad": ", ".join(bad)}
                )

    @api.model
    def cron_publish_daily_menu(self, today=None):
        """Activate/deactivate products based on their ``x_available_days`` schedule.

        Products with an empty schedule are left untouched (always-on). Otherwise
        the day-of-week of ``today`` (defaults to ``date.today()``) is matched
        against the CSV token list; the ``active`` flag is flipped if needed.
        """
        today = today or date.today()
        token = DAY_TOKENS[today.weekday()]
        # We need archived rows too, so the cron can re-activate them.
        products = self.with_context(active_test=False).search([("x_available_days", "!=", False)])
        activated = 0
        deactivated = 0
        for prod in products:
            allowed = _parse_days_csv(prod.x_available_days)
            if not allowed:
                continue
            should_be_active = token in allowed
            if prod.active != should_be_active:
                prod.active = should_be_active
                if should_be_active:
                    activated += 1
                else:
                    deactivated += 1
        _logger.info(
            "[custom_lunch] cron_publish_daily_menu(%s/%s): scanned=%s activated=%s deactivated=%s",
            today,
            token,
            len(products),
            activated,
            deactivated,
        )
        return {"scanned": len(products), "activated": activated, "deactivated": deactivated}
