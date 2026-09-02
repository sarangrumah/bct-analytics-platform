# Part of custom_pdp_masking. Licence: LGPL-3.
"""Mask personal data on the export path.

`read()` is not the only way values leave Odoo. `export_data()` (the "Export" button in any list
view, and the same method over RPC) does NOT go through `read()`: it calls `_export_rows()`, which
reads each value with `record[name]` - `__getitem__`, straight out of the ORM cache. The `read()`
override in `pdp.masked.mixin` therefore never runs, and a user without `PDP / Data Viewer` who has
the standard `base.group_allow_export` right gets a CSV/XLSX of cleartext names, e-mails and
subscriber numbers.

Verified on the pinned image before writing this, against a real seeded partner and a real
non-viewer user::

    read()        -> {'name': '***8a2b1f58', 'email': '***49f22484'}
    export_data() -> [['Budi Santoso (Demo 001)', 'budi.santoso.001@contoh.invalid',
                       '+62-800-555-0001']]

An export is a bulk copy of personal data leaving the system, which is precisely the event
UU 27/2022 is concerned with, so this is the more important of the two paths.

Why this extends `base` rather than living in `pdp.masked.mixin`
---------------------------------------------------------------
`export_data()` is called on the model being exported, but an export path may *reach* personal data
on another model: exporting `sale.order` with the column `partner_id/email` returns a partner's
e-mail while never calling anything on `res.partner`. Putting the override only on the two models
that carry the mixin would leave that path open. Extending `base` means every export is checked,
and each column is resolved to the model and field it actually terminates on.

Consequence, stated so it is not a surprise: **the export surface is masked more broadly than the
UI read surface.** `sale.order.note` is classified `sensitive`, so it is blanked in an export, while
the form view still shows it (because `sale.order` does not carry the read-masking mixin - that
scope was kept narrow deliberately, see res_partner.py). Strictly safer, and asymmetric on purpose:
a value on screen is read by one person, a value in a spreadsheet is a copy that outlives the
session.
"""

import logging

from odoo import models

_logger = logging.getLogger(__name__)

#: Path segments that are identifiers rather than data, and are never masked.
_ID_SEGMENTS = frozenset({"id", ".id", "/id"})


class Base(models.AbstractModel):
    """Extends every model. Keep this override cheap: it runs on every export."""

    _inherit = "base"

    def _pdp_export_column_modes(self, fields_to_export):
        """Resolve each export column to ``'hash'``, ``'null'`` or ``None`` (do not mask).

        Walks each ``a/b/c`` path from ``self`` through relational fields and looks the terminal
        field up in the terminal model's UI mask plan.
        """
        masking = self.env["pdp.masking.rule"].sudo()
        plans = {}

        def plan_for(model_name):
            if model_name not in plans:
                plans[model_name] = masking._ui_mask_plan(model_name)
            return plans[model_name]

        modes = []
        for path in fields_to_export:
            parts = path.split("/") if isinstance(path, str) else list(path)
            model = self
            mode = None
            for position, part in enumerate(parts):
                if part in _ID_SEGMENTS:
                    break
                field = model._fields.get(part)
                if field is None:
                    break
                if position == len(parts) - 1:
                    excluded = getattr(model, "_pdp_ui_mask_exclude", ())
                    if part in excluded:
                        break
                    plan = plan_for(model._name)
                    mode = plan.get(part)
                    if mode is None and part in ("display_name", "complete_name"):
                        # A computed label derived from a masked `name` leaks it back.
                        if "name" in plan and "name" not in excluded:
                            mode = plan["name"]
                    break
                if not field.relational:
                    break
                model = self.env[field.comodel_name]
            modes.append(mode)
        return modes

    def export_data(self, fields_to_export):
        result = super().export_data(fields_to_export)
        # env.su covers module install, data loading, cron and the demo seeder.
        if self.env.su or self.env.user.has_group("custom_pdp_core.group_pdp_data_viewer"):
            return result
        try:
            modes = self._pdp_export_column_modes(fields_to_export)
        except Exception:  # noqa: BLE001 - never let masking analysis turn into a data leak
            _logger.exception(
                "custom_pdp_masking: could not resolve export columns for %s; "
                "blanking every column rather than exporting cleartext",
                self._name,
            )
            modes = ["null"] * len(fields_to_export)
        if not any(modes):
            return result
        masked_columns = [i for i, mode in enumerate(modes) if mode]
        for row in result.get("datas", []):
            for index in masked_columns:
                if index < len(row) and row[index]:
                    row[index] = self._pdp_export_mask_value(row[index], modes[index])
        _logger.info(
            "custom_pdp_masking: masked %d column(s) in a %s export for uid %s",
            len(masked_columns), self._name, self.env.uid,
        )
        return result

    def _pdp_export_mask_value(self, value, mode):
        """Reuse the mixin's token so an export and the UI agree on what a record looks like."""
        mixin = self.env["pdp.masked.mixin"]
        if mode == "null":
            return mixin._pdp_ui_blank()
        return mixin._pdp_ui_token(value)
