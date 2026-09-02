# -*- coding: utf-8 -*-
"""Turns staged MDM items into X101-shaped records and applies them.

The whole point of this file is that it does **not** write products itself. It maps,
it decides, and it hands the result to ``retail.import.executor._x101_upsert_items``
-- the same seam the X101 XLSX import goes through. Anything that diverges here would
show up as a product the two routes disagree about, which is exactly the failure this
integration cannot afford.

Two things about the payload are worth stating plainly, because they are not obvious
from the sample and getting either wrong is expensive:

``udf2``, not ``skuCode``, is the variant key.
    X101 composes its PROD SKU as ``PRODUCT_CODE without dashes + "0" + SIZE + INSEAM``
    (verified across all 214,305 rows of the material master). For the sample item that
    is ``002IJ0027`` + ``0`` + ``32`` + ``28`` = ``002IJ002703228``, which is exactly
    ``udf2``. ``skuCode`` (``002IJ-00273228``) keeps the dash and appears nowhere in
    X101. Writing it to ``default_code`` would create a parallel SKU namespace and every
    X24DN composite lookup would miss, so it goes to ``mdm_sku_code`` instead.

One ``upc_ean`` per message, many GTINs per SKU.
    Barcodes therefore accumulate as ``product.barcode`` aliases and are never
    overwritten. A GTIN that already belongs to a *different* variant is refused
    outright: stealing it would silently redirect every POS scan of that code.
"""

from __future__ import annotations

import logging

from odoo import _, api, fields, models

from .retail_mdm_request import DEFAULT_NAMESPACE, _num, _text, _yes

_logger = logging.getLogger(__name__)


