# -*- coding: utf-8 -*-
"""Product-master attributes fed by the apparel brand MDM HUB.

The X101 XLSX report carries a narrow slice of the master (code, description, the
three-level category, size/inseam, GTIN, retail price). The MDM JSON feed carries far
more, and some of it is dangerous to apply blindly to a live ledger -- writing
``standard_price`` on a storable product with stock posts an inventory revaluation
entry, and archiving a template cascades to its variants and can break open POS
orders and quants.

So the rule here is: **always record, conditionally apply**. Every incoming value is
stored on an ``mdm_*`` field (and the whole raw item in ``mdm_raw_json``, so nothing is
lost and a mapping can be added later without asking the apparel brand to re-send). Only the
values that cannot surprise finance are written to the real Odoo fields; the rest sit
behind a default-off ``ir.config_parameter`` gate.
"""

from __future__ import annotations

import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

MDM_SOURCES = [
    ("x101_file", "X101 File"),
    ("mdm_api", "MDM API"),
    ("x24_autoregister", "X24DN Auto-register"),
]


def _yes(value):
    """the apparel brand sends Yes/No strings, not booleans."""
    return str(value or "").strip().lower() in ("yes", "y", "true", "1")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    # -- provenance -----------------------------------------------------
    mdm_source = fields.Selection(MDM_SOURCES, string="MDM Source", copy=False, index=True)
    mdm_synced_at = fields.Datetime(string="MDM Synced At", copy=False)
    mdm_request_id = fields.Char(string="MDM Request", copy=False)
    mdm_content_hash = fields.Char(string="MDM Content Hash", copy=False, index=True)
    mdm_pending = fields.Boolean(
        string="Awaiting MDM Master",
        copy=False,
        index=True,
        help="Created from a sales row before the product master arrived. Upgraded in "
        "place (same record id, so posted POS lines stay valid) once MDM sends it.",
    )
    mdm_category_unmapped = fields.Boolean(
        string="MDM Category Unmapped",
        copy=False,
        index=True,
        help="The MDM category1/category2 pair had no crosswalk entry, so the category "
        "was derived. Review before trusting this product's revenue/COGS accounts.",
    )

    # -- recorded master attributes -------------------------------------
    mdm_template_code = fields.Char(
        string="MDM Mainline Code",
        copy=False,
        index=True,
        help="udf1, the mainline code this template was keyed on.\n"
        "Kept separately because ``default_code`` cannot serve as that identity: on a "
        "single-variant template Odoo mirrors the variant's own code into it, so a "
        "jeans template with one size ends up showing the PROD SKU rather than the "
        "mainline code.",
    )
    mdm_brand = fields.Char(string="MDM Brand", copy=False)
    mdm_season = fields.Char(string="MDM Season", copy=False)
    mdm_gender = fields.Char(string="MDM Gender", copy=False)
    mdm_segment = fields.Char(string="MDM Segment", copy=False)
    mdm_classification = fields.Char(string="MDM Classification", copy=False)
    mdm_vendor_code = fields.Char(string="MDM Vendor Code", copy=False)
    mdm_base_cost = fields.Float(
        string="MDM Base Cost",
        copy=False,
        help="baseCost as sent. Only copied to Standard Price when "
        "retail_import.mdm_write_cost is on -- writing cost on a stocked product "
        "posts an inventory revaluation entry.",
    )
    mdm_active_flag = fields.Boolean(
        string="MDM isActive",
        default=True,
        copy=False,
        help="isActive as sent. Only applied to the Active field when retail_import.mdm_apply_active is on.",
    )
    mdm_length = fields.Float(string="MDM Length", copy=False)
    mdm_width = fields.Float(string="MDM Width", copy=False)
    mdm_height = fields.Float(string="MDM Height", copy=False)
    mdm_raw_json = fields.Json(
        string="MDM Raw Payload",
        copy=False,
        help="The item exactly as MDM sent it, including fields we do not map yet.",
    )


class ProductProduct(models.Model):
    _inherit = "product.product"

    mdm_sku_code = fields.Char(
        string="MDM SKU Code",
        copy=False,
        index=True,
        help="MDM's skuCode (e.g. 002IJ-00273228). This is NOT the internal reference: "
        "X101's PROD SKU -- and therefore default_code -- is udf2 (002IJ002703228). "
        "Kept as an additional lookup key so a sales row quoting either form resolves.",
    )


