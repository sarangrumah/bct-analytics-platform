# -*- coding: utf-8 -*-
from odoo import fields, models, tools


class CustomLeaveBalanceReport(models.Model):
    _name = "custom.leave.balance.report"
    _description = "Leave Balance Report"
    _auto = False
    _order = "year desc, employee_id, leave_type_id"
    _rec_name = "employee_id"

    employee_id = fields.Many2one("hr.employee", string="Employee", readonly=True)
    leave_type_id = fields.Many2one("hr.leave.type", string="Leave Type", readonly=True)
    year = fields.Integer(string="Year", readonly=True)
    allocated = fields.Float(string="Allocated Days", readonly=True)
    used = fields.Float(string="Used Days", readonly=True)
    remaining = fields.Float(string="Remaining Days", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS
            WITH alloc AS (
                SELECT
                    a.employee_id            AS employee_id,
                    a.holiday_status_id      AS leave_type_id,
                    EXTRACT(YEAR FROM COALESCE(a.date_from, a.create_date))::int AS year,
                    SUM(COALESCE(a.number_of_days, 0))::numeric AS allocated
                FROM hr_leave_allocation a
                WHERE a.state = 'validate'
                  AND a.employee_id IS NOT NULL
                  AND a.holiday_status_id IS NOT NULL
                GROUP BY a.employee_id, a.holiday_status_id,
                         EXTRACT(YEAR FROM COALESCE(a.date_from, a.create_date))
            ),
            used AS (
                SELECT
                    l.employee_id            AS employee_id,
                    l.holiday_status_id      AS leave_type_id,
                    EXTRACT(YEAR FROM COALESCE(l.date_from, l.create_date))::int AS year,
                    SUM(COALESCE(l.number_of_days, 0))::numeric AS used
                FROM hr_leave l
                WHERE l.state = 'validate'
                  AND l.employee_id IS NOT NULL
                  AND l.holiday_status_id IS NOT NULL
                GROUP BY l.employee_id, l.holiday_status_id,
                         EXTRACT(YEAR FROM COALESCE(l.date_from, l.create_date))
            ),
            joined AS (
                SELECT
                    COALESCE(a.employee_id, u.employee_id)     AS employee_id,
                    COALESCE(a.leave_type_id, u.leave_type_id) AS leave_type_id,
                    COALESCE(a.year, u.year)                   AS year,
                    COALESCE(a.allocated, 0)                   AS allocated,
                    COALESCE(u.used, 0)                        AS used
                FROM alloc a
                FULL OUTER JOIN used u
                    ON  u.employee_id   = a.employee_id
                    AND u.leave_type_id = a.leave_type_id
                    AND u.year          = a.year
            )
            SELECT
                ROW_NUMBER() OVER (ORDER BY year DESC, employee_id, leave_type_id)::int AS id,
                employee_id,
                leave_type_id,
                year,
                allocated,
                used,
                (allocated - used) AS remaining
            FROM joined
            """
        )
