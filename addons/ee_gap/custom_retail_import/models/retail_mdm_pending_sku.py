# -*- coding: utf-8 -*-
"""Registry of SKUs a sales file referenced before the product master had them.

X24DN parks a whole transaction when any of its lines quotes a SKU that is not in
the X101 master -- posting a partial order would leave it unbalanced against the
X70D tender. Until now the only trace of that was an error string on the source
line, so finishing the sale meant a human noticing the log, chasing the master, and
re-driving the import by hand.

This model turns that into a closed loop. Every parked SKU is recorded here with
the lines it blocked; when the master finally arrives -- from the X101 XLSX import
*or* from the MDM API, since both go through ``_x101_upsert_items`` -- the registry
resolves what it can and enqueues a replay of exactly the affected logs.

The replay is safe to run repeatedly: ``_post_x24``'s per-order external ID skips
transactions that already posted, and a transaction whose lines still do not all
resolve is simply parked again.
"""

from __future__ import annotations

import json
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class RetailMdmPendingSku(models.Model):
    _name = "retail.mdm.pending.sku"
    _description = "Sales SKU Awaiting Product Master"
    _order = "occurrence_count desc, id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(compute="_compute_name", store=True)
    item_code = fields.Char(string="Item Code", index=True, help="X24DN ITEM CODE, as sent.")
    waist = fields.Char()
    inseam = fields.Char()
    composite_code = fields.Char(
        index=True,
        help="item_code + waist + inseam -- the form X101 stores as the variant's internal reference.",
    )
    ean = fields.Char(index=True)
    description = fields.Char(help="Item description from the sales row, to help identify it.")
    store_code = fields.Char(index=True)

    first_seen_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    last_seen_at = fields.Datetime(default=fields.Datetime.now, readonly=True)
    occurrence_count = fields.Integer(default=0, readonly=True)

    parked_line_ids = fields.One2many("retail.import.line", "pending_sku_id", string="Parked Rows")
    parked_line_count = fields.Integer(compute="_compute_parked", store=True)
    parked_txn_count = fields.Integer(compute="_compute_parked", store=True, string="Parked Transactions")

    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("registered", "Registered"),
            ("replayed", "Replayed"),
            ("ignored", "Ignored"),
        ],
        default="pending",
        required=True,
        index=True,
        tracking=True,
    )
    resolved_product_id = fields.Many2one("product.product", ondelete="set null")
    resolved_at = fields.Datetime(readonly=True)
    replayed_at = fields.Datetime(readonly=True)
    last_error = fields.Char(readonly=True)
    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True, index=True)

    # Odoo 19 silently ignores _sql_constraints -- models.Constraint is the live form.
    _pending_uniq = models.Constraint(
        "unique(company_id, composite_code, ean)",
        "This SKU/EAN pair is already registered as pending.",
    )

    @api.depends("composite_code", "item_code", "ean")
    def _compute_name(self):
        for rec in self:
            rec.name = rec.composite_code or rec.item_code or rec.ean or _("Unknown SKU")

    @api.depends("parked_line_ids", "parked_line_ids.aggregate_key", "parked_line_ids.log_id")
    def _compute_parked(self):
        for rec in self:
            lines = rec.parked_line_ids
            rec.parked_line_count = len(lines)
            keys = set()
            for line in lines:
                keys.add(line.aggregate_key or f"{line.log_id.id}:{line.row_number}")
            rec.parked_txn_count = len(keys)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    @api.model
    def _record(self, row, line=None):
        """Upsert the pending entry for one unresolvable X24DN sales row."""
        Executor = self.env["retail.import.executor"]
        code = str(row.get("item_code") or "").strip()
        ean = str(row.get("ean") or "").strip()
        waist = Executor._x24_codepart(row.get("waist"))
        inseam = Executor._x24_codepart(row.get("inseam"))
        composite = (code + waist + inseam) if code else ""
        if not composite and not code and not ean:
            return self.browse()

        # The unique key is (company, composite_code, ean); NULL never equals NULL in
        # Postgres, so store "" rather than False to make the constraint actually bite.
        domain = [
            ("company_id", "=", self.env.company.id),
            ("composite_code", "=", composite or code or ""),
            ("ean", "=", ean or ""),
        ]
        rec = self.sudo().search(domain, limit=1)
        now = fields.Datetime.now()
        if rec:
            vals = {"last_seen_at": now, "occurrence_count": rec.occurrence_count + 1}
            if rec.state in ("registered", "replayed"):
                # Seen again after we thought it was resolved -- back to pending.
                vals["state"] = "pending"
            rec.write(vals)
        else:
            rec = self.sudo().create(
                {
                    "item_code": code or False,
                    "waist": waist or False,
                    "inseam": inseam or False,
                    "composite_code": composite or code or "",
                    "ean": ean or "",
                    "description": (str(row.get("item_description") or "").strip() or False),
                    "store_code": str(row.get("store_code") or "").strip() or False,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "occurrence_count": 1,
                }
            )
        if line:
            line.sudo().write({"pending_sku_id": rec.id})
        return rec

    # ------------------------------------------------------------------
    # Resolution + replay
    # ------------------------------------------------------------------
    @api.model
    def _resolve_and_replay(self, codes=(), gtins=()):
        """Mark pending SKUs that the just-loaded master covers, and replay their logs.

        Called from ``_x101_upsert_items``, so it fires for the XLSX import and the
        MDM API alike. ``codes`` are the template/variant internal references and
        ``gtins`` the barcodes registered by that run -- used only to narrow the
        candidate set; the actual decision is a real product lookup, using the same
        resolution order as ``_post_x24``'s ``resolve_product`` so the registry and
        the importer can never disagree.
        """
        codes = {str(c).strip() for c in (codes or ()) if str(c or "").strip()}
        gtins = {str(g).strip() for g in (gtins or ()) if str(g or "").strip()}
        if not codes and not gtins:
            return self.browse()

        pending = self.sudo().search([("state", "=", "pending")])
        if not pending:
            return self.browse()

        resolved = self.browse()
        for rec in pending:
            candidate = (rec.composite_code or "").strip()
            if not (
                (candidate and candidate in codes)
                or (rec.item_code and rec.item_code in codes)
                or (rec.ean and rec.ean in gtins)
            ):
                continue
            product = rec._resolve_product()
            if not product:
                continue
            rec.write(
                {
                    "state": "registered",
                    "resolved_product_id": product.id,
                    "resolved_at": fields.Datetime.now(),
                    "last_error": False,
                }
            )
            resolved |= rec

        if resolved:
            resolved._enqueue_replay()
        return resolved

    def _resolve_product(self):
        """Look the SKU up exactly the way the X24DN importer would."""
        self.ensure_one()
        Product = self.env["product.product"].sudo()
        if self.ean:
            product = Product._resolve_barcode(self.ean)
            if product:
                return product
        for code in (self.composite_code, self.item_code):
            code = (code or "").strip()
            if not code:
                continue
            product = Product.search([("default_code", "=", code)], limit=1)
            if product:
                return product
        if self.composite_code:
            product = Product.search([("mdm_sku_code", "=", self.composite_code)], limit=1)
            if product:
                return product
        return Product.browse()

    def _enqueue_replay(self):
        """One replay job per affected import log."""
        Executor = self.env["retail.import.executor"]
        by_log = {}
        for rec in self:
            for line in rec.parked_line_ids:
                by_log.setdefault(line.log_id.id, self.browse())
                by_log[line.log_id.id] |= rec
        for log_id, recs in by_log.items():
            if not log_id:
                continue
            Executor.with_delay(
                channel="root.retail_import",
                description=f"Replay parked X24 rows of log #{log_id}",
            )._job_replay_x24_parked(log_id, recs.ids)
        return True

    # ------------------------------------------------------------------
    # Ops actions
    # ------------------------------------------------------------------
    def action_replay(self):
        """Manual equivalent of the automatic trigger, for the ops list/form."""
        for rec in self:
            product = rec._resolve_product()
            if product:
                rec.write(
                    {
                        "state": "registered",
                        "resolved_product_id": product.id,
                        "resolved_at": fields.Datetime.now(),
                        "last_error": False,
                    }
                )
            else:
                rec.last_error = _("Still not in the product master.")
        ready = self.filtered(lambda r: r.state == "registered")
        if ready:
            ready._enqueue_replay()
        return True

    def action_ignore(self):
        self.write({"state": "ignored"})
        return True

    def action_reset_pending(self):
        self.write({"state": "pending", "last_error": False})
        return True

    def action_view_parked_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Parked Rows"),
            "res_model": "retail.import.line",
            "view_mode": "list,form",
            "domain": [("pending_sku_id", "=", self.id)],
        }


