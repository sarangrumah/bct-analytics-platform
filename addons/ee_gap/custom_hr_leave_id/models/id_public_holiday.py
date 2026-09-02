# -*- coding: utf-8 -*-
import logging
from datetime import date

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class IdPublicHoliday(models.Model):
    _name = "id.public.holiday"
    _description = "Indonesian Public Holiday"
    _order = "date desc, name"

    name = fields.Char(string="Holiday Name", required=True)
    date = fields.Date(string="Date", required=True, index=True)
    type_code = fields.Selection(
        [
            ("national", "National"),
            ("regional", "Regional"),
            ("religious", "Religious"),
        ],
        string="Type",
        default="national",
        required=True,
    )
    year = fields.Integer(
        string="Year",
        compute="_compute_year",
        store=True,
        index=True,
    )
    notes = fields.Text(string="Notes")

    _uniq_date_name = models.Constraint(
        "unique(date, name)",
        "Holiday with this name and date already exists",
    )

    @api.depends("date")
    def _compute_year(self):
        for rec in self:
            rec.year = rec.date.year if rec.date else 0

    @api.model
    def cron_import_public_holidays(self):
        """Verify that current and next year's public holidays are seeded.

        Seed data for 2024-2026 is loaded via `data/id_public_holiday_*.xml`
        (noupdate=1). This cron only logs whether seed data already exists for
        the relevant years; future implementations may fetch upstream APIs
        (api-harilibur, dayoffapi) to populate missing years.
        """
        today = date.today()
        years_to_check = (today.year, today.year + 1)
        for year in years_to_check:
            count = self.search_count([("year", "=", year)])
            if count:
                _logger.info(
                    "id.public.holiday: %s seeded holiday(s) for year %s.",
                    count,
                    year,
                )
            else:
                _logger.warning(
                    "id.public.holiday: no holidays seeded for year %s; "
                    "add data/id_public_holiday_%s.xml or wire an upstream API.",
                    year,
                    year,
                )
        return True
