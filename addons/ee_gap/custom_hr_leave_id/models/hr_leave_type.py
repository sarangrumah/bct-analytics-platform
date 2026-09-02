# -*- coding: utf-8 -*-
from odoo import fields, models


class HrLeaveType(models.Model):
    _inherit = "hr.leave.type"

    x_id_leave_category = fields.Selection(
        [
            ("cuti_tahunan", "Cuti Tahunan"),
            ("cuti_melahirkan", "Cuti Melahirkan"),
            ("cuti_haid", "Cuti Haid"),
            ("cuti_besar", "Cuti Besar"),
            ("cuti_alasan_penting", "Cuti Alasan Penting"),
            ("cuti_di_luar_tanggungan", "Cuti di Luar Tanggungan"),
        ],
        string="ID Leave Category",
        help="Indonesian regulatory category per UU Ketenagakerjaan / UU Cipta Kerja.",
    )
