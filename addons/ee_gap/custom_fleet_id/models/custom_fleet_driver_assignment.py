# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomFleetDriverAssignment(models.Model):
    _name = "custom.fleet.driver.assignment"
    _description = "Fleet Driver Assignment History"
    _order = "start_date desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(
        string="Reference",
        compute="_compute_name",
        store=True,
    )
    vehicle_id = fields.Many2one(
        "fleet.vehicle",
        string="Vehicle",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Driver",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    start_date = fields.Date(
        string="Start Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    end_date = fields.Date(
        string="End Date",
        tracking=True,
    )
    status = fields.Selection(
        [
            ("active", "Active"),
            ("ended", "Ended"),
            ("transferred", "Transferred"),
        ],
        string="Status",
        default="active",
        required=True,
        tracking=True,
    )
    duration_days = fields.Integer(
        string="Duration (days)",
        compute="_compute_duration",
        store=True,
    )
    notes = fields.Text(string="Notes")

    # ---------- Computes ----------

    @api.depends("vehicle_id", "partner_id", "start_date")
    def _compute_name(self):
        for rec in self:
            v = rec.vehicle_id.license_plate or rec.vehicle_id.display_name or "-"
            d = rec.partner_id.display_name or "-"
            rec.name = "%s -> %s (%s)" % (v, d, rec.start_date or "")

    @api.depends("start_date", "end_date")
    def _compute_duration(self):
        today = fields.Date.context_today(self)
        for rec in self:
            if not rec.start_date:
                rec.duration_days = 0
                continue
            end = rec.end_date or today
            rec.duration_days = max(0, (end - rec.start_date).days)

    # ---------- Constraints ----------

    @api.constrains("start_date", "end_date")
    def _check_dates(self):
        for rec in self:
            if rec.end_date and rec.start_date and rec.end_date < rec.start_date:
                raise ValidationError(_("End Date cannot be before Start Date."))

    @api.constrains("vehicle_id", "status")
    def _check_single_active_per_vehicle(self):
        for rec in self:
            if rec.status != "active":
                continue
            dup = self.search_count(
                [
                    ("vehicle_id", "=", rec.vehicle_id.id),
                    ("status", "=", "active"),
                    ("id", "!=", rec.id),
                ]
            )
            if dup:
                raise ValidationError(
                    _("Vehicle %s already has an active driver assignment.") % rec.vehicle_id.display_name
                )