class RetailImportLinePending(models.Model):
    _inherit = "retail.import.line"

    pending_sku_id = fields.Many2one(
        "retail.mdm.pending.sku",
        string="Pending SKU",
        index=True,
        ondelete="set null",
        help="Set when this row was parked because its product was not in the master.",
    )


class RetailImportExecutorReplay(models.AbstractModel):
    _inherit = "retail.import.executor"

    def _job_replay_x24_parked(self, log_id, pending_ids=None):
        """Re-post the parked rows of one X24DN log.

        Feeds only the still-errored rows back through ``_post_x24`` in replay mode.
        The readiness rule is not re-implemented here: ``_post_x24`` already parks a
        whole transaction when any of its lines fails to resolve, so a transaction
        that is still incomplete is simply parked again, and one that is now complete
        posts. Transactions that posted on an earlier pass are skipped by their
        per-order external ID.
        """
        log = self.env["retail.import.log"].browse(log_id)
        if not log.exists():
            return False
        profile = log.profile_id
        if profile.file_type != "x24":
            _logger.info("replay skipped: log #%s is %s, not x24", log_id, profile.file_type)
            return False

        lines = self.env["retail.import.line"].search([("log_id", "=", log.id), ("state", "=", "error")])
        if not lines:
            return False

        records, row_to_line = [], {}
        for line in lines:
            try:
                row = json.loads(line.raw_data_json or "{}")
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            row["_row"] = line.row_number
            records.append(row)
            row_to_line[line.row_number] = line
        records = self._ri_drop_footer_rows(records)
        if not records:
            return False

        result = self._post_x24(profile, records, log, row_to_line, replay=True)

        if pending_ids:
            pending = self.env["retail.mdm.pending.sku"].browse(pending_ids).exists()
            done = pending.filtered(lambda p: all(ln.state != "error" for ln in p.parked_line_ids))
            if done:
                done.write({"state": "replayed", "replayed_at": fields.Datetime.now()})
        _logger.info("x24 replay of log #%s: %s", log_id, result)
        return result