class RetailImportExecutorMdm(models.AbstractModel):
    """Extended-attribute application, mixed into ``retail.import.executor``."""

    _inherit = "retail.import.executor"

    # ------------------------------------------------------------------
    # Gates -- everything that can surprise finance is off by default
    # ------------------------------------------------------------------
    def _mdm_flag(self, name, default="0"):
        return self.env["ir.config_parameter"].sudo().get_param(f"retail_import.{name}", default) in (
            "1",
            "true",
            "True",
        )

    def _mdm_apply_extended(self, template_ids, variant_ids, tmpl_mdm, sku_mdm):
        """Write the extended MDM attributes onto templates and variants.

        ``template_ids`` / ``variant_ids`` are the {code: id} maps the X101 seam
        returns; ``tmpl_mdm`` / ``sku_mdm`` the per-code extended payloads. A no-op
        for the XLSX path, which never populates them.
        """
        write_cost = self._mdm_flag("mdm_write_cost")
        apply_active = self._mdm_flag("mdm_apply_active")
        Template = self.env["product.template"]
        has_hs_code = "hs_code" in Template._fields

        for code, mdm in tmpl_mdm.items():
            tmpl_id = template_ids.get(code)
            if not tmpl_id:
                continue
            tmpl = Template.browse(tmpl_id)
            if not tmpl.exists():
                continue
            vals = self._mdm_template_vals(tmpl, mdm, write_cost, apply_active, has_hs_code)
            if vals:
                tmpl.with_context(tracking_disable=True, mail_notrack=True).write(vals)

        for sku, mdm in sku_mdm.items():
            product_id = variant_ids.get(sku)
            if not product_id:
                continue
            variant = self.env["product.product"].browse(product_id)
            if not variant.exists():
                continue
            sku_code = (mdm.get("sku_code") or "").strip()
            if sku_code and variant.mdm_sku_code != sku_code:
                variant.with_context(tracking_disable=True).write({"mdm_sku_code": sku_code})
            # GTINs the seam did not take as the primary barcode still have to resolve
            # at the till, so they land in the alias table.
            if mdm.get("extra_gtins"):
                self._x101_register_gtins(variant, mdm["extra_gtins"])

    def _mdm_template_vals(self, tmpl, mdm, write_cost, apply_active, has_hs_code):
        """Build the write() vals for one template. Split out so it is unit-testable."""
        vals = {
            "mdm_template_code": mdm.get("template_code") or False,
            # The seam only sets name/list_price when it *creates* a template -- the
            # file import re-reads the whole master every time, so it never needed to
            # update. A near-realtime feed is mostly updates (price changes above all),
            # so MDM is authoritative for these two on every message.
            **({"name": mdm["name"]} if mdm.get("name") else {}),
            **({"list_price": mdm["list_price"]} if mdm.get("list_price") is not None else {}),
            "mdm_source": mdm.get("source") or "mdm_api",
            "mdm_synced_at": mdm.get("synced_at") or fields.Datetime.now(),
            "mdm_brand": mdm.get("brand") or False,
            "mdm_season": mdm.get("season") or False,
            "mdm_gender": mdm.get("gender") or False,
            "mdm_segment": mdm.get("segment") or False,
            "mdm_classification": mdm.get("classification") or False,
            "mdm_vendor_code": mdm.get("vendor_code") or False,
            "mdm_raw_json": mdm.get("raw") or False,
        }
        if mdm.get("request_id"):
            vals["mdm_request_id"] = mdm["request_id"]
        if mdm.get("content_hash"):
            vals["mdm_content_hash"] = mdm["content_hash"]
        if mdm.get("category_unmapped") is not None:
            vals["mdm_category_unmapped"] = bool(mdm["category_unmapped"])

        # isSaleable is safe: it blocks new sales without touching history.
        if mdm.get("is_saleable") is not None:
            vals["sale_ok"] = bool(mdm["is_saleable"])

        # isActive is NOT safe -- archiving cascades to variants and can break open
        # POS/SO/quant references. Record it; apply only behind the gate.
        if mdm.get("is_active") is not None:
            vals["mdm_active_flag"] = bool(mdm["is_active"])
            if apply_active and tmpl.active != bool(mdm["is_active"]):
                vals["active"] = bool(mdm["is_active"])

        # baseCost drives inventory valuation. Record always; write only when the gate
        # is on AND the product cannot trigger a revaluation entry.
        cost = mdm.get("base_cost")
        if cost is not None:
            vals["mdm_base_cost"] = cost
            if write_cost and cost > 0 and not tmpl.qty_available:
                vals["standard_price"] = cost

        # tracking is create-only: changing it on a product with quants/moves is
        # refused by Odoo or corrupts traceability. The caller flags the mismatch.
        tracking = mdm.get("tracking")
        if tracking and not tmpl.mdm_source and tmpl.tracking != tracking:
            vals["tracking"] = tracking

        # A crosswalk entry that pins a category directly bypasses the three-level
        # tree the seam builds from category/klass/subclass.
        if mdm.get("categ_id"):
            vals["categ_id"] = mdm["categ_id"]
        if has_hs_code and mdm.get("hs_code"):
            vals["hs_code"] = mdm["hs_code"]
        if mdm.get("weight"):
            vals["weight"] = mdm["weight"]
        for key in ("length", "width", "height"):
            value = mdm.get(key)
            if value:
                vals[f"mdm_{key}"] = value

        # Drop no-op writes so a full daily re-push does not churn every row.
        def _changed(field, value):
            current = tmpl[field]
            if hasattr(current, "_name"):
                # Relational: compare ids, not recordset-to-int (which Odoo warns on).
                return (current.id or False) != (value or False)
            return current != value

        return {k: v for k, v in vals.items() if _changed(k, v)}

    @api.model
    def _mdm_tracking_conflict(self, tmpl, tracking):
        """True when MDM wants a different tracking mode than a live product has.

        Reported as needs_review rather than written: Odoo refuses the change once
        quants exist, and forcing it would break traceability on posted moves.
        """
        if not tracking or not tmpl or tmpl.tracking == tracking:
            return False
        return bool(tmpl.mdm_source) or bool(tmpl.qty_available)
