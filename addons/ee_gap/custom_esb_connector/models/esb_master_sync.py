# -*- coding: utf-8 -*-
"""Master-data sync: ESB → Odoo.

Every feed is an idempotent upsert keyed by the ESB identifier, so a re-run
changes nothing and a partial run resumes cleanly. Feeds are declared in
``MASTER_FEEDS`` so adding one is a one-line change plus a handler.

When no active adapter config exists the whole run logs ``skipped`` and returns
— the module stays installable and harmless long before ESB credentials arrive.
"""

from __future__ import annotations

import logging
import time

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .esb_adapter import ESB_CORE, ESB_COREV1, EsbApiError

_logger = logging.getLogger(__name__)

#: (feed key, adapter type, handler method). Order matters: branches before
#: locations, products before product details.
MASTER_FEEDS = [
    ("branch", ESB_CORE, "_upsert_branches"),
    ("location", ESB_CORE, "_upsert_locations"),
    ("purpose", ESB_CORE, "_upsert_purposes"),
    ("document_template", ESB_CORE, "_upsert_document_templates"),
    ("supplier", ESB_CORE, "_upsert_suppliers"),
    ("product", ESB_COREV1, "_upsert_products"),
]


class EsbMasterSync(models.AbstractModel):
    _name = "custom.esb.master.sync"
    _description = "ESB Master Data Sync"

    # ------------------------------------------------------------------
    # Adapter resolution
    # ------------------------------------------------------------------

    @api.model
    def _adapter(self, adapter_type=ESB_CORE, raise_if_missing=False):
        cfg = (
            self.env["custom.adapter.config"]
            .sudo()
            .search([("adapter_type", "=", adapter_type), ("status", "!=", "disabled")], limit=1)
        )
        if not cfg:
            if raise_if_missing:
                raise UserError(
                    _("No active '%s' adapter config. Configure the ESB hosts under Settings first.") % adapter_type
                )
            return None
        return cfg.get_adapter()

    @api.model
    def _enabled(self, param, default="0"):
        return (self.env["ir.config_parameter"].sudo().get_param(param, default) or "").strip().lower() in (
            "1",
            "true",
            "yes",
        )

    # ------------------------------------------------------------------
    # Cron entry point
    # ------------------------------------------------------------------

    @api.model
    def _cron_sync_masters(self):
        log = self.env["custom.esb.sync.log"]
        if not self._enabled("esb.master_sync_enabled"):
            log._record("pull", "master", "skipped", message="esb.master_sync_enabled is off")
            return False
        for key, adapter_type, handler in MASTER_FEEDS:
            self._run_feed(key, adapter_type, handler)
        return True

    @api.model
    def _run_feed(self, key, adapter_type, handler):
        log = self.env["custom.esb.sync.log"]
        adapter = self._adapter(adapter_type)
        if adapter is None:
            log._record("pull", "master:%s" % key, "skipped", message="No active %s adapter config" % adapter_type)
            return False
        t0 = time.time()
        try:
            stats = getattr(self, handler)(adapter)
        except (EsbApiError, UserError) as exc:
            log._record("pull", "master:%s" % key, "error", message=str(exc))
            return False
        except Exception as exc:  # pragma: no cover - unexpected
            _logger.exception("ESB master feed %s failed", key)
            log._record("pull", "master:%s" % key, "error", message=str(exc))
            return False
        log._record(
            "pull",
            "master:%s" % key,
            "ok",
            duration_ms=int((time.time() - t0) * 1000),
            **stats,
        )
        return True

    # ------------------------------------------------------------------
    # Upsert helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _stats(created=0, updated=0, received=0):
        return {"created_count": created, "updated_count": updated, "record_count": received}

    @api.model
    def _upsert(self, model, domain, vals):
        """Create or update one record; return ``"created"`` or ``"updated"``."""
        rec = self.env[model].sudo().with_context(active_test=False).search(domain, limit=1)
        if rec:
            rec.write(vals)
            return "updated", rec
        return "created", self.env[model].sudo().create(vals)

    # ------------------------------------------------------------------
    # Feeds
    # ------------------------------------------------------------------

    @api.model
    def _upsert_branches(self, adapter):
        created = updated = received = 0
        now = fields.Datetime.now()
        company = self.env.company
        seen = []
        for row in adapter.get_rows("branch"):
            esb_id = row.get("branchID")
            if not esb_id:
                continue
            received += 1
            seen.append(esb_id)
            action, rec = self._upsert(
                "custom.esb.branch",
                [("esb_branch_id", "=", esb_id), ("company_id", "=", company.id)],
                {
                    "esb_branch_id": esb_id,
                    "code": row.get("branchCode"),
                    "name": row.get("branchName") or str(esb_id),
                    "company_id": company.id,
                    "active": True,
                    "last_synced_at": now,
                },
            )
            created += action == "created"
            updated += action == "updated"
        # /branch returns only active branches, so anything missing is archived
        # in ESB. Archive rather than delete — snapshots and history point at it.
        if seen:
            stale = (
                self.env["custom.esb.branch"]
                .sudo()
                .search([("company_id", "=", company.id), ("esb_branch_id", "not in", seen), ("active", "=", True)])
            )
            stale.write({"active": False})
        return self._stats(created, updated, received)

    @api.model
    def _upsert_locations(self, adapter):
        created = updated = received = 0
        now = fields.Datetime.now()
        branches = self.env["custom.esb.branch"].sudo().search([("active", "=", True)])
        for branch in branches:
            for row in adapter.get_rows("location", {"branchID": branch.esb_branch_id}):
                esb_id = row.get("locationID")
                if not esb_id:
                    continue
                received += 1
                action, _rec = self._upsert(
                    "custom.esb.location",
                    [("esb_location_id", "=", esb_id), ("branch_id", "=", branch.id)],
                    {
                        "esb_location_id": esb_id,
                        "name": row.get("locationName") or str(esb_id),
                        "branch_id": branch.id,
                        "active": True,
                        "last_synced_at": now,
                    },
                )
                created += action == "created"
                updated += action == "updated"
        return self._stats(created, updated, received)

    @api.model
    def _upsert_purposes(self, adapter):
        created = updated = received = 0
        for row in adapter.iter_rows("purpose", {"flagActive": 1}):
            esb_id = row.get("purposeID")
            if not esb_id:
                continue
            received += 1
            action, _rec = self._upsert(
                "custom.esb.purpose",
                [("esb_purpose_id", "=", esb_id)],
                {
                    "esb_purpose_id": esb_id,
                    "name": row.get("purposeName") or str(esb_id),
                    "account_code": row.get("purposeAccount"),
                    "applied_to": row.get("purposeAppliedTo"),
                    "active": bool(row.get("flagActive", 1)),
                },
            )
            created += action == "created"
            updated += action == "updated"
        return self._stats(created, updated, received)

    @api.model
    def _upsert_document_templates(self, adapter):
        created = updated = received = 0
        for row in adapter.iter_rows("document-template", {"flagActive": 1}):
            esb_id = row.get("requestTemplateID")
            if not esb_id:
                continue
            received += 1
            action, _rec = self._upsert(
                "custom.esb.document.template",
                [("esb_template_id", "=", esb_id)],
                {
                    "esb_template_id": esb_id,
                    "name": row.get("requestTemplateName") or str(esb_id),
                    "branch_names": row.get("branchNames"),
                    "active": bool(row.get("flagActive", 1)),
                },
            )
            created += action == "created"
            updated += action == "updated"
        return self._stats(created, updated, received)

    @api.model
    def _upsert_suppliers(self, adapter):
        created = updated = received = 0
        for row in adapter.iter_rows("supplier", {"flagActive": 1}):
            esb_id = row.get("supplierID")
            if not esb_id:
                continue
            received += 1
            action, _rec = self._upsert(
                "custom.esb.supplier",
                [("esb_supplier_id", "=", esb_id)],
                {
                    "esb_supplier_id": esb_id,
                    "code": row.get("supplierCode"),
                    "name": row.get("supplierName") or str(esb_id),
                    "due_days": row.get("dueDate") or 0,
                    "category": row.get("category"),
                    "contact_person": row.get("contactPerson"),
                    "phone": row.get("cellPhone"),
                    "active": bool(row.get("flagActive", 1)),
                },
            )
            created += action == "created"
            updated += action == "updated"
        return self._stats(created, updated, received)

    @api.model
    def _upsert_products(self, adapter):
        """Pull ``/corev1/master/product`` — the only feed carrying productDetails.

        One ESB product becomes one ``product.product``; its per-unit
        ``productDetail`` rows become ``custom.esb.product.detail`` records, and
        the stock-unit detail is denormalised onto the product for fast lookup.
        """
        created = updated = received = 0
        now = fields.Datetime.now()
        for row in adapter.iter_rows("corev1/master/product", {"statusActive": "Yes"}):
            esb_id = row.get("productID")
            if not esb_id:
                continue
            received += 1
            details = row.get("productDetails") or []
            stock_detail = next((d for d in details if (d.get("defaultUnit") or {}).get("stockUnit")), None)
            if stock_detail is None and details:
                stock_detail = details[0]
            product_vals = {
                "name": row.get("productName") or row.get("productCode") or str(esb_id),
                "x_esb_product_id": esb_id,
                "x_esb_product_code": row.get("productCode"),
                "x_esb_product_detail_id": (stock_detail or {}).get("productDetailID") or 0,
                "x_esb_synced_at": now,
            }
            product = (
                self.env["product.product"]
                .sudo()
                .with_context(active_test=False)
                .search([("x_esb_product_id", "=", esb_id)], limit=1)
            )
            if product:
                # Never overwrite a name a user has curated in Odoo; only fill it
                # in when it is still whatever the first sync created.
                product.write({k: v for k, v in product_vals.items() if k != "name"})
                updated += 1
            else:
                product_vals.update({"type": "consu", "is_storable": True, "default_code": row.get("productCode")})
                product = self.env["product.product"].sudo().create(product_vals)
                created += 1
            for seq, det in enumerate(details, start=1):
                det_id = det.get("productDetailID")
                if not det_id:
                    continue
                default_unit = det.get("defaultUnit") or {}
                self._upsert(
                    "custom.esb.product.detail",
                    [("esb_product_detail_id", "=", det_id)],
                    {
                        "esb_product_detail_id": det_id,
                        "esb_product_id": esb_id,
                        "product_id": product.id,
                        "sequence": seq * 10,
                        "unit_name": det.get("unit"),
                        "conversion_factor": det.get("conversionFactor") or 1.0,
                        "base_price": det.get("basePrice") or 0.0,
                        "sku": det.get("sku"),
                        "is_stock_unit": bool(default_unit.get("stockUnit")),
                        "is_purchase_unit": bool(default_unit.get("purchaseUnit")),
                        "is_base_unit": bool(default_unit.get("baseUnit")),
                        "is_transfer_unit": bool(default_unit.get("transferUnit")),
                        "is_sales_unit": bool(default_unit.get("salesUnit")),
                    },
                )
        return self._stats(created, updated, received)

    # ------------------------------------------------------------------
    # Manual trigger
    # ------------------------------------------------------------------

    @api.model
    def action_sync_now(self):
        """Run every feed regardless of ``esb.master_sync_enabled`` (button)."""
        for key, adapter_type, handler in MASTER_FEEDS:
            self._run_feed(key, adapter_type, handler)
        return True
