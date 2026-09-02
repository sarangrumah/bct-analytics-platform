# -*- coding: utf-8 -*-
"""#15 General Report Aset — monthly stock-opname of the fleet.

One row per fixed-asset unit (``custom.fixed.asset``), enriched best-effort with:

* **Operational state** — matched from ``rental.asset.state`` by serial number
  (available / on_rent / maintenance / retired).
* **Condition** — the ``condition`` (good / damaged / partial) on the most
  recent ``custom.bast.line`` whose lot serial matches the unit's serial.

Both enrichments are keyed on the serial string because there is no hard FK from
the accounting asset register to the rental/BAST records; units with no match
show a blank operational state / condition. This is a snapshot (no period).

``custom.fixed.asset.serial_number`` is contributed by the tenant module
``custom_asset_register_seed``, not by the generic asset app. This report
does not depend on that tenant module — depending on it would drag the tenant's
3,329-unit data seed into any database that merely wanted the report — so the
serial is read only when the field is present, and the enrichment degrades to
blank when it is not.
"""

from odoo import models


class CustomReportAssetOpname(models.AbstractModel):
    _name = "custom.report.asset.opname"
    _inherit = "custom.report.engine"
    _description = "Asset Opname Report"

    _report_code = "asset_opname"
    _report_title = "General Report Aset (Opname)"

    def _xlsx_columns(self):
        return [
            {"header": "No", "field": "seq", "kind": "number", "width": 6},
            {"header": "Code", "field": "code", "kind": "text", "width": 16},
            {"header": "Unit", "field": "name", "kind": "text", "width": 32},
            {"header": "Serial", "field": "serial", "kind": "text", "width": 22},
            {"header": "Group", "field": "group", "kind": "text", "width": 22},
            {"header": "Location", "field": "location", "kind": "text", "width": 22},
            {"header": "Asset State", "field": "state", "kind": "text", "width": 14},
            {"header": "Operational", "field": "op_state", "kind": "text", "width": 14},
            {"header": "Condition", "field": "condition", "kind": "text", "width": 12},
        ]

    def _serial_to_op_state(self, company_ids):
        """{serial_number: rental.asset.state} for units carrying a serial."""
        out = {}
        assets = self.env["rental.asset"].search(
            [("serial_number", "!=", False), ("company_id", "in", company_ids + [False])]
        )
        for asset in assets:
            out.setdefault(asset.serial_number, asset.state)
        return out

    def _serial_to_condition(self, company_ids):
        """{lot serial: latest custom.bast.line.condition}. The most recent BAST
        line (by document date) for a given serial wins."""
        out = {}
        # Newest first so the first write per serial is the latest condition.
        lines = self.env["custom.bast.line"].search(
            [("lot_id", "!=", False)],
            order="id desc",
        )
        for line in lines:
            serial = line.lot_id.name
            if serial and serial not in out:
                out[serial] = line.condition
        return out

    def _has_serial(self):
        """The serial lives on a tenant module; it may simply not be there."""
        return "serial_number" in self.env["custom.fixed.asset"]._fields

    def _build_lines(self, filters):
        company_ids = filters["company_ids"]
        domain = [("company_id", "in", company_ids)]
        if filters.get("group_ids"):
            domain.append(("group_id", "in", filters["group_ids"]))
        if filters.get("location_ids"):
            domain.append(("location_id", "in", filters["location_ids"]))
        if filters.get("state"):
            domain.append(("state", "=", filters["state"]))

        has_serial = self._has_serial()
        op_by_serial = self._serial_to_op_state(company_ids) if has_serial else {}
        cond_by_serial = self._serial_to_condition(company_ids) if has_serial else {}

        state_labels = dict(self.env["custom.fixed.asset"]._fields["state"]._description_selection(self.env))

        assets = self.env["custom.fixed.asset"].search(domain, order="code")
        lines = []
        n_good = n_damaged = n_on_rent = 0
        for idx, asset in enumerate(assets, start=1):
            serial = (asset.serial_number or "") if has_serial else ""
            op_state = op_by_serial.get(serial, "")
            condition = cond_by_serial.get(serial, "")
            if condition == "good":
                n_good += 1
            elif condition == "damaged":
                n_damaged += 1
            if op_state == "on_rent":
                n_on_rent += 1
            lines.append(
                {
                    "seq": idx,
                    "code": asset.code or "",
                    "name": asset.name or "",
                    "serial": serial,
                    "group": asset.group_id.name or "",
                    "location": asset.location_id.name or "",
                    "state": state_labels.get(asset.state, asset.state or ""),
                    "op_state": op_state,
                    "condition": condition,
                }
            )

        lines.append(
            {
                "type": "grand_total",
                "name": "Total units: %d | Good: %d | Damaged: %d | On rent: %d"
                % (len(assets), n_good, n_damaged, n_on_rent),
            }
        )
        return lines