class RetailMdmProcessor(models.AbstractModel):
    _name = "retail.mdm.processor"
    _description = "MDM Product-Master Processor"

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------
    @api.model
    def _process(self, request):
        """Map, screen and upsert every pending item of ``request``."""
        Executor = self.env["retail.import.executor"]
        namespace = self._namespace(request.company_id)
        records = []

        for item in request.item_ids.filtered(lambda i: i.state == "pending"):
            try:
                record, notes = self._prepare_item(item, request, namespace)
            except Exception as exc:  # noqa: BLE001
                _logger.exception("MDM item %s failed to map", item.sku_code)
                item.write({"state": "error", "error": str(exc)[:255], "processed_at": fields.Datetime.now()})
                continue
            if record is not None:
                records.append((item, record, notes))

        if records and not request.dry_run:
            # commit=False: this runs inside a queue job, where queue_job forbids
            # committing -- and one MDM message should be atomic anyway.
            summary = Executor._x101_upsert_items([r for _i, r, _n in records], namespace, commit=False)
            self._apply_results(records, summary)
        elif records:
            # Shadow mode: everything above ran (validation, crosswalk, conflict
            # screening) but nothing touched master data.
            for item, _record, notes in records:
                item.write(
                    {
                        "state": "skipped",
                        "error": "; ".join(notes + [_("dry run: not written")])[:255],
                        "processed_at": fields.Datetime.now(),
                    }
                )

        request._rollup()
        return True

    def _namespace(self, company=None):
        """The external-ID namespace, taken from the X101 profile so both routes match.

        Falls back to a company-agnostic lookup: called from the controller or a job
        there may be no company in the environment at all.
        """
        Profile = self.env["retail.import.profile"].sudo()
        company = company or self.env.company
        profile = Profile.browse()
        if company:
            profile = Profile.search([("file_type", "=", "x101"), ("company_id", "=", company.id)], limit=1)
        if not profile:
            profile = Profile.search([("file_type", "=", "x101")], order="id", limit=1)
        return (profile.namespace if profile else "") or DEFAULT_NAMESPACE

    # ------------------------------------------------------------------
    # Per-item mapping + screening
    # ------------------------------------------------------------------
    def _prepare_item(self, item, request, namespace):
        """Return ``(record, notes)`` for one item, or ``(None, [])`` to skip it.

        Setting the item's own state is this method's job; returning None means it has
        already been decided (duplicate, conflict, stale) and must not reach the seam.
        ``notes`` are review remarks the caller folds into the final item state.
        """
        payload = item.payload or {}
        Executor = self.env["retail.import.executor"]
        Product = self.env["product.product"].sudo()

        template_code = _text(payload.get("udf1")) or _text(payload.get("skuCode"))
        prod_sku = _text(payload.get("udf2"))
        sku_code = _text(payload.get("skuCode"))
        ean = _text(payload.get("upc_ean"))
        if not template_code:
            item.write({"state": "error", "error": _("No udf1/skuCode to key the template on.")})
            return None, []
        if not prod_sku:
            # Without udf2 we cannot produce X101's PROD SKU. Guessing it from skuCode
            # would create a variant under a key nothing else in the system uses.
            item.write({"state": "error", "error": _("No udf2 (PROD SKU) in the payload.")})
            return None, []

        # -- template identity: adopt / upgrade / refuse -----------------
        template, problem = self._resolve_template(namespace, template_code, prod_sku, ean)
        if problem:
            item.write({"state": "conflict", "error": problem, "processed_at": fields.Datetime.now()})
            return None, []

        # -- unchanged since last time? ---------------------------------
        if template and template.mdm_content_hash and template.mdm_content_hash == item.content_hash:
            item.write(
                {
                    "state": "duplicate",
                    "template_id": template.id,
                    "processed_at": fields.Datetime.now(),
                }
            )
            template.mdm_synced_at = fields.Datetime.now()
            return None, []

        # -- out-of-order message? --------------------------------------
        # IBM MQ / Mulesoft do not guarantee per-SKU ordering, so a retried older
        # message must not silently revert a newer price.
        if template and template.mdm_synced_at and template.mdm_synced_at > request.received_at:
            item.write(
                {
                    "state": "skipped",
                    "error": _("Stale message: a newer update was already applied."),
                    "template_id": template.id,
                    "processed_at": fields.Datetime.now(),
                }
            )
            return None, []

        # -- GTIN ownership ---------------------------------------------
        gtin = ean
        extra_gtins = []
        if ean:
            owner = Product._resolve_barcode(ean)
            if owner and owner.default_code and owner.default_code != prod_sku:
                item.write(
                    {
                        "state": "conflict",
                        "error": _("GTIN %(ean)s already belongs to %(code)s (product %(id)s).")
                        % {"ean": ean, "code": owner.default_code, "id": owner.id},
                        "processed_at": fields.Datetime.now(),
                    }
                )
                return None, []

            # A SKU has several GTINs but a message carries one, so successive messages
            # must accumulate aliases rather than take turns owning the primary
            # barcode. The seam writes whatever ``gtin`` it is given onto
            # ``product.product.barcode``, so once a variant already has a different
            # one, this GTIN is routed to the alias table instead.
            existing = Product.search([("default_code", "=", prod_sku)], limit=1)
            if existing and existing.barcode and existing.barcode != ean:
                gtin = ""
                extra_gtins = [ean]

        # -- size / inseam ----------------------------------------------
        size, inseam, size_ok = Executor._mdm_split_size(payload.get("size"), prod_sku, template_code)
        notes = [] if size_ok else [_("size %r does not agree with udf2") % payload.get("size")]

        # -- category ----------------------------------------------------
        triple, mapped = self.env["retail.mdm.category.map"].resolve(
            payload.get("udf8"), payload.get("category1"), payload.get("category2")
        )
        pinned_categ_id = False
        if triple and triple[0] == "__pinned__":
            pinned_categ_id = int(triple[1])
            category = klass = subclass = ""
        else:
            category, klass, subclass = triple
        if not mapped:
            notes.append(_("category not in the crosswalk"))

        # -- tracking is create-only -------------------------------------
        tracking = "serial" if _yes(payload.get("serialTrackingRequired")) else "none"
        if template and Executor._mdm_tracking_conflict(template, tracking):
            notes.append(_("serialTrackingRequired differs from the live tracking mode"))
            tracking = None

        record = {
            "_row": item.id,
            "product_code": template_code,
            "description": _text(payload.get("detailDesc")) or _text(payload.get("skuName")) or template_code,
            "brand": _text(payload.get("brand")),
            "category": category,
            "klass": klass,
            "subclass": subclass,
            "sku": prod_sku,
            "size": size,
            "inseam": inseam,
            "gtin": gtin,
            "retail_price": _num(payload.get("salePrice")),
            "price_eff": request.received_at,
            "_mdm": {
                "source": "mdm_api",
                "template_code": template_code,
                "name": _text(payload.get("detailDesc")) or _text(payload.get("skuName")),
                "list_price": _num(payload.get("salePrice")),
                "synced_at": request.received_at,
                "request_id": request.request_id,
                "content_hash": item.content_hash,
                "sku_code": sku_code,
                "extra_gtins": extra_gtins,
                "brand": _text(payload.get("brand")),
                "season": _text(payload.get("udf4")),
                "gender": _text(payload.get("udf8")),
                "segment": _text(payload.get("udf10")),
                "classification": _text(payload.get("classification")),
                "vendor_code": _text(payload.get("vendorCode")),
                "hs_code": _text(payload.get("taxCategory")),
                "base_cost": _num(payload.get("baseCost")),
                "is_active": _yes(payload.get("isActive")),
                "is_saleable": _yes(payload.get("isSaleable")),
                "tracking": tracking,
                "weight": _num(payload.get("weight")),
                "length": _num(payload.get("length")),
                "width": _num(payload.get("width")),
                "height": _num(payload.get("height")),
                "categ_id": pinned_categ_id,
                "category_unmapped": not mapped,
                "raw": dict(payload),
            },
        }
        return record, notes

    def _resolve_template(self, namespace, template_code, prod_sku, ean):
        """Find (or adopt, or upgrade) the template this item belongs to.

        Returns ``(template_or_empty, problem_message_or_None)``.

        Four cases, in the order they are checked:

        1. The external ID exists -- reuse it, unless ``mdm_template_code`` disagrees
           with the incoming code. That only happens when two distinct codes collapse
           to the same ``_safe_xid`` (all non-alphanumerics become ``_``, so
           ``002IJ-0027`` and ``002IJ.0027`` collide). Low probability, catastrophic
           if silent, so it is refused.

           The comparison deliberately uses ``mdm_template_code`` and not
           ``default_code``: Odoo mirrors a lone variant's code into its template, so
           a one-size template's ``default_code`` is the PROD SKU, and comparing that
           against ``udf1`` would flag every such product as a collision.
        2. No external ID but a template already carries this ``default_code`` --
           adopt it. Prevents the API duplicating templates the odoo-shell loader
           created without external IDs.
        3. No external ID, but an X24DN stub exists for this SKU -- upgrade it in
           place. This is the important one: the stub already has posted
           ``pos.order.line`` rows pointing at it, so it must keep its record id.
        4. Nothing exists -- the seam will create it.
        """
        Executor = self.env["retail.import.executor"]
        Template = self.env["product.template"].sudo()
        Product = self.env["product.product"].sudo()

        txid = Executor._safe_xid("tmpl_", template_code)
        tmpl_id = Executor._xid_get(namespace, txid, "product.template")
        if tmpl_id:
            template = Template.browse(tmpl_id)
            if template.exists():
                if template.mdm_template_code and template.mdm_template_code != template_code:
                    return Template.browse(), _("External ID %(xid)s already belongs to %(other)s, not %(code)s.") % {
                        "xid": txid,
                        "other": template.mdm_template_code,
                        "code": template_code,
                    }
                return template, None

        adopted = Template.search([("default_code", "=", template_code)], limit=1)
        if adopted:
            Executor._xid_set(namespace, txid, "product.template", adopted.id)
            _logger.info("MDM adopted existing template %s as %s.%s", adopted.id, namespace, txid)
            return adopted, None

        stub = self._find_stub(namespace, prod_sku, ean)
        if stub:
            template = stub.product_tmpl_id
            Executor._xid_set(namespace, txid, "product.template", template.id)
            # Deliberately NOT writing default_code here: the stub has one variant, so
            # Odoo would push the mainline code down onto it and destroy the PROD SKU
            # that posted pos.order.line rows resolve against. The seam sets the
            # variant's code from udf2; the mainline code lives in mdm_template_code.
            template.with_context(tracking_disable=True).write(
                {
                    "mdm_template_code": template_code,
                    "mdm_pending": False,
                    "mdm_source": "mdm_api",
                }
            )
            _logger.info(
                "MDM upgraded X24 stub product %s in place (template %s) for %s",
                stub.id,
                template.id,
                prod_sku,
            )
            return template, None

        return Template.browse(), None

    def _find_stub(self, namespace, prod_sku, ean):
        """An X24DN lazy-created / auto-registered product for this SKU, if any."""
        Executor = self.env["retail.import.executor"]
        Product = self.env["product.product"].sudo()
        for key in (prod_sku, ean):
            if not key:
                continue
            pid = Executor._xid_get(namespace, Executor._safe_xid("x24prod_", key), "product.product")
            if pid:
                product = Product.browse(pid)
                if product.exists():
                    return product
        if prod_sku:
            product = Product.search([("default_code", "=", prod_sku), ("mdm_pending", "=", True)], limit=1)
            if product:
                return product
        return Product.browse()

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------
    def _apply_results(self, records, summary):
        """Write back what the seam produced onto each staged item."""
        templates = summary.get("templates") or {}
        variants = summary.get("variants") or {}
        quality = summary.get("quality") or {}
        now = fields.Datetime.now()

        for item, record, item_notes in records:
            code = record["product_code"]
            sku = record["sku"]
            template_id = templates.get(code)
            product_id = variants.get(sku)
            notes = list(item_notes or [])
            notes.extend(quality.get(code, []))

            vals = {
                "template_id": template_id or False,
                "product_id": product_id or False,
                "processed_at": now,
            }
            if not template_id:
                vals["state"] = "error"
                vals["error"] = _("The upsert produced no template for %s.") % code
            elif not product_id:
                # The template exists but the Size x Inseam combination did not resolve
                # to a variant -- almost always a size that disagrees with udf2.
                vals["state"] = "needs_review"
                vals["error"] = "; ".join(notes + [_("no variant matched size %r") % record["size"]])[:255]
            elif notes:
                vals["state"] = "needs_review"
                vals["error"] = "; ".join(notes)[:255]
            else:
                vals["state"] = "done"
                vals["error"] = False
            item.write(vals)
        return True
