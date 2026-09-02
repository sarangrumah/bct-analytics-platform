# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CustomFleetBbmLog(models.Model):
    _name = "custom.fleet.bbm.log"
    _description = "Fleet BBM (Fuel) Log"
    _order = "date desc, id desc"
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
    date = fields.Date(
        string="Date",
        required=True,
        default=fields.Date.context_today,
        tracking=True,
    )
    odometer_km = fields.Integer(
        string="Odometer (km)",
        required=True,
        tracking=True,
        help="Odometer reading in kilometers at refuel.",
    )
    liter = fields.Float(
        string="Liter",
        required=True,
        digits=(12, 2),
    )
    currency_id = fields.Many2one(
        "res.currency",
        string="Currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    price_per_liter = fields.Monetary(
        string="Price / Liter",
        currency_field="currency_id",
        required=True,
    )
    total = fields.Monetary(
        string="Total",
        currency_field="currency_id",
        compute="_compute_total",
        store=True,
    )
    gas_station = fields.Char(string="Gas Station")
    receipt_attachment = fields.Binary(
        string="Receipt",
        attachment=True,
    )
    receipt_filename = fields.Char(string="Receipt Filename")

    consumption_km_per_l = fields.Float(
        string="Consumption (km/L)",
        compute="_compute_consumption",
        store=True,
        digits=(8, 2),
        help="Computed from delta odometer vs liters since the previous log.",
    )

    notes = fields.Text(string="Notes")

    # ---------- Computes ----------

    @api.depends("vehicle_id", "date")
    def _compute_name(self):
        for rec in self:
            v = rec.vehicle_id.license_plate or rec.vehicle_id.display_name or "-"
            rec.name = "BBM %s / %s" % (v, rec.date or "")

    @api.depends("liter", "price_per_liter")
    def _compute_total(self):
        for rec in self:
            rec.total = (rec.liter or 0.0) * (rec.price_per_liter or 0.0)

    @api.depends("vehicle_id", "date", "odometer_km", "liter")
    def _compute_consumption(self):
        for rec in self:
            consumption = 0.0
            if rec.vehicle_id and rec.odometer_km and rec.liter:
                domain = [
                    ("vehicle_id", "=", rec.vehicle_id.id),
                    ("odometer_km", "<", rec.odometer_km),
                ]
                if rec.id and isinstance(rec.id, int):
                    domain.append(("id", "!=", rec.id))
                prev = self.search(domain, order="odometer_km desc", limit=1)
                if prev and prev.odometer_km and rec.odometer_km > prev.odometer_km:
                    delta_km = rec.odometer_km - prev.odometer_km
                    if rec.liter > 0:
                        consumption = delta_km / rec.liter
            rec.consumption_km_per_l = consumption

    # ---------- Constraints ----------

    @api.constrains("liter", "price_per_liter", "odometer_km")
    def _check_positive(self):
        for rec in self:
            if rec.liter is not None and rec.liter <= 0:
                raise ValidationError(_("Liter must be greater than zero."))
            if rec.price_per_liter is not None and rec.price_per_liter < 0:
                raise ValidationError(_("Price per liter cannot be negative."))
            if rec.odometer_km is not None and rec.odometer_km < 0:
                raise ValidationError(_("Odometer cannot be negative."))

    # ---------- Side effects ----------

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._sync_vehicle_odometer()
        return records

    def write(self, vals):
        res = super().write(vals)
        if "odometer_km" in vals or "vehicle_id" in vals:
            self._sync_vehicle_odometer()
        return res

    def _sync_vehicle_odometer(self):
        """Push the highest odometer reading back to the vehicle for service-due calc."""
        for rec in self:
            if rec.vehicle_id and rec.odometer_km:
                if rec.odometer_km > (rec.vehicle_id.x_current_odometer or 0):
                    rec.vehicle_id.sudo().write({"x_current_odometer": rec.odometer_km})
