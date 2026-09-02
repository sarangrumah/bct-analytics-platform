# -*- coding: utf-8 -*-
from odoo import api, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    home_pinned_action_ids = fields.Many2many(
        comodel_name="ir.actions.actions",
        relation="res_users_home_pinned_action_rel",
        column1="user_id",
        column2="action_id",
        string="Pinned Shortcuts",
        help="Actions pinned by the user on the Home Console. Shown above the regular app cards.",
    )
    home_console_density = fields.Selection(
        selection=[("comfort", "Comfortable"), ("compact", "Compact")],
        default="comfort",
        string="Home Layout Density",
    )

    @api.model
    def home_console_bootstrap(self):
        """Single RPC payload for the Home Console mount.

        Returns the data the OWL component needs in one round-trip:
        company branding, pinned shortcuts, recent activities, and the
        getting-started checklist.
        """
        user = self.env.user
        company = user.company_id

        pinned = []
        for action in user.home_pinned_action_ids:
            pinned.append(
                {
                    "id": action.id,
                    "name": action.name,
                    "type": action.type,
                }
            )

        recent_activities = []
        Activity = self.env.get("mail.activity")
        if Activity is not None:
            activities = Activity.search(
                [("user_id", "=", user.id)],
                order="date_deadline asc, id desc",
                limit=5,
            )
            for act in activities:
                recent_activities.append(
                    {
                        "id": act.id,
                        "summary": act.summary or act.activity_type_id.name or "",
                        "res_model": act.res_model,
                        "res_id": act.res_id,
                        "date_deadline": act.date_deadline and act.date_deadline.isoformat() or None,
                    }
                )

        checklist = self._home_console_checklist()

        return {
            "user": {
                "id": user.id,
                "name": user.name,
                "density": user.home_console_density or "comfort",
            },
            "company": {
                "id": company.id,
                "name": company.name,
                "accent": company.brand_accent_color or "#714B67",
                "has_home_logo": bool(company.brand_logo_home),
                "has_logo": bool(company.logo),
                "announcement_html": company.home_announcement_active and company.home_announcement_html or "",
            },
            "pinned": pinned,
            "recent_activities": recent_activities,
            "checklist": checklist,
        }

    def _home_console_checklist(self):
        """Lightweight onboarding checklist (best-effort, never raises).

        Each item: {key, label, done}. Probes existing models without
        hard-depending on optional addons.
        """
        env = self.env
        company = self.env.user.company_id
        items = []

        items.append(
            {
                "key": "company_vat",
                "label": "Set company tax ID (NPWP)",
                "done": bool(company.vat),
            }
        )

        Employee = env.get("hr.employee")
        if Employee is not None:
            items.append(
                {
                    "key": "has_employee",
                    "label": "Add at least one employee",
                    "done": bool(Employee.sudo().search_count([("company_id", "=", company.id)], limit=1)),
                }
            )

        Product = env.get("product.template")
        if Product is not None:
            items.append(
                {
                    "key": "has_product",
                    "label": "Create a product",
                    "done": bool(Product.sudo().search_count([], limit=1)),
                }
            )

        return items
