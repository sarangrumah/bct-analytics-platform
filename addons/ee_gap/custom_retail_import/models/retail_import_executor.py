# -*- coding: utf-8 -*-
"""Retail import executor — turns parsed rows into Odoo records, per file type.

This is the business logic behind ``retail.import.wizard`` / ``retail.import.feed``.
Each ``_load_<type>`` method consumes the dicts produced by
``retail.import.profile.read_records`` and writes Odoo records with batched
commits + ``ir.model.data`` external IDs (under ``profile.namespace``) for
idempotency.

Coverage:
  x101         — FULL. Port of the proven era_busana 2-script pipeline (products).
  coa          — FULL. account.account from a clean code/name/account_type file.
  company      — FULL. res.company / its partner from the SES legal-entity sheet.
  x20          — FULL. stock.quant opening on-hand (one-shot, guarded).
  x24          — POS sales -> pos.order (no stock move). Needs pos.config + session
                 + payment methods to exist; fails loudly with guidance otherwise.
  x70d         — pos.payment, joined to x24 orders by (store,date,register,trans).
  x70t/x31/x32p/store_master — STAGED: parsed, counted, attachment kept; no model
                 writes yet (reference/decision-gated per plan Phase 3/5). Warehouse
                 creation is done by the Track A odoo-shell loader.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.tools import config, float_compare

_logger = logging.getLogger(__name__)

BATCH = 200
LINE_BATCH = 500

# MDM sends size as one string ("32 28", "32/28", "M"). Split on any of the
# separators seen in the feed; "-" is included because _x24_codepart treats a lone
# "-" as "no value" anyway.
_MDM_SIZE_SPLIT = re.compile(r"[\s/xX*\-]+")
# "34.0" arriving as a JSON string rather than a number.
_MDM_INT_FLOAT = re.compile(r"\d+\.0+")


class RetailImportExecutor(models.AbstractModel):
    _name = "retail.import.executor"
    _description = "Retail Import Executor"

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def run(self, log):
        """Process the file stored on ``log`` (an ir.attachment). Updates counters
        + state on the log. Returns the log."""
        log.ensure_one()
        profile = log.profile_id
        file_b64 = log.source_b64()
        now = fields.Datetime.now
        if not file_b64:
            log.write({"state": "failed", "error_message": "No stored source file on log.", "finished_at": now()})
            return log
        # started_at/finished_at existed on the model but nothing ever wrote them, so
        # a long import gave no way to tell "still running" from "died an hour ago"
        # without reading the server log.
        log.write({"state": "running", "started_at": now(), "finished_at": False})
        self.env.cr.commit()
        handler = getattr(self, f"_load_{profile.file_type}", None)
        if handler is None:
            log.write(
                {
                    "state": "failed",
                    "error_message": f"No executor for file_type {profile.file_type!r}.",
                    "finished_at": now(),
                }
            )
            return log
        try:
            handler(profile, file_b64, log)
        except UserError:
            self.env.cr.rollback()
            raise
        except Exception as e:  # pragma: no cover - defensive
            self.env.cr.rollback()
            _logger.exception("Retail import failed (log %s)", log.id)
            log.write({"state": "failed", "error_message": str(e), "finished_at": now()})
            self.env.cr.commit()
            return log
        if log.state == "running":
            log.state = "partial" if log.error_count else "imported"
        log.finished_at = now()
        self.env.cr.commit()
        return log

    # ------------------------------------------------------------------
    # External-ID helpers (idempotency)
    # ------------------------------------------------------------------
    def _xid_get(self, namespace, name, model):
        ext = self.env["ir.model.data"].search(
            [("module", "=", namespace), ("name", "=", name), ("model", "=", model)], limit=1
        )
        return ext.res_id if ext else False

    def _xid_set(self, namespace, name, model, res_id):
        self.env["ir.model.data"].create(
            {"module": namespace, "name": name, "model": model, "res_id": res_id, "noupdate": True}
        )

    @staticmethod
    def _safe_xid(prefix, value):
        return prefix + "".join(c if c.isalnum() else "_" for c in str(value)).upper()

    # ------------------------------------------------------------------
    # Source-file footer rows
    # ------------------------------------------------------------------
    _RI_FOOTER_MARKERS = ("grand total", "total", "sub total", "subtotal")

    @classmethod
    def _ri_drop_footer_rows(cls, records, sku_fields=("item_code", "product_code")):
        """Drop the trailing summary row the EBR reports append.

        X24DN's last row carries ``STORE CODE = 'Grand Total'`` and an **empty-string**
        ITEM CODE — an ``is None`` guard lets it through, and it then becomes a bogus
        parked transaction whose amounts double the file's totals.
        """
        out = []
        for r in records:
            store = str(r.get("store_code") or "").strip().lower()
            has_sku = any(str(r.get(f) or "").strip() for f in sku_fields)
            if store in cls._RI_FOOTER_MARKERS or not has_sku:
                continue
            out.append(r)
        return out

    # ------------------------------------------------------------------
    # X101 product-type classification
    # ------------------------------------------------------------------
    def _x101_service_matchers(self):
        """Config-driven markers that flag an X101 row as a *service* (e.g. the
        "Original cut" tailoring/alteration item) rather than storable
        merchandise. Editable as data via ir.config_parameter, no code change:

          retail_import.service_category_keywords  (comma-sep, matched as a
              case-insensitive substring against category/class/subclass/name)
          retail_import.service_product_codes      (comma-sep, exact product code)

        Default keyword list is EMPTY on purpose: substring keywords like "TAILOR"
        false-match real merchandise ("TAILORED BUSTIER", "TAILORED CLASSIC" shirts,
        jeans named "…TAILOR"). Everything is storable merchandise by default; pin
        the actual "Original cut" tailoring service by exact code (or a carefully
        chosen keyword) via the config parameters above once its marker is known.
        """
        icp = self.env["ir.config_parameter"].sudo()
        kw_raw = icp.get_param("retail_import.service_category_keywords", "")
        code_raw = icp.get_param("retail_import.service_product_codes", "")
        keywords = [k.strip().upper() for k in (kw_raw or "").split(",") if k.strip()]
        codes = {c.strip().upper() for c in (code_raw or "").split(",") if c.strip()}
        return keywords, codes

    def _x101_is_service(self, meta, keywords, codes):
        """True when an X101 template should be a non-storable service product."""
        if codes and str(meta.get("code") or "").strip().upper() in codes:
            return True
        if not keywords:
            return False
        haystack = " ".join(str(meta.get(k) or "") for k in ("cat", "cls", "subcls", "name")).upper()
        return any(kw in haystack for kw in keywords)

    # ------------------------------------------------------------------
    # Row-level backtracking helpers
    # ------------------------------------------------------------------
    def _persist_lines(self, log, records):
        """Create retail.import.line for every parsed row before aggregation.

        Returns a {row_number: line_record} dict for post-load target linking.
        Batched in LINE_BATCH creates to avoid ORM memory spikes on large files.
        """
        Line = self.env["retail.import.line"]
        row_to_line = {}
        for start in range(0, len(records), LINE_BATCH):
            batch = records[start : start + LINE_BATCH]
            created = Line.create(
                [
                    {
                        "log_id": log.id,
                        "row_number": r.get("_row"),
                        "raw_data_json": json.dumps(r, default=str),
                    }
                    for r in batch
                ]
            )
            for r, ln in zip(batch, created, strict=False):
                row_to_line[r.get("_row")] = ln
        return row_to_line

    def _link_lines(self, row_to_line, row_nums, target_model, target_res_id, aggregate_key=None, state="ok"):
        """Batch-write target linkage onto a group of source lines."""
        line_ids = [row_to_line[rn].id for rn in row_nums if rn in row_to_line]
        if not line_ids:
            return
        vals = {"state": state, "target_model": target_model, "target_res_id": target_res_id}
        if aggregate_key is not None:
            vals["aggregate_key"] = aggregate_key
        self.env["retail.import.line"].browse(line_ids).write(vals)

    #: Advisory-lock key shared by every writer of the X101 product external IDs.
    _X101_LOCK = "retail_import:x101_upsert"

    def _x101_lock(self):
        """Serialise the writers of the ``tmpl_``/``cat_l*_`` external IDs.

        **Transaction-scoped on purpose.** A session-level ``pg_advisory_lock`` would
        hold across the whole run even where the loader commits between batches, which
        sounds stronger -- but it leaks. Postgres does not release a session lock on
        rollback, so a job that dies mid-upsert hands its connection back to the pool
        still holding it, and the next worker to ask blocks forever. That is not
        theoretical: it wedged the queue runner here until the connections were killed.
        A wedged queue is far worse than an imperfect lock.

        So the guarantee is scoped honestly:

        * the MDM API path runs with ``commit=False``, i.e. one transaction, and is
          therefore fully serialised -- which is where concurrency actually bites,
          because two messages for the same SKU arrive milliseconds apart;
        * the XLSX path is covered until its first batch commit. Beyond that,
          correctness rests where it always did: every record is keyed by an
          ``ir.model.data`` external ID, so a concurrent writer converges on the same
          rows rather than duplicating them.

        Released automatically at commit or rollback. Nothing to unlock, nothing to leak.
        """
        self.env.cr.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self._X101_LOCK,))

    def _ri_commit(self, allow=True):
        """Commit, unless the caller or the test runner forbids it.

        The X101 loader commits between batches so a 200k-row file does not hold one
        giant transaction. Two callers must not do that:

        * a ``TransactionCase``, where a commit escapes the rollback and leaks records
          into the database -- the same guard ``_sweep_orphan_product_values`` uses;
        * a queue job, where queue_job replaces ``cr.commit`` with a hard error, since
          committing mid-job would leave the job record and its work out of step.

        Passing ``allow=False`` also makes the whole run atomic, which is what an MDM
        message (at most a few hundred items) should be anyway.
        """
        if allow and not config["test_enable"]:
            self.env.cr.commit()

    # ==================================================================
    # X101 — Products (categories / attributes / templates / variants)
    # ==================================================================
    def _load_x101(self, profile, file_b64, log):
        ns = profile.namespace
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)

        row_to_line = self._persist_lines(log, records)
        self._x101_upsert_items(records, ns, profile=profile, log=log, row_to_line=row_to_line)

    def _x101_upsert_items(self, records, ns, profile=None, log=None, row_to_line=None, commit=True):
        """Upsert X101-shaped item dicts under the shared advisory lock.

        Thin wrapper so both callers get the lock without having to remember it; the
        body is in ``_x101_upsert_items_locked``. Pass ``commit=False`` from inside a
        queue job -- queue_job forbids committing mid-job, and it also keeps the
        advisory lock (transaction-scoped) held for the whole run.
        """
        self._x101_lock()
        return self._x101_upsert_items_locked(
            records, ns, profile=profile, log=log, row_to_line=row_to_line, commit=commit
        )

    def _x101_upsert_items_locked(self, records, ns, profile=None, log=None, row_to_line=None, commit=True):
        """Upsert X101-shaped item dicts into categories / attributes / templates / variants.

        This is the single seam both the XLSX import and the MDM REST API go through,
        so a product created by either route is identical -- same category xids, same
        Size/Inseam attribute values, same variant match, same GTIN aliases.

        ``records`` is a list of dicts carrying the logical keys that
        ``retail.import.profile.read_records`` produces for an X101 file::

            product_code, description, category, klass, subclass, sku, size, inseam,
            gtin, retail_price, price_eff, _row

        plus, from the MDM API path only, an optional ``_mdm`` sub-dict with the
        extended attributes (brand, season, cost, ...). The XLSX path never sets it.

        ``profile``, ``log`` and ``row_to_line`` are all OPTIONAL. The API path passes
        none of them: it gets the same product writes with no ``retail.import.line``
        bookkeeping, and pre-normalises amounts/dates itself.

        Returns a summary dict::

            {"created": n, "matched": n, "unmatched": n,
             "templates": {product_code: template_id},
             "variants":  {sku: product_id},
             "quality":   {product_code: [data-quality messages]}}
        """

        def _amt(value):
            return float(profile._parse_amount(value)) if profile is not None else float(value or 0.0)

        def _effdate(value):
            # NB: not named _eff -- the variant loop below rebinds that name
            # (``for _eff, v in sku_best.values()``) in this same scope.
            return (profile._parse_date(value) or None) if profile is not None else (value or None)

        # ---- aggregate (mirror of 01_extract_x101.py) ----
        sku_best = {}  # sku -> (eff, dict)
        sku_gtins = defaultdict(set)  # sku -> {all GTINs} (a variant may have several)
        tmpl_meta = {}  # code -> dict
        sizes, inseams = set(), set()
        tmpl_variants = defaultdict(set)
        tmpl_rows = defaultdict(list)  # pc -> [row_nums] for post-load line linking
        skipped_row_nums = []
        tmpl_mdm = {}  # pc  -> extended attrs from the MDM payload (API path only)
        sku_mdm = {}  # sku -> extended attrs from the MDM payload (API path only)
        for r in records:
            pc = r.get("product_code")
            sku = r.get("sku")
            row_num = r.get("_row")
            if not pc or not sku:
                if row_num is not None:
                    skipped_row_nums.append(row_num)
                continue
            size = (r.get("size") or "").strip()
            inseam_raw = r.get("inseam")
            inseam = str(inseam_raw).strip() if inseam_raw not in (None, "-", "") else ""
            gtin = str(r.get("gtin") or "").strip()
            if gtin:
                sku_gtins[sku].add(gtin)  # keep EVERY GTIN so all scanned codes resolve
            retail = _amt(r.get("retail_price"))
            eff = _effdate(r.get("price_eff"))
            if r.get("_mdm"):
                sku_mdm[sku] = r["_mdm"]
                tmpl_mdm[pc] = r["_mdm"]

            prev = sku_best.get(sku)
            prev_eff = prev[0] if prev else None
            if prev is None or (eff and (prev_eff is None or eff > prev_eff)):
                sku_best[sku] = (eff, {"sku": sku, "tmpl_code": pc, "size": size, "inseam": inseam, "gtin": gtin})

            m = tmpl_meta.get(pc)
            if m is None or (eff and (m.get("eff") is None or eff > m["eff"])):
                tmpl_meta[pc] = {
                    "code": pc,
                    "name": (r.get("description") or pc),
                    "cat": (r.get("category") or "").strip(),
                    "cls": (r.get("klass") or "").strip(),
                    "subcls": (r.get("subclass") or "").strip(),
                    "retail": retail,
                    "eff": eff,
                }
            tmpl_variants[pc].add((size, inseam))
            tmpl_rows[pc].append(row_num)
            if size:
                sizes.add(size)
            if inseam:
                inseams.add(inseam)

        _logger.info(
            "x101: %s rows -> %s templates, %s skus, %s sizes, %s inseams",
            len(records),
            len(tmpl_meta),
            len(sku_best),
            len(sizes),
            len(inseams),
        )

        # ---- categories (3-level) ----
        def cat_xid(name):
            return self._safe_xid("cat_l1_", name)

        def cls_xid(cat, cls):
            return self._safe_xid("cat_l2_", f"{cat}_{cls}")

        def sub_xid(cat, cls, sub):
            return self._safe_xid("cat_l3_", f"{cat}_{cls}_{sub}")

        xid_to_cat = {}
        # build the parent->child sequence
        l1 = {m["cat"] for m in tmpl_meta.values() if m["cat"]}
        l2 = {(m["cat"], m["cls"]) for m in tmpl_meta.values() if m["cat"] and m["cls"]}
        l3 = {(m["cat"], m["cls"], m["subcls"]) for m in tmpl_meta.values() if m["cat"] and m["cls"] and m["subcls"]}
        for c in sorted(l1):
            xid = cat_xid(c)
            rid = self._xid_get(ns, xid, "product.category")
            if not rid:
                rid = self.env["product.category"].create({"name": c}).id
                self._xid_set(ns, xid, "product.category", rid)
            xid_to_cat[xid] = rid
        for cat, cls in sorted(l2):
            xid = cls_xid(cat, cls)
            rid = self._xid_get(ns, xid, "product.category")
            if not rid:
                rid = self.env["product.category"].create({"name": cls, "parent_id": xid_to_cat.get(cat_xid(cat))}).id
                self._xid_set(ns, xid, "product.category", rid)
            xid_to_cat[xid] = rid
        for cat, cls, sub in sorted(l3):
            xid = sub_xid(cat, cls, sub)
            rid = self._xid_get(ns, xid, "product.category")
            if not rid:
                rid = (
                    self.env["product.category"]
                    .create({"name": sub, "parent_id": xid_to_cat.get(cls_xid(cat, cls))})
                    .id
                )
                self._xid_set(ns, xid, "product.category", rid)
            xid_to_cat[xid] = rid
        self._ri_commit(commit)

        # ---- attributes ----
        attr_by_name = {}
        for attr_name in ("Size", "Inseam"):
            attr = self.env["product.attribute"].search([("name", "=", attr_name)], limit=1)
            if not attr:
                attr = self.env["product.attribute"].create(
                    {"name": attr_name, "create_variant": "always", "display_type": "radio"}
                )
            attr_by_name[attr_name] = attr
        attr_value_id = {}
        for attr_name, vals in (("Size", sorted(sizes)), ("Inseam", sorted(inseams))):
            attr = attr_by_name[attr_name]
            for v in vals:
                av = self.env["product.attribute.value"].search(
                    [("attribute_id", "=", attr.id), ("name", "=", v)], limit=1
                )
                if not av:
                    av = self.env["product.attribute.value"].create({"attribute_id": attr.id, "name": v})
                attr_value_id[(attr_name, v)] = av.id
        self._ri_commit(commit)

        # ---- templates ----
        tmpl_xid_to_id = {}
        for ext in self.env["ir.model.data"].search([("module", "=", ns), ("model", "=", "product.template")]):
            tmpl_xid_to_id[ext.name] = ext.res_id
        created = 0
        svc_keywords, svc_codes = self._x101_service_matchers()
        bad_quality = defaultdict(list)  # pc -> [data-quality messages] for line flagging
        items = sorted(tmpl_meta.items())
        reprice = self._x101_update_price_enabled()
        repriced = 0
        for start in range(0, len(items), BATCH):
            repricing = {}  # tmpl_id -> price from the file, applied in bulk per batch
            for pc, m in items[start : start + BATCH]:
                txid = self._safe_xid("tmpl_", pc)
                vset = tmpl_variants[pc]
                t_sizes = sorted({s for s, _ in vset if s})
                t_inseams = sorted({i for _, i in vset if i})
                if txid in tmpl_xid_to_id:
                    # Fix B: template already registered — backfill any newly-appearing
                    # size/inseam values so the missing variants get generated. The
                    # variant-match loop below then assigns their default_code/barcode.
                    self._x101_backfill_template_attrs(
                        tmpl_xid_to_id[txid], t_sizes, t_inseams, attr_by_name, attr_value_id
                    )
                    if reprice:
                        # Only the create path below ever set list_price, so a re-import
                        # of a corrected file never moved the price of a product that
                        # already existed. Opt-in because this addon is shared by every
                        # tenant: switching it on decides that the file, not the
                        # database, owns the price.
                        if not m["retail"] or m["retail"] <= 0:
                            bad_quality[pc].append("invalid/zero price")
                        else:
                            repricing[tmpl_xid_to_id[txid]] = m["retail"]
                    continue
                attr_lines = []
                if t_sizes:
                    attr_lines.append(
                        (
                            0,
                            0,
                            {
                                "attribute_id": attr_by_name["Size"].id,
                                "value_ids": [(6, 0, [attr_value_id[("Size", s)] for s in t_sizes])],
                            },
                        )
                    )
                if t_inseams:
                    attr_lines.append(
                        (
                            0,
                            0,
                            {
                                "attribute_id": attr_by_name["Inseam"].id,
                                "value_ids": [(6, 0, [attr_value_id[("Inseam", i)] for i in t_inseams])],
                            },
                        )
                    )
                categ_id = (
                    xid_to_cat.get(sub_xid(m["cat"], m["cls"], m["subcls"]))
                    or xid_to_cat.get(cls_xid(m["cat"], m["cls"]))
                    or xid_to_cat.get(cat_xid(m["cat"]))
                )
                # Data-quality flags (surface bad source rows in the import log).
                if not categ_id:
                    bad_quality[pc].append("missing category")
                if not m["retail"] or m["retail"] <= 0:
                    bad_quality[pc].append("invalid/zero price")
                # Merchandise (jeans, tops, paperbag, ...) is storable & qty-tracked;
                # the "Original cut" tailoring/alteration item is a service. Odoo 19
                # carries the distinction on is_storable, not on type.
                is_service = self._x101_is_service(m, svc_keywords, svc_codes)
                vals = {
                    "name": m["name"] or pc,
                    "default_code": pc,
                    "list_price": m["retail"],
                    "type": "service" if is_service else "consu",
                    "sale_ok": True,
                    "purchase_ok": True,
                }
                if not is_service:
                    vals["is_storable"] = True
                if categ_id:
                    vals["categ_id"] = categ_id
                if attr_lines:
                    vals["attribute_line_ids"] = attr_lines
                # Odoo generates the whole Size x Inseam variant matrix inside this
                # create(); without the mail context each variant logs a creation
                # message and subscribes a follower, which on a full X101 run is
                # ~350k mail_message rows.
                tmpl = (
                    self.env["product.template"]
                    .with_context(
                        tracking_disable=True,
                        mail_create_nolog=True,
                        mail_create_nosubscribe=True,
                    )
                    .create(vals)
                )
                self._xid_set(ns, txid, "product.template", tmpl.id)
                tmpl_xid_to_id[txid] = tmpl.id
                created += 1
            repriced += self._x101_apply_repricing(repricing)
            self._ri_commit(commit)
            self.env.invalidate_all()
        if log is not None:
            log.records_created = created
        if reprice:
            _logger.info("x101 repricing: %s templates updated from the file", repriced)

        # ---- variants: match auto-generated by (size, inseam) and set sku/barcode ----
        variant_ids = {}  # sku -> product.product id, for the summary
        size_val_id = {v: i for (a, v), i in attr_value_id.items() if a == "Size"}
        inseam_val_id = {v: i for (a, v), i in attr_value_id.items() if a == "Inseam"}
        by_tmpl = defaultdict(list)
        for _eff, v in sku_best.values():
            by_tmpl[self._safe_xid("tmpl_", v["tmpl_code"])].append(v)
        matched = unmatched = 0
        tkeys = list(by_tmpl.keys())
        for start in range(0, len(tkeys), 100):
            batch_xids = tkeys[start : start + 100]
            tmpl_ids = [tmpl_xid_to_id[x] for x in batch_xids if x in tmpl_xid_to_id]
            if not tmpl_ids:
                unmatched += sum(len(by_tmpl[x]) for x in batch_xids)
                continue
            # The mail context matters as much here as it does on the template create
            # below: without it every default_code/barcode write logs a tracking
            # message, and a full X101 touches a quarter of a million variants.
            variants = (
                self.env["product.product"]
                .with_context(tracking_disable=True, mail_notrack=True)
                .search([("product_tmpl_id", "in", tmpl_ids)])
            )
            pending_gtins = []  # [(variant, gtins)] -> registered in one pass per batch
            var_index = {}
            var_by_tmpl = defaultdict(list)
            used_by_tmpl = defaultdict(set)  # attribute-value ids that actually distinguish variants
            for vp in variants:
                ids = vp.product_template_variant_value_ids.product_attribute_value_id.ids
                var_index[(vp.product_tmpl_id.id, frozenset(ids))] = vp
                var_by_tmpl[vp.product_tmpl_id.id].append(vp)
                used_by_tmpl[vp.product_tmpl_id.id].update(ids)
            for txid in batch_xids:
                tid = tmpl_xid_to_id.get(txid)
                if not tid:
                    unmatched += len(by_tmpl[txid])
                    continue
                for v in by_tmpl[txid]:
                    wanted = set()
                    if v["size"] and size_val_id.get(v["size"]):
                        wanted.add(size_val_id[v["size"]])
                    if v["inseam"] and inseam_val_id.get(v["inseam"]):
                        wanted.add(inseam_val_id[v["inseam"]])
                    # Odoo omits SINGLE-value attributes from a variant's combo (e.g. a
                    # jeans template with one inseam "32", or an "OS" accessory). Restrict
                    # ``wanted`` to the values that actually distinguish this template's
                    # variants so the size-only / empty combo still resolves.
                    wanted_eff = frozenset(wanted & used_by_tmpl.get(tid, set()))
                    vp = var_index.get((tid, wanted_eff))
                    if not vp:
                        # Last resort: a lone-variant template that contributes a single
                        # sku — assign it directly.
                        tvars = var_by_tmpl.get(tid, [])
                        if len(tvars) == 1 and len(by_tmpl[txid]) == 1:
                            vp = tvars[0]
                        else:
                            unmatched += 1
                            continue
                    updates = {}
                    if vp.default_code != v["sku"]:
                        updates["default_code"] = v["sku"]
                    if v["gtin"] and vp.barcode != v["gtin"]:
                        updates["barcode"] = v["gtin"]
                    if updates:
                        try:
                            vp.write(updates)
                        except Exception:
                            if "barcode" in updates:
                                vp.write({k: x for k, x in updates.items() if k != "barcode"})
                    # Fix A: register EVERY GTIN of this sku as an alternate barcode so a
                    # POS scan of any of them resolves (the variant's single ``barcode``
                    # field only holds one). Deferred to one bulk pass per batch --
                    # see _x101_register_gtins_bulk for why.
                    pending_gtins.append((vp, sku_gtins.get(v["sku"], ())))
                    variant_ids[v["sku"]] = vp.id
                    matched += 1
            self._x101_register_gtins_bulk(pending_gtins)
            self._ri_commit(commit)
            # The ORM cache never shrinks on its own and this loop walks the whole
            # catalogue: without this the process grows past a gigabyte on a full run.
            self.env.invalidate_all()
        if log is not None:
            log.records_matched = matched
            log.records_skipped = unmatched
        _logger.info("x101 done: created=%s matched=%s unmatched=%s", created, matched, unmatched)
        self._sweep_orphan_product_values(commit=commit)

        template_ids = {}
        for pc in tmpl_meta:
            tmpl_id = tmpl_xid_to_id.get(self._safe_xid("tmpl_", pc))
            if tmpl_id:
                template_ids[pc] = tmpl_id

        # Extended MDM attributes (brand, season, cost, active, ...). A no-op for the
        # XLSX path, which never populates the "_mdm" key.
        if tmpl_mdm or sku_mdm:
            self._mdm_apply_extended(template_ids, variant_ids, tmpl_mdm, sku_mdm)
            self._ri_commit(commit)

        # Link source rows to the product.template they contributed to
        if row_to_line:
            for pc, row_nums in tmpl_rows.items():
                tmpl_id = template_ids.get(pc)
                if tmpl_id:
                    self._link_lines(row_to_line, row_nums, "product.template", tmpl_id, aggregate_key=pc)
            if skipped_row_nums:
                skip_ids = [row_to_line[rn].id for rn in skipped_row_nums if rn in row_to_line]
                if skip_ids:
                    self.env["retail.import.line"].browse(skip_ids).write({"state": "skipped"})
            self._ri_commit(commit)

        # Flag data-quality issues on the contributing source lines (runs AFTER
        # linking so it overrides the "ok" state set above). Bumps error_count,
        # which marks the log "partial" so the user reviews it.
        errors = []
        for pc, msgs in bad_quality.items():
            msg = "; ".join(sorted(set(msgs)))
            row_nums = tmpl_rows.get(pc, [])
            if row_to_line:
                line_ids = [row_to_line[rn].id for rn in row_nums if rn in row_to_line]
                if line_ids:
                    self.env["retail.import.line"].browse(line_ids).write({"state": "error", "error_message": msg})
            for rn in row_nums:
                errors.append((rn, f"{pc}: {msg}"))
        if log is not None:
            log.set_errors(errors)
        self._ri_commit(commit)

        summary = {
            "created": created,
            "matched": matched,
            "unmatched": unmatched,
            "templates": template_ids,
            "variants": variant_ids,
            "quality": dict(bad_quality),
        }
        # Master data just landed: any X24DN transaction parked for one of these SKUs
        # can now be posted. Fires for BOTH callers -- that is the point of the seam.
        self._x101_notify_master_registered(summary, sku_gtins)
        return summary

    def _x101_notify_master_registered(self, summary, sku_gtins=None):
        """Tell the pending-SKU registry which codes/GTINs now exist, and replay.

        Soft-guarded: ``retail.mdm.pending.sku`` is added by the same module version
        that adds this call, but the guard keeps the loader working against a database
        that has not been upgraded yet (the registry is inert, the import is not).
        """
        if "retail.mdm.pending.sku" not in self.env:
            return
        codes = set(summary.get("variants") or ()) | set(summary.get("templates") or ())
        gtins = set()
        for values in (sku_gtins or {}).values():
            gtins.update(values)
        if not codes and not gtins:
            return
        try:
            self.env["retail.mdm.pending.sku"]._resolve_and_replay(codes, gtins)
        except Exception:  # never let the registry break a successful product load
            _logger.exception("pending-sku resolve/replay failed after X101 upsert")

    # ------------------------------------------------------------------
    # X101 helpers — multi-GTIN (Fix A) + variant backfill (Fix B)
    # ------------------------------------------------------------------
    def _x101_register_gtins(self, variant, gtins):
        """Register every GTIN of a sku as a product.barcode alternate on ``variant``.

        The variant's single ``barcode`` field only holds one code; a the apparel brand SKU has
        several GTINs and POS may scan any of them. Reuses custom_product_barcode's
        ``product.barcode`` (resolved via ``product.product._resolve_barcode``). Dedup
        against the primary barcode and existing aliases; idempotent + sudo (write on
        product.barcode is group_system)."""
        if not gtins:
            return
        Barcode = self.env["product.barcode"].sudo()
        have = set(variant.barcode_ids.mapped("barcode"))
        if variant.barcode:
            have.add(variant.barcode)
        for g in gtins:
            g = (g or "").strip()
            if not g or g in have:
                continue
            try:
                Barcode.create({"product_id": variant.id, "barcode": g, "note": "X101 GTIN"})
                have.add(g)
            except Exception as e:  # unique(product_id,barcode) race / bad value
                _logger.debug("x101 gtin alias skip %s on %s: %s", g, variant.id, e)

    def _x101_update_price_enabled(self):
        """Whether a re-import may move ``list_price`` on templates that already exist.

        Default OFF. The create path has always written ``list_price``, but the
        already-registered path did not, so no X101 re-import has ever changed the
        price of an existing product -- a corrected file silently left the old number
        (or a 0,00 from a source row with no price) in place. Turning this on is a
        decision that the file owns the price, which is not safe to assume for a
        database whose prices were touched by hand, and this addon is shared by every
        tenant. So it is per-database opt-in, like the other ``retail_import.*``
        switches.
        """
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x101_update_price", "0") in (
            "1",
            "True",
            "true",
        )

    def _x101_apply_repricing(self, repricing):
        """Write ``{tmpl_id: price}`` with one UPDATE per distinct price. Returns the count.

        Reads the current prices in a single query and writes only what actually
        differs, so a re-import of an unchanged file costs one SELECT per batch and
        touches nothing -- no write_date churn, no needless recompute of anything
        related to list_price.
        """
        if not repricing:
            return 0
        Template = self.env["product.template"].with_context(tracking_disable=True, mail_notrack=True)
        current = {t["id"]: t["list_price"] for t in Template.browse(list(repricing)).read(["list_price"])}
        by_price = defaultdict(list)
        for tmpl_id, price in repricing.items():
            if tmpl_id in current and float_compare(current[tmpl_id], price, precision_digits=2) != 0:
                by_price[price].append(tmpl_id)
        for price, ids in by_price.items():
            Template.browse(ids).write({"list_price": price})
        return sum(len(ids) for ids in by_price.values())

    def _x101_register_gtins_bulk(self, pairs):
        """Batch version of :meth:`_x101_register_gtins` for a whole template batch.

        ``pairs`` is ``[(variant, gtins), ...]``. The per-variant version is correct
        but pathologically slow on a full X101, and it was measured: sampling
        ``pg_stat_activity`` during a 260k-sku run put ~78% of database time on the
        barcode path alone. Three costs, all per single alias:

        * ``variant.barcode_ids`` re-reads the o2m for every variant, because the
          preceding ``vp.write()`` invalidated the cache the batched search filled;
        * each ``create()`` is one INSERT in its own subtransaction -- the ``except``
          makes Postgres take a savepoint per row;
        * each ``create()`` flushes the pending variant writes, and that flush fires
          core's ``@api.constrains('barcode') _check_barcode_uniqueness``, which
          read_groups ``product_product`` and probes ``product_uom`` once per barcode.

        Doing it once per batch collapses all three: one o2m read, one multi-create,
        one constraint pass. On a unique-constraint collision we fall back to the
        per-variant path so a single bad row still cannot lose the rest of the batch.
        """
        pairs = [(v, g) for v, g in pairs if g]
        if not pairs:
            return
        Barcode = self.env["product.barcode"].sudo()
        variant_ids = [v.id for v, _ in pairs]
        have = defaultdict(set)
        for row in Barcode.search_read([("product_id", "in", variant_ids)], ["product_id", "barcode"]):
            have[row["product_id"]].add(row["barcode"])
        vals_list = []
        for variant, gtins in pairs:
            seen = have[variant.id]
            if variant.barcode:
                seen.add(variant.barcode)
            for g in gtins:
                g = (g or "").strip()
                if not g or g in seen:
                    continue
                seen.add(g)  # also dedups a GTIN repeated across skus in this batch
                vals_list.append({"product_id": variant.id, "barcode": g, "note": "X101 GTIN"})
        if not vals_list:
            return
        try:
            with self.env.cr.savepoint():
                Barcode.create(vals_list)
        except Exception as e:
            _logger.warning(
                "x101 gtin alias bulk insert of %s rows failed (%s); retrying one by one",
                len(vals_list),
                e,
            )
            for variant, gtins in pairs:
                self._x101_register_gtins(variant, gtins)

    def _sweep_orphan_product_values(self, commit=True):
        """Drop the zero-value product.value rows left behind by variant deletes.

        Core writes one product.value per product.product create ("Price update
        from None to 0.0"). Its FK is ON DELETE SET NULL, so every re-import that
        restructures the Size x Inseam matrix orphans a row that nothing will ever
        read again. Left alone these accumulate into the hundreds of thousands and
        turn _get_last_product_value into a full table scan.

        Opt-in: the first run on an existing database clears the whole backlog,
        which is a mass delete nobody asked for at import time. Enable with
        ``retail_import.sweep_orphan_product_values=1`` once the backlog has been
        purged (and reviewed) out of band.
        """
        enabled = self.env["ir.config_parameter"].sudo().get_param("retail_import.sweep_orphan_product_values", "0")
        if enabled not in ("1", "True", "true"):
            return
        self.env.cr.execute(
            """
            DELETE FROM product_value
            WHERE product_id IS NULL AND move_id IS NULL AND lot_id IS NULL AND value = 0
            """
        )
        removed = self.env.cr.rowcount
        if removed:
            _logger.info("swept %s orphaned product.value rows", removed)
        self._ri_commit(commit)

    def _x101_backfill_template_attrs(self, tmpl_id, t_sizes, t_inseams, attr_by_name, attr_value_id):
        """Add newly-appearing Size/Inseam values to an EXISTING template so Odoo
        regenerates the missing variants (create_variant='always'). Only writes when
        there is something to add, so re-runs are no-ops (idempotent).

        Adding a value to an existing attribute line is safe (just more variants).
        Creating a brand-new attribute line restructures the variant matrix — acceptable
        here because imported POS is financial-only (no stock on these products)."""
        # Adding attribute values below regenerates variants, so carry the same
        # mail context the create path uses.
        tmpl = (
            self.env["product.template"]
            .with_context(
                tracking_disable=True,
                mail_create_nolog=True,
                mail_create_nosubscribe=True,
            )
            .browse(tmpl_id)
        )
        if not tmpl.exists():
            return
        want = {
            "Size": [attr_value_id[("Size", s)] for s in t_sizes if ("Size", s) in attr_value_id],
            "Inseam": [attr_value_id[("Inseam", i)] for i in t_inseams if ("Inseam", i) in attr_value_id],
        }
        for attr_name, wanted_ids in want.items():
            if not wanted_ids:
                continue
            attr = attr_by_name[attr_name]
            line = tmpl.attribute_line_ids.filtered(lambda l: l.attribute_id == attr)[:1]
            if line:
                missing = [i for i in wanted_ids if i not in line.value_ids.ids]
                if missing:
                    try:
                        line.write({"value_ids": [(4, i) for i in missing]})
                    except Exception as e:
                        _logger.warning("x101 backfill add-value tmpl %s %s: %s", tmpl_id, attr_name, e)
            else:
                try:
                    tmpl.write(
                        {"attribute_line_ids": [(0, 0, {"attribute_id": attr.id, "value_ids": [(6, 0, wanted_ids)]})]}
                    )
                except Exception as e:
                    _logger.warning("x101 backfill add-line tmpl %s %s: %s", tmpl_id, attr_name, e)

    # ==================================================================
    # CoA — account.account
    # ==================================================================
    def _load_coa(self, profile, file_b64, log):
        ns = profile.namespace
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)
        row_to_line = self._persist_lines(log, records)
        valid_types = dict(self.env["account.account"]._fields["account_type"].selection)
        company = profile.company_id
        created = skipped = 0
        errors = []
        Account = self.env["account.account"]
        for r in records:
            rn = r.get("_row")
            code = str(r.get("code") or "").strip()
            name = (r.get("account_name") or r.get("name") or "").strip()
            atype = str(r.get("account_type") or "").strip()
            if not code or not name:
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "skipped"})
                continue
            if atype not in valid_types:
                errors.append((rn, f"invalid account_type {atype!r} for {code}"))
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": f"invalid account_type {atype!r}"})
                continue
            xid = self._safe_xid("coa_", code)
            existing_id = self._xid_get(ns, xid, "account.account")
            if existing_id:
                skipped += 1
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {
                            "state": "skipped",
                            "aggregate_key": code,
                            "target_model": "account.account",
                            "target_res_id": existing_id,
                        }
                    )
                continue
            existing = Account.with_company(company).search(
                [("code", "=", code), ("company_ids", "in", company.id)], limit=1
            )
            if existing:
                self._xid_set(ns, xid, "account.account", existing.id)
                skipped += 1
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {
                            "state": "skipped",
                            "aggregate_key": code,
                            "target_model": "account.account",
                            "target_res_id": existing.id,
                        }
                    )
                continue
            try:
                acc = Account.with_company(company).create(
                    {"code": code, "name": name, "account_type": atype, "company_ids": [(4, company.id)]}
                )
                self._xid_set(ns, xid, "account.account", acc.id)
                created += 1
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {"aggregate_key": code, "target_model": "account.account", "target_res_id": acc.id}
                    )
            except Exception as e:
                errors.append((rn, f"{code}: {e}"))
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": f"{code}: {e}"})
        self.env.cr.commit()
        log.records_created = created
        log.records_skipped = skipped
        log.set_errors(errors)

    # ==================================================================
    # Company — res.company / partner (SES legal entity)
    # ==================================================================
    def _load_company(self, profile, file_b64, log):
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)
        company = profile.company_id
        vals = {}
        # SES sheet is a key/value layout; the profile maps a 'label'/'value' pair
        # OR direct fields. Support both: if a record carries 'field'/'value', set by label.
        label_to_field = {
            "nama": "name",
            "name": "name",
            "npwp": "vat",
            "vat": "vat",
            "alamat": "street",
            "address": "street",
            "telepon": "phone",
            "phone": "phone",
            "email": "email",
        }
        for r in records:
            raw_field = r.get("field")
            raw_value = r.get("value")
            # SES sheet often packs "Label : Value" into one cell -> split it.
            if raw_field and (raw_value is None or str(raw_value).strip() == "") and ":" in str(raw_field):
                label, _sep, val = str(raw_field).partition(":")
                raw_field, raw_value = label, val
            if raw_field is not None and raw_value is not None and str(raw_value).strip():
                key = str(raw_field or "").strip().lower().split(":")[0].strip()
                f = label_to_field.get(key)
                if f and str(raw_value).strip():
                    vals[f] = str(raw_value).strip()
            else:
                for src, dst in (
                    ("name", "name"),
                    ("vat", "vat"),
                    ("street", "street"),
                    ("phone", "phone"),
                    ("email", "email"),
                ):
                    if r.get(src):
                        vals[dst] = str(r.get(src)).strip()
        if vals:
            company.write({k: v for k, v in vals.items() if k in ("name",)})
            company.partner_id.write({k: v for k, v in vals.items() if k != "name"})
            log.records_matched = 1
        log.set_errors([])

    # ==================================================================
    # X20 — Opening on-hand stock -> stock.quant (one-shot, guarded)
    # ==================================================================
    def _load_x20(self, profile, file_b64, log):
        ns = profile.namespace
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)
        row_to_line = self._persist_lines(log, records)
        Product = self.env["product.product"]
        Quant = self.env["stock.quant"]
        # store-code -> internal stock location (warehouse lot_stock_id), resolved by xid
        loc_cache = {}

        def resolve_location(store_code):
            sc = str(store_code or "").strip()
            if sc in loc_cache:
                return loc_cache[sc]
            wh_id = self._xid_get(ns, self._safe_xid("wh_", sc), "stock.warehouse")
            loc = self.env["stock.warehouse"].browse(wh_id).lot_stock_id if wh_id else False
            loc_cache[sc] = loc
            return loc

        prod_by_barcode = {}
        prod_by_code = {}
        applied = skipped = 0
        errors = []
        quant_vals = []  # (prod, loc, qty, row_num, agg_key)
        for r in records:
            rn = r.get("_row")
            store = r.get("store_code")
            ean = str(r.get("ean") or "").strip()
            item_id = str(r.get("item_id") or "").strip()
            qty = float(profile._parse_amount(r.get("onhand_qty")))
            if not store or qty <= 0:
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "skipped"})
                continue
            loc = resolve_location(store)
            if not loc:
                errors.append((rn, f"store {store} -> no warehouse (run store loader first)"))
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": f"store {store}: no warehouse"})
                continue
            prod = False
            if ean:
                if ean not in prod_by_barcode:
                    prod_by_barcode[ean] = Product._resolve_barcode(ean)
                prod = prod_by_barcode[ean]
            if not prod and item_id:
                if item_id not in prod_by_code:
                    prod_by_code[item_id] = Product.search([("default_code", "=", item_id)], limit=1)
                prod = prod_by_code[item_id]
            if not prod:
                errors.append((rn, f"no product for ean={ean!r} item={item_id!r}"))
                skipped += 1
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {"state": "error", "error_message": f"no product ean={ean!r} item={item_id!r}"}
                    )
                continue
            quant_vals.append((prod, loc, qty, rn, f"{store}|{item_id or ean}"))

        # Guard: refuse to re-apply if this profile already applied opening stock.
        prior = self.env["retail.import.log"].search(
            [("profile_id", "=", profile.id), ("state", "=", "imported"), ("id", "!=", log.id)], limit=1
        )
        if prior:
            raise UserError(
                _(
                    "Opening stock for profile %s was already applied (log #%s). "
                    "Re-applying would double the on-hand. Archive the prior log to override."
                )
                % (profile.code, prior.id)
            )

        for i, (prod, loc, qty, rn, agg_key) in enumerate(quant_vals):
            try:
                q = Quant.with_context(inventory_mode=True).create(
                    {"product_id": prod.id, "location_id": loc.id, "inventory_quantity": qty}
                )
                q.action_apply_inventory()
                applied += 1
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {
                            "aggregate_key": agg_key,
                            "target_model": "stock.quant",
                            "target_res_id": q.id,
                        }
                    )
            except Exception as e:
                errors.append((None, f"{prod.default_code}: {e}"))
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": str(e)})
            if (i + 1) % 500 == 0:
                self.env.cr.commit()
        self.env.cr.commit()
        log.records_created = applied
        log.records_skipped = skipped
        log.set_errors(errors)

    # ==================================================================
    # X24 — Retail sales -> pos.order (financial, no stock move)
    # ==================================================================
    # --- Phase-5 X24 configuration (see docs/PHASE5_X24_DESIGN.md) ---------
    # Locked defaults (2026-07-05):
    #   A: store->pos.config via ir.model.data xid ``posconfig_<STORE>``
    #   B: no stock (import posts financial pos.order only; sessions may be closed)
    #   C: one pos.session per (config, trans_date)
    #   D: tender_type == pos.payment.method.name; OFFLINE_OTHER_CARD folds in
    #   E: tax_rate 0.11 -> account.tax "12% (Non-Luxury Good)" (amount 11.0, sale)
    #   F: one pos.order per (store, date, register, transnum)
    _X24_TENDER_FOLD = {"OFFLINE_OTHER_CARD": "OFFLINE_OTHER_CREDITCARD"}
    _X24_SEED_TENDERS = ("OFFLINE_AMEX", "OFFLINE_OVO", "SODEXO")
    _X24_BALANCE_TOL = 1.0  # currency units; parks orders whose tenders != line total

    def _x24_post_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x24_post_enabled", "0") in (
            "1",
            "true",
            "True",
        )

    def _x24_discount_reclass_enabled(self):
        """Book X24DN's NET DISCOUNT AMOUNT as a per-category contra-revenue reclass
        (Dr Sales Discount-<cat> / Cr Gross Sales-<cat>) after the POS sessions close.

        Mutually exclusive with ``retail_import.x31_post_enabled`` — X24DN covers every
        X31 discount, so enabling both grosses revenue up twice. Gated (default OFF)."""
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x24_discount_reclass", "0") in (
            "1",
            "true",
            "True",
        )

    def _ri_assert_single_discount_source(self):
        if self._x24_discount_reclass_enabled() and self._x31_post_enabled():
            raise UserError(
                _(
                    "retail_import.x24_discount_reclass and retail_import.x31_post_enabled are "
                    "both on. X24DN's NET DISCOUNT AMOUNT already covers every X31 discount, so "
                    "posting both would gross Gross Sales up twice. Turn one off."
                )
            )

    def _x24_strict_product_enabled(self):
        """Strict mode: never lazy-create a product for an X24/X48 row whose SKU is
        absent from the X101 master. The transaction is parked instead, forcing the
        team to register the product via X101 first. Gated (default OFF) so other
        tenants keep the legacy auto-stub behaviour."""
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x24_strict_product", "0") in (
            "1",
            "true",
            "True",
        )

    def _x24_decouple_enabled(self):
        """Decouple mode: post X24 sales fully paid against a POS Suspense Clearing
        account (single SUSPENSE payment) instead of joining X70D tenders, so sales
        post regardless of whether payment data exists yet. X70D, when imported, posts
        a transfer entry (Dr per-tender receivable / Cr Suspense) and reconciles the
        suspense lines. Gated (default OFF). Requires x24_close_sessions=1 (the GL is
        only produced at session close)."""
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x24_decouple_payment", "0") in (
            "1",
            "true",
            "True",
        )

    def _x24_close_sessions_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x24_close_sessions", "0") in (
            "1",
            "true",
            "True",
        )

    @staticmethod
    def _x24_codepart(v):
        """Normalise a WAIST/INSEAM cell for composing the sized variant code.

        Numeric cells come back as ``34.0``/``10.0`` floats from the xlsx reader;
        the default_code is built from the integer text (``34``/``10``). A ``-`` or
        blank inseam contributes nothing.
        """
        if v is None:
            return ""
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        s = str(v).strip()
        return "" if s in ("", "-") else s

    @classmethod
    def _mdm_split_size(cls, size, prod_sku=None, tmpl_code=None):
        """Split MDM's single ``size`` string into the (Size, Inseam) pair X101 uses.

        ``("32 28", "002IJ002703228", "002IJ-0027") -> ("32", "28")``

        The returned strings must be byte-identical to the values the XLSX import
        creates, or the API would build a second set of ``product.attribute.value``
        records and the variant matcher would stop resolving.

        Rather than trust a free-text size string, the split is **validated against
        the SKU itself**. X101's PROD SKU is composed as::

            PROD SKU == PRODUCT_CODE without dashes + "0" + SIZE + INSEAM

        (verified against all 214,305 rows of the material master, zero exceptions),
        so the tail after the code is exactly the concatenation we must reproduce.

        Returns ``(size, inseam, ok)``. ``ok`` is False when the tokens could not be
        reconciled with the SKU -- the caller marks the item needs_review rather than
        guessing, because a wrong split silently creates a duplicate variant.
        """

        def _norm(part):
            # _x24_codepart only de-floats real floats; JSON can carry the same value
            # as the string "34.0", which must still become "34" to match X101.
            token = cls._x24_codepart(part)
            if _MDM_INT_FLOAT.fullmatch(token):
                token = token.split(".", 1)[0]
            return token

        raw = "" if size is None else str(size).strip()
        tokens = [t for t in (_norm(p) for p in _MDM_SIZE_SPLIT.split(raw)) if t]

        tail = None
        if prod_sku and tmpl_code:
            prefix = str(tmpl_code).replace("-", "")
            sku = str(prod_sku).strip()
            # The "0" separator is part of the composition rule.
            if sku.upper().startswith(prefix.upper() + "0"):
                tail = sku[len(prefix) + 1 :]

        if tail is None:
            # No SKU to validate against (e.g. a unit test or a partial payload):
            # fall back to positional tokens.
            if len(tokens) >= 2:
                return tokens[0], tokens[1], True
            return (tokens[0] if tokens else raw), "", bool(tokens)

        if not tokens:
            return raw, "", False

        if "".join(tokens) == tail:
            # Tokens reconcile with the SKU. The one ambiguous shape is a single
            # four-digit numeric token ("3228") -- MDM omitted the separator, and a
            # the apparel brand waist is two digits, so it is a waist/inseam pair.
            if len(tokens) == 1 and len(tail) == 4 and tail.isdigit():
                return tail[:2], tail[2:], True
            return tokens[0], (tokens[1] if len(tokens) > 1 else ""), True

        # Tokens contradict the SKU. The SKU wins -- it is what X101 and every X24DN
        # composite lookup are built from -- but the item is flagged for review rather
        # than silently accepted, because one of the two source fields is wrong.
        if len(tail) == 4 and tail.isdigit():
            return tail[:2], tail[2:], False
        if tail:
            return tail, "", False
        return tokens[0], (tokens[1] if len(tokens) > 1 else ""), False

    def _x24_record_pending(self, r, line=None):
        """Register an unresolvable X24DN sales row in the pending-SKU registry.

        Soft-guarded on the model's presence so the loader still runs against a
        database that has not been upgraded to this module version yet.
        """
        if "retail.mdm.pending.sku" not in self.env:
            return
        try:
            self.env["retail.mdm.pending.sku"]._record(r, line=line)
        except Exception:  # a registry failure must never break the import
            _logger.exception("pending-sku record failed for row %s", r.get("_row"))

    def _x24_autoregister_product(self, profile, ns, r, prod_dc, prod_bc):
        """Create a minimal product for an unregistered X24DN merchandise row.

        Only reachable with ``x24_strict_product`` AND
        ``x24_autoregister_from_sales`` both on. The product is flagged
        ``mdm_pending`` and filed under the holding category
        (``retail_import.mdm_pending_categ_id``) so its revenue is quarantined until
        MDM sends the real master, which then upgrades this same record in place.
        """
        Product = self.env["product.product"]
        code = str(r.get("item_code") or "").strip()
        ean = str(r.get("ean") or "").strip()
        w = self._x24_codepart(r.get("waist"))
        ins = self._x24_codepart(r.get("inseam"))
        composite = (code + w + ins) if code else ""
        key = composite or code or ean
        if not key:
            return Product.browse()
        xid = self._safe_xid("x24prod_", key)
        pid = self._xid_get(ns, xid, "product.product")
        if pid:
            product = Product.browse(pid)
        else:
            vals = {
                "name": (str(r.get("item_description") or "").strip() or key)[:200],
                "default_code": composite or code or False,
                "type": "consu",
                "is_storable": True,
                "sale_ok": True,
                "purchase_ok": True,
                "list_price": float(profile._parse_amount(r.get("retail_price")) or 0),
                "mdm_pending": True,
                "mdm_source": "x24_autoregister",
                "mdm_raw_json": {k: v for k, v in r.items() if k != "_row"},
            }
            categ = self._mdm_pending_category()
            if categ:
                vals["categ_id"] = categ.id
            tmpl = (
                self.env["product.template"]
                .with_context(tracking_disable=True, mail_create_nolog=True, mail_create_nosubscribe=True)
                .create(vals)
            )
            product = tmpl.product_variant_id
            if ean and not product.barcode and not Product.search_count([("barcode", "=", ean)]):
                try:
                    product.barcode = ean
                except Exception:
                    pass
            self._xid_set(ns, xid, "product.product", product.id)
            _logger.info("x24 auto-registered pending product %s (%s)", key, product.id)
        # Record it as pending too, so the ops report shows what is quarantined and
        # the master arriving still triggers the in-place upgrade.
        self._x24_record_pending(r)
        for cache, cache_key in ((prod_dc, composite or code), (prod_bc, ean)):
            if cache_key:
                cache[cache_key] = product
        return product

    def _mdm_pending_category(self):
        """The holding category for auto-registered products. Created on demand."""
        icp = self.env["ir.config_parameter"].sudo()
        categ_id = icp.get_param("retail_import.mdm_pending_categ_id", "0")
        Category = self.env["product.category"]
        if str(categ_id).isdigit() and int(categ_id):
            categ = Category.browse(int(categ_id))
            if categ.exists():
                return categ
        categ = Category.search([("name", "=", "MDM Pending")], limit=1)
        if not categ:
            categ = Category.create({"name": "MDM Pending"})
        icp.set_param("retail_import.mdm_pending_categ_id", str(categ.id))
        return categ

    def _x24_is_non_merch(self, r):
        """True for ancillary POS lines that never appear in the X101 garment
        master (paid carrier bags, tailoring services) — flagged by CATEGORY == 'NP'.

        These must not park the whole transaction under strict-product mode; they
        fall through to the lazy-create path so the sale posts and balances.
        """
        return str(r.get("category") or "").strip().upper() == "NP"

    # X24DN records up to four stacked discounts per line, each as its own
    # TYPE / CODE / DESCRIPTION / AMOUNT / PERCENTAGE quintet.
    _X24_DISCOUNT_SLOTS = 4

    def _x24_discount_slots(self, profile, r):
        """The populated discount slots of one X24DN row, in file order."""
        slots = []
        for i in range(1, self._X24_DISCOUNT_SLOTS + 1):
            slot = {
                "type": str(r.get("discount_type_%d" % i) or "").strip(),
                "code": str(r.get("discount_code_%d" % i) or "").strip(),
                "description": str(r.get("discount_description_%d" % i) or "").strip(),
                "amount": float(profile._parse_amount(r.get("discount_amount_%d" % i)) or 0),
                "percentage": float(profile._parse_amount(r.get("discount_percentage_%d" % i)) or 0),
            }
            if any((slot["type"], slot["code"], slot["description"], slot["amount"])):
                slots.append(slot)
        return slots

    @staticmethod
    def _x24_join_slots(slots, key, limit=120):
        """' | '-join one attribute of the discount slots, de-duplicated, order kept."""
        values = [s[key] for s in slots if s.get(key)]
        joined = " | ".join(dict.fromkeys(values))
        return joined[:limit] or False

    @staticmethod
    def _x24_cell(r, key, limit=120):
        """A trimmed, length-capped source cell, or False when blank."""
        value = str(r.get(key) or "").strip()
        return value[:limit] or False

    _X24_NP_SERVICE_PREFIXES = "TS"

    def _x24_np_category(self, code):
        """product.category for a lazy-created non-merchandise product.

        Without one the template is created with no ``categ_id`` and Odoo resolves its
        revenue against the company fallback income account (Gross Sales-Others) instead
        of the labour/other bucket finance expects. Tailoring codes (``TS…``: Original
        Cut, hemming, repair) book to Gross Sales-Labor; the rest (paid carrier bags) to
        Gross Sales-Others.

        The prefix alone is not enough: the apparel brand issues ``TS…`` codes to sold *goods* too
        (Fodable Cup, the apparel brand Pin, TAB, patches, buttons), which are merchandise and must
        not land in the labour bucket. ``x24_np_goods_codes`` lists those exceptions.
        All of it is config data:

          retail_import.x24_np_service_prefixes    (comma-sep, default "TS")
          retail_import.x24_np_goods_codes         (comma-sep exact codes that keep the
                                                    service prefix but are goods)
          retail_import.x24_np_service_category_id (int, else category "Labor (Service)")
          retail_import.x24_np_category_id         (int, else category "Others")
        """
        icp = self.env["ir.config_parameter"].sudo()
        raw = icp.get_param("retail_import.x24_np_service_prefixes", self._X24_NP_SERVICE_PREFIXES)
        prefixes = tuple(p.strip().upper() for p in (raw or "").split(",") if p.strip())
        goods = {
            c.strip().upper()
            for c in (icp.get_param("retail_import.x24_np_goods_codes", "") or "").split(",")
            if c.strip()
        }
        code_up = str(code or "").strip().upper()
        is_service = bool(prefixes) and code_up.startswith(prefixes) and code_up not in goods
        param = "retail_import.x24_np_service_category_id" if is_service else "retail_import.x24_np_category_id"
        Categ = self.env["product.category"]
        cid = int(icp.get_param(param, 0) or 0)
        if cid and Categ.browse(cid).exists():
            return Categ.browse(cid)
        return Categ.search([("name", "=", "Labor (Service)" if is_service else "Others")], limit=1)

    def _load_x24(self, profile, file_b64, log):
        """Phase-5: post pos.order when ``retail_import.x24_post_enabled``, else stage.

        Staging (flag off, default) is the legacy behaviour: parse + persist lines
        marked 'skipped' with a ``store|date|sku`` aggregate_key, no model writes.
        Posting (flag on) creates one pos.order per (store,date,register,transnum)
        with payments joined from X70D. See docs/PHASE5_X24_DESIGN.md.
        """
        self._ri_assert_single_discount_source()
        data = profile.read_records(file_b64)
        records = self._ri_drop_footer_rows(data["records"])
        log.line_count = len(records)
        if not records:
            log.records_skipped = 0
            return
        row_to_line = self._persist_lines(log, records)
        if self._x24_post_enabled():
            self._post_x24(profile, records, log, row_to_line)
        else:
            self._stage_x24(profile, records, log, row_to_line)

    def _stage_x24(self, profile, records, log, row_to_line):
        log.records_skipped = len(records)
        log.error_message = "X24DN POS sales: staged (Phase-5 gated — set retail_import.x24_post_enabled=1 to post)."
        agg = self._aggregate_x24_by_sku_day(records)
        for (store, date, sku), vals in agg.items():
            agg_key = f"{store}|{date}|{sku}"
            line_ids = [row_to_line[rn].id for rn in vals["row_nums"] if rn in row_to_line]
            if line_ids:
                self.env["retail.import.line"].browse(line_ids).write(
                    {
                        "state": "skipped",
                        "aggregate_key": agg_key,
                    }
                )
        self.env.cr.commit()
        _logger.info("x24: %s rows -> %s daily-SKU aggregates (staged)", len(records), len(agg))

    # ------------------------------------------------------------------
    # Phase-5 posting helpers
    # ------------------------------------------------------------------
    def _x24_resolve_tax(self):
        """account.tax for the X24 11% rate (Decision E). Configurable via param."""
        icp = self.env["ir.config_parameter"].sudo()
        tid = icp.get_param("retail_import.x24_tax_id")
        Tax = self.env["account.tax"]
        if tid and Tax.browse(int(tid)).exists():
            return Tax.browse(int(tid))
        return Tax.search(
            [("type_tax_use", "=", "sale"), ("amount", "=", 11.0), ("amount_type", "=", "percent")],
            limit=1,
        )

    def _ri_pos_configs(self, domain=()):
        """Every pos.config, **including archived ones**.

        Stores that closed mid-period (OLS SES - GRAND INDONESIA, PACIFIC PLACE MALL,
        PASKAL BANDUNG) are archived but still carry transactions in the source files,
        and ``resolve_config`` reaches them by xid regardless of ``active``. A plain
        ``search([])`` skips them, so they never received the seeded tender methods nor
        the SUSPENSE method — their orders then died with "The payment method selected
        is not allowed in the config of the POS session."
        """
        return self.env["pos.config"].with_context(active_test=False).search(list(domain))

    def _ri_assert_stores_postable(self, configs):
        """Fail fast when a store the file still transacts against is archived.

        ``resolve_config`` reaches a pos.config by xid regardless of ``active``, so an
        archived store happily accepts orders — but its session cannot close: Odoo
        refuses to post an entry against the store's (also archived) analytic account,
        leaving the session in ``closing_control`` with NO general-ledger entry. The
        import would then look successful while that store's revenue and VAT silently
        never reached the books. Better to stop and have the operator unarchive.
        """
        blocked = []
        for cfg in configs:
            reasons = []
            if not cfg.active:
                reasons.append("pos.config archived")
            # The Operating-Unit analytic reaches the close move via
            # pos.session._get_sale_vals (custom_retail_localization); an archived one
            # makes account.move._post refuse the entry.
            warehouse = cfg.warehouse_id
            if warehouse and "l10n_ou_analytic_id" in warehouse._fields:
                analytic = warehouse.with_context(active_test=False).l10n_ou_analytic_id
                if analytic and not analytic.active:
                    reasons.append("Operating-Unit analytic %r archived" % analytic.name)
            if reasons:
                blocked.append("  - %s: %s" % (cfg.name, ", ".join(reasons)))
        if blocked:
            raise UserError(
                _(
                    "These stores still have transactions in the source file but are archived, "
                    "so their POS session could never post to the general ledger:\n\n%s\n\n"
                    "Unarchive the pos.config and its analytic account (Operating Unit), then "
                    "re-run the import.",
                    "\n".join(blocked),
                )
            )

    def _ri_bridge_vals(self, model, vals):
        """Keep only the ``ri_*`` keys ``model`` actually defines.

        The fields live in the optional ``custom_retail_import_pos`` bridge (this module
        must not depend on point_of_sale — trn_tenant runs the importer without POS),
        so return an empty dict when the model or the fields are absent. Filtering per
        field also lets a tenant sit on an older bridge without the importer exploding.
        """
        if model not in self.env:
            return {}
        fields_ = self.env[model]._fields
        return {k: v for k, v in vals.items() if k in fields_}

    def _ri_src_line_vals(self, net, tax, discount=0.0, is_return=False, source=None):
        """pos.order.line values carrying the source file's own net/tax/discount, plus
        the descriptive columns (cashier, discount slots, line comment) in ``source``."""
        vals = {
            "ri_src_net": net,
            "ri_src_tax": tax,
            "ri_src_discount": discount,
            "ri_is_return": is_return,
        }
        vals.update(source or {})
        return self._ri_bridge_vals("pos.order.line", vals)

    def _ri_src_order_vals(self, source):
        """pos.order values carrying the source file's transaction-level columns."""
        return self._ri_bridge_vals("pos.order", source or {})

    # ------------------------------------------------------------------
    # Operating Unit — the per-store analytic every posted line should carry
    # ------------------------------------------------------------------
    def _ri_config_ou(self, cfg):
        """Operating-Unit analytic of a pos.config's store, when the tenant defines one.

        ``l10n_ou_analytic_id`` comes from ``custom_retail_localization``; on a tenant
        without it this returns an empty recordset and every OU stamp below no-ops.
        """
        Empty = self.env["account.analytic.account"].browse()
        warehouse = cfg.warehouse_id if cfg else False
        if warehouse and "l10n_ou_analytic_id" in warehouse._fields:
            return warehouse.with_context(active_test=False).l10n_ou_analytic_id
        return Empty

    def _ri_ou_line_vals(self, ou):
        """account.move.line values stamping ``ou`` as the line's Operating Unit.

        The POS closing entry gets its OU from ``pos.session._get_sale_vals`` and
        friends, but the reclass / settlement entries this module posts by hand build
        their lines from scratch — without this they land outside the per-OU reporting
        exactly like the VAT and suspense lines used to.
        """
        if not ou:
            return {}
        POL = self.env["purchase.order.line"] if "purchase.order.line" in self.env else None
        if POL is not None and hasattr(POL, "_retail_merge_ou_distribution"):
            distribution = POL._retail_merge_ou_distribution(False, ou.id)
        else:
            distribution = {str(ou.id): 100.0}
        vals = {"analytic_distribution": distribution}
        if "l10n_ou_analytic_id" in self.env["account.move.line"]._fields:
            vals["l10n_ou_analytic_id"] = ou.id
        return vals

    def _x24_tender_index(self, profile):
        """Build {(store,date,reg,txn): [(tender_type, amount)]} from staged X70D lines.

        Sourced from the most recent imported X70D log so X70D must be synced before
        X24 posting. Rows with a blank tender_type (file totals) are ignored.
        """
        Log = self.env["retail.import.log"].sudo()
        x70d_profile = self.env["retail.import.profile"].search(
            [("file_type", "=", "x70d"), ("company_id", "=", profile.company_id.id)], limit=1
        )
        idx = defaultdict(list)
        if not x70d_profile:
            return idx
        log = Log.search([("profile_id", "=", x70d_profile.id)], order="id desc", limit=1)
        if not log:
            return idx
        lines = self.env["retail.import.line"].sudo().search([("log_id", "=", log.id)])
        for ln in lines:
            try:
                r = json.loads(ln.raw_data_json or "{}")
            except Exception:
                continue
            tt = str(r.get("tender_type") or "").strip()
            if not tt:
                continue
            key = (
                str(r.get("store_code") or "").strip(),
                str(r.get("trans_date") or "").strip(),
                str(r.get("register") or "").strip(),
                str(r.get("transnum") or "").strip(),
            )
            try:
                amt = float(r.get("tender_amount") or 0)
            except Exception:
                amt = 0.0
            idx[key].append((tt, amt))
        return idx

    def _x24_per_date_sessions_enabled(self):
        """Whether each trading day gets its own POS session (hence its own GL entry).

        Odoo refuses a second non-closed session on the same pos.config, so a day's
        sessions must close before the next day's open — which only happens when
        ``retail_import.x24_close_sessions`` is on. With closing off there is no GL to
        date anyway, so the import keeps the legacy one-session-per-store shape.
        """
        return self._x24_close_sessions_enabled()

    def _x24_day_batches(self, profile, orders):
        """``orders`` split into ``(trans_date, {key: rows})`` batches, oldest day first.

        Grouping the whole import into one session per store dated every entry — sales,
        VAT, suspense, discounts — on the file's *last* trans date. Batching per day puts
        each day's journal entry on the day it belongs to.
        """
        if not self._x24_per_date_sessions_enabled():
            return [(None, orders)]
        per_date = defaultdict(dict)
        for key, rows in orders.items():
            per_date[key[1]][key] = rows
        # Unparseable dates sort last; their orders fall back to ``now`` as before.
        return sorted(per_date.items(), key=lambda kv: profile._parse_date(kv[0]) or datetime.max.date())

    def _x24_group_orders(self, records):
        orders = defaultdict(list)
        for r in records:
            store = str(r.get("store_code") or "").strip()
            txn = str(r.get("transnum") or "").strip()
            if not store or not txn:
                continue
            key = (store, str(r.get("trans_date") or "").strip(), str(r.get("register") or "").strip(), txn)
            orders[key].append(r)
        return orders

    def _post_x24(self, profile, records, log, row_to_line, replay=False):
        """Post X24DN rows as pos.order.

        ``replay=True`` re-enters with the parked rows of an already-processed log,
        once the missing products have arrived in the master. Two things change, and
        only two: the whole-file guard is skipped (it exists to stop a *second file*
        being imported, not to stop a parked transaction being finished), and the log
        counters are merged instead of overwritten. Every other safety property is
        unchanged and is what makes replay idempotent -- in particular the per-order
        xid guard below skips transactions that already posted, and a transaction
        whose lines still do not all resolve is parked again rather than half-posted.
        """
        ns = profile.namespace
        Product = self.env["product.product"]
        Order = self.env["pos.order"]
        Line = self.env["retail.import.line"]
        icp = self.env["ir.config_parameter"].sudo()

        # Whole-file idempotency guard (mirror _load_x20).
        if not replay:
            prior = self.env["retail.import.log"].search(
                [("profile_id", "=", profile.id), ("state", "=", "imported"), ("id", "!=", log.id)], limit=1
            )
            if prior:
                raise UserError(
                    _("X24 already posted (log #%s). Archive it before re-posting to avoid duplicate sales.") % prior.id
                )

        # Ensure each tender method books to its own GL receivable account so the
        # session-close journal splits cash / card / e-wallet instead of piling every
        # tender into the single company-default Trade Receivables. Idempotent.
        self._x24_ensure_method_gl_split()

        # Decouple mode: post each order fully paid against a POS Suspense Clearing
        # account (single SUSPENSE payment), ignoring X70D tenders. GL only forms at
        # session close, so decouple requires x24_close_sessions=1.
        decouple = self._x24_decouple_enabled()
        suspense_method = None
        if decouple:
            if not self._x24_close_sessions_enabled():
                raise UserError(
                    _(
                        "Decouple mode (retail_import.x24_decouple_payment=1) requires "
                        "retail_import.x24_close_sessions=1 — the GL is only produced at "
                        "POS session close."
                    )
                )
            suspense_method = self._x24_ensure_suspense_method()

        self._x24_automap_missing(ns, records)
        tax = self._x24_resolve_tax()
        tenders = {} if decouple else self._x24_tender_index(profile)
        orders = self._x24_group_orders(records)
        strict_products = self._x24_strict_product_enabled()
        autoregister = strict_products and self._mdm_flag("x24_autoregister_from_sales")

        cfg_cache, sess_cache, method_cache = {}, {}, {}
        prod_bc, prod_dc = {}, {}
        created = skipped = disc_slot_mismatch = 0
        errors = []
        # One dict per posted line carrying a NET DISCOUNT AMOUNT: the resolved product,
        # the amount, the trading day, the store's config and the source discount slots.
        posted_disc = []

        def resolve_config(store):
            if store not in cfg_cache:
                cid = self._xid_get(ns, self._safe_xid("posconfig_", store), "pos.config")
                cfg_cache[store] = self.env["pos.config"].browse(cid) if cid else False
            return cfg_cache[store]

        def get_session(cfg):
            # One session per config **within the current day batch**: Odoo forbids >1
            # non-closed session per pos.config, so the caller closes a day's sessions
            # before the next day opens its own. ``_pos_close_and_backdate`` then stamps
            # each closing entry with that day's date.
            k = cfg.id
            if k not in sess_cache:
                s = self.env["pos.session"].create({"config_id": cfg.id, "user_id": self.env.uid})
                # Decision B: imported POS is financial-only and must NOT move stock.
                # Merchandise is now storable (is_storable=True), so without this the
                # close would create pickings/stock moves and double-count on-hand
                # (the X20 snapshot already sets opening quantities). pos.session.create
                # forces this flag from company config, so override it post-create.
                if s.update_stock_at_closing:
                    s.update_stock_at_closing = False
                if s.state != "opened":
                    # Proper opening (cash control) so the close can book cash to
                    # Cash-on-hand instead of Cash Difference Loss.
                    try:
                        s.set_opening_control(0, None)
                    except Exception:
                        pass
                    if s.state != "opened":
                        s.write({"state": "opened"})
                sess_cache[k] = s
            return sess_cache[k]

        def resolve_product(r):
            ean = str(r.get("ean") or "").strip()
            code = str(r.get("item_code") or "").strip()
            if ean:
                if ean not in prod_bc:
                    prod_bc[ean] = Product._resolve_barcode(ean)
                if prod_bc[ean]:
                    return prod_bc[ean]
            if code:
                if code not in prod_dc:
                    prod_dc[code] = Product.search([("default_code", "=", code)], limit=1)
                if prod_dc[code]:
                    return prod_dc[code]
            # X24 carries the *base* article code plus WAIST/INSEAM in separate columns,
            # but X101 stores the full sized variant as default_code (base+waist+inseam,
            # e.g. 000YB00010 + 34 + 10 -> 000YB000103410). Reconstruct and retry so a
            # registered garment is not falsely flagged "belum teregister di master X101".
            w = self._x24_codepart(r.get("waist"))
            ins = self._x24_codepart(r.get("inseam"))
            for comp in (code + w + ins, code + w):
                if comp and comp != code:
                    if comp not in prod_dc:
                        prod_dc[comp] = Product.search([("default_code", "=", comp)], limit=1)
                    if prod_dc[comp]:
                        return prod_dc[comp]
            # Strict mode: refuse to invent a *merchandise* product for an unregistered
            # garment — return empty so the caller parks the whole transaction until X101
            # is synced. Non-merchandise lines (category "NP": carrier bags, tailoring
            # services) never live in the X101 garment master, so let them fall through to
            # the lazy-create path below instead of detonating the entire sale.
            if strict_products and not self._x24_is_non_merch(r):
                if autoregister:
                    # Post the sale now against a minimal product parked in a holding
                    # category, so its revenue does not land in a real Gross Sales
                    # bucket before MDM confirms the taxonomy. When the master arrives
                    # the template is upgraded in place -- same record id, so the
                    # pos.order.line written here stays valid.
                    return self._x24_autoregister_product(profile, ns, r, prod_dc, prod_bc)
                return Product.browse()
            # Lazy-create a non-merchandise product (carrier bags, vouchers, etc. sold at
            # POS but absent from the X101 master) so the order is complete and balances
            # against the X70D tender. Idempotent by xid. Parity with legacy 8548b52.
            key = code or ean
            if not key:
                return Product.browse()
            xid = self._safe_xid("x24prod_", key)
            pid = self._xid_get(ns, xid, "product.product")
            if pid:
                p = Product.browse(pid)
            else:
                tmpl_vals = {
                    "name": (str(r.get("item_description") or "").strip() or key)[:200],
                    "default_code": code or False,
                    "type": "consu",
                    "sale_ok": True,
                    "list_price": float(profile._parse_amount(r.get("retail_price")) or 0),
                }
                # Only genuine non-merchandise gets a revenue bucket. With strict mode off
                # this same path also lazy-creates unmatched *garments*, and filing those
                # under "Others" would misstate their revenue, COGS and valuation.
                if self._x24_is_non_merch(r):
                    np_categ = self._x24_np_category(code)
                    if np_categ:
                        tmpl_vals["categ_id"] = np_categ.id
                tmpl = (
                    self.env["product.template"]
                    .with_context(tracking_disable=True, mail_create_nolog=True)
                    .create(tmpl_vals)
                )
                p = tmpl.product_variant_id
                if ean and not p.barcode and not Product.search_count([("barcode", "=", ean)]):
                    try:
                        p.barcode = ean
                    except Exception:
                        pass
                self._xid_set(ns, xid, "product.product", p.id)
            if code:
                prod_dc[code] = p
            if ean:
                prod_bc[ean] = p
            return p

        def map_method(cfg, tender_type):
            tt = self._X24_TENDER_FOLD.get(tender_type, tender_type)
            k = (cfg.id, tt)
            if k not in method_cache:
                method_cache[k] = cfg.payment_method_ids.filtered(lambda m: (m.name or "") == tt)[:1]
            return method_cache[k]

        def fail_rows(rows, msg):
            for r in rows:
                rn = r.get("_row")
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": msg[:250]})
            errors.append((rows[0].get("_row"), msg))

        # Preflight: an archived store accepts orders but its session can never post.
        self._ri_assert_stores_postable({cfg for cfg in (resolve_config(k[0]) for k in orders) if cfg})

        # One batch of orders per trading day, oldest first. Each batch opens its own
        # sessions and closes them before the next day starts, so the closing entry
        # (Gross Sales + VAT + POS Suspense / tender receivable) is dated on the day the
        # sale happened rather than on the last date of the whole file.
        for _batch_date, batch_orders in self._x24_day_batches(profile, orders):
            sess_cache = {}
            for key, rows in batch_orders.items():
                store, date, reg, txn = key
                oxid = self._safe_xid("posorder_", f"{store}_{date}_{reg}_{txn}")
                if self._xid_get(ns, oxid, "pos.order"):
                    continue  # already posted
                cfg = resolve_config(store)
                if not cfg:
                    fail_rows(rows, f"store {store}: no pos.config (map xid posconfig_{store})")
                    skipped += len(rows)
                    continue

                line_cmds, order_net, order_incl, missing = [], 0.0, 0.0, []
                order_disc = []
                for r in rows:
                    prod = resolve_product(r)
                    if not prod:
                        missing.append(str(r.get("item_code") or r.get("ean") or "?"))
                        rn = r.get("_row")
                        line = row_to_line.get(rn)
                        if line:
                            line.write(
                                {
                                    "state": "error",
                                    "error_message": f"not in X101 master: ean={r.get('ean')!r} code={r.get('item_code')!r}"[
                                        :250
                                    ],
                                }
                            )
                        # Remember the SKU so the master arriving later can replay this
                        # transaction automatically instead of waiting for someone to
                        # notice it in the log.
                        self._x24_record_pending(r, line)
                        continue
                    qty = float(profile._parse_amount(r.get("net_qty")))
                    incl = float(profile._parse_amount(r.get("total_amount")))
                    rate = str(r.get("tax_rate") or "").strip()
                    taxed = bool(tax) and rate not in ("", "None")
                    # X24DN is the source of truth: NET SOLD AMOUNT / TAX AMOUNT are taken
                    # verbatim. The file truncates net to whole rupiah per line
                    # (net = trunc(total/1.11), tax = total - net) whereas Odoo rounds the
                    # tax globally per order, so recomputing here drifts by ~1 rupiah on more
                    # than half the lines. ``ri_src_*`` carries the file's figures through to
                    # session close via pos.order.line._prepare_base_line_for_taxes_computation
                    # (custom_retail_import_pos).
                    src_net = float(profile._parse_amount(r.get("net_amount")) or 0)
                    src_tax = float(profile._parse_amount(r.get("tax_amount")) or 0)
                    src_disc = float(profile._parse_amount(r.get("net_discount")) or 0)
                    if not (src_net or src_tax):
                        # Older extracts without the net/tax columns: fall back to deriving.
                        rate_val = (tax.amount / 100.0) if taxed else 0.0
                        src_net, src_tax = incl / (1.0 + rate_val), incl - incl / (1.0 + rate_val)
                    gl_net = src_net
                    tax_ids = [tax.id] if taxed else []
                    # NET DISCOUNT AMOUNT stays the GL figure, but the four slots explain it:
                    # they name the promo (code + description) that the reclass entry labels
                    # its lines with. A slot total that disagrees with NET DISCOUNT AMOUNT means
                    # the extract is inconsistent — surfaced in the log, never silently fixed.
                    slots = self._x24_discount_slots(profile, r)
                    if slots and round(sum(s["amount"] for s in slots), 2) != round(src_disc, 2):
                        disc_slot_mismatch += 1
                    # A negative NET SOLD QUANTITY inside a sale transaction is a return:
                    # X24DN books an in-store exchange as a qty=-1 line paired with a qty=+1
                    # one. It belongs on Sales Return-<category> (53xxxxx), not as a debit
                    # against Gross Sales-<category> — same treatment as an X48 refund. The
                    # bridge repoints the account off ``ri_is_return``.
                    is_return = qty < 0
                    # The sale tax is PRICE-INCLUDED, so POS extracts net from price_unit and
                    # ignores a forced price_subtotal. price_unit must therefore carry the
                    # tax-INCLUSIVE amount (the paid ``incl``) so the order total matches the
                    # tender exactly and nothing plugs to Cash Difference.
                    unit = incl / qty if qty else incl
                    line_cmds.append(
                        (
                            0,
                            0,
                            dict(
                                {
                                    "product_id": prod.id,
                                    "qty": qty,
                                    "price_unit": unit,
                                    "discount": 0.0,
                                    "tax_ids": [(6, 0, tax_ids)],
                                    "price_subtotal": gl_net,
                                    "price_subtotal_incl": incl,
                                },
                                **self._ri_src_line_vals(
                                    net=src_net,
                                    tax=src_tax,
                                    discount=src_disc,
                                    is_return=is_return,
                                    source={
                                        "ri_staff_id": self._x24_cell(r, "staff_id", 64),
                                        "ri_staff_name": self._x24_cell(r, "staff_name"),
                                        "ri_discount_type": self._x24_join_slots(slots, "type"),
                                        "ri_discount_code": self._x24_join_slots(slots, "code"),
                                        "ri_discount_description": self._x24_join_slots(slots, "description"),
                                        "ri_line_comment": self._x24_cell(r, "line_comment"),
                                    },
                                ),
                            ),
                        )
                    )
                    order_net += gl_net
                    order_incl += incl
                    if src_disc:
                        # Carry the RESOLVED product: the discount reclass must not re-look-up
                        # by bare item_code — X101 variants are keyed on code+waist+inseam, so
                        # a default_code search misses most rows.
                        order_disc.append((prod, src_disc, slots))

                # Strict mode / any unresolved line: park the WHOLE transaction rather than
                # posting a partial order (which would later fail the tender-balance check).
                if missing:
                    sku_list = ", ".join(sorted(set(missing))[:10])
                    fail_rows(
                        rows,
                        f"store {store} txn {txn}: produk belum teregister di master X101 ({sku_list}) — sync X101 dulu",
                    )
                    skipped += len(rows)
                    continue
                if not line_cmds:
                    fail_rows(rows, f"store {store} txn {txn}: no resolvable product")
                    skipped += len(rows)
                    continue

                if not decouple:
                    tlist = tenders.get(key, [])
                    pay_total = sum(a for _, a in tlist)
                    if not tlist:
                        fail_rows(rows, f"store {store} txn {txn}: no X70D tender (sync X70D first)")
                        skipped += len(rows)
                        continue
                    if abs(pay_total - order_incl) > self._X24_BALANCE_TOL:
                        fail_rows(
                            rows, f"store {store} txn {txn}: unbalanced lines={order_incl:.2f} tenders={pay_total:.2f}"
                        )
                        skipped += len(rows)
                        continue

                d = profile._parse_date(date)
                dt = datetime(d.year, d.month, d.day, 12, 0, 0) if d else datetime.now()
                order_date = d
                # Session is created outside the per-order savepoint so a single bad
                # order does not roll back the shared session.
                sess = get_session(cfg)
                try:
                    with self.env.cr.savepoint():
                        order = Order.create(
                            dict(
                                {
                                    "session_id": sess.id,
                                    "company_id": cfg.company_id.id,
                                    "pricelist_id": cfg.pricelist_id.id or False,
                                    "date_order": dt,
                                    "pos_reference": f"{store}-{reg}-{txn}",
                                    "lines": line_cmds,
                                    "amount_tax": order_incl - order_net,
                                    "amount_total": order_incl,
                                    "amount_paid": 0.0,
                                    "amount_return": 0.0,
                                },
                                **self._ri_src_order_vals(
                                    {
                                        # X24DN repeats these on every line of the transaction.
                                        "ri_staff_id": self._x24_cell(rows[0], "staff_id", 64),
                                        "ri_staff_name": self._x24_cell(rows[0], "staff_name"),
                                        "ri_member_id": self._x24_cell(rows[0], "member_id", 64),
                                        "ri_member_type": self._x24_cell(rows[0], "member_type", 64),
                                        "ri_customer_phone": self._x24_cell(rows[0], "customer_phone", 64),
                                        "ri_transaction_note": self._x24_cell(rows[0], "transaction_note", 250),
                                        "ri_omni_order_id": self._x24_cell(rows[0], "omni_order_id", 64),
                                    }
                                ),
                            )
                        )
                        if decouple:
                            # Pay the full amount to the POS Suspense Clearing method; X70D
                            # reconciliation later transfers it to the real tender receivables.
                            order.add_payment(
                                {
                                    "pos_order_id": order.id,
                                    "payment_method_id": suspense_method[cfg.company_id.id].id,
                                    "amount": order_incl,
                                    "payment_date": dt,
                                }
                            )
                        else:
                            for tt, amt in tlist:
                                m = map_method(cfg, tt)
                                if not m:
                                    errors.append((rows[0].get("_row"), f"{store}/{txn}: no method for tender {tt}"))
                                    continue
                                order.add_payment(
                                    {
                                        "pos_order_id": order.id,
                                        "payment_method_id": m.id,
                                        "amount": amt,
                                        "payment_date": dt,
                                    }
                                )
                        # ``pos.order.amount_paid`` is a plain stored field (no @api.depends)
                        # in Odoo 19: writing pos.payment rows does NOT refresh it (the UI
                        # relies on an onchange). Recompute from payments before the paid gate
                        # so orders are actually marked paid (mirrors fix b68559b).
                        order.invalidate_recordset(["payment_ids", "amount_paid"])
                        order.amount_paid = sum(order.payment_ids.mapped("amount"))
                        # Finalize whenever the tender matches the total — including a
                        # zero-value giveaway (100% discount, TOTAL AMOUNT 0) or a net-negative
                        # refund. POS refuses to CLOSE a session that still holds any 'draft'
                        # order, so a 0/negative order left un-paid would silently block the
                        # whole session's GL (close_session_from_ui returns successful=False).
                        if abs(order.amount_paid - order.amount_total) < 0.01:
                            order.action_pos_order_paid()
                        self._xid_set(ns, oxid, "pos.order", order.id)
                except Exception as e:  # per-order savepoint rolled back; keep going
                    fail_rows(rows, f"store {store} txn {txn}: post failed: {e}")
                    skipped += len(rows)
                    continue
                for r in rows:
                    rn = r.get("_row")
                    if rn in row_to_line:
                        row_to_line[rn].write(
                            {
                                "state": "ok",
                                "target_model": "pos.order",
                                "target_res_id": order.id,
                                "aggregate_key": f"{store}|{date}|{txn}",
                            }
                        )
                posted_disc.extend(
                    {"product": p, "amount": amount, "date": order_date, "config": cfg, "slots": slots}
                    for p, amount, slots in order_disc
                )
                created += 1
                if created % 200 == 0:
                    self.env.cr.commit()

            # Close this day's sessions before the next day opens one on the same
            # pos.config — Odoo allows a single non-closed session per config.
            self._pos_close_and_backdate(sess_cache.values(), errors, "x24")

        if disc_slot_mismatch:
            errors.append(
                (
                    None,
                    f"{disc_slot_mismatch} line(s): DISCOUNT AMOUNT 1..4 do not "
                    f"add up to NET DISCOUNT AMOUNT; the latter was booked",
                )
            )

        if self._x24_discount_reclass_enabled():
            self._post_x24_discount_reclass(profile, records, posted_disc, log)

        self._ri_commit()
        if replay:
            # The log already carries the counts of the original run; a replay only
            # ever converts parked rows into posted ones, so add rather than replace
            # and recompute the error state from what is actually still parked.
            log.records_created += created
            log.records_skipped = max(0, log.records_skipped - created)
            self._x24_refresh_log_errors(log)
        else:
            log.records_created = created
            log.records_skipped = skipped
            log.set_errors(errors)
        _logger.info(
            "x24 %s: %s orders created, %s rows skipped, %s errors",
            "REPLAY" if replay else "POST",
            created,
            skipped,
            len(errors),
        )
        return {"created": created, "skipped": skipped, "errors": errors}

    def _x24_refresh_log_errors(self, log):
        """Recompute a log's error state from its lines after a partial replay.

        ``set_errors`` takes the errors of one run; after a replay the truth is
        whatever rows are *still* in error. When the last one clears, the log moves
        from ``partial`` back to ``imported``.
        """
        Line = self.env["retail.import.line"]
        remaining = Line.search([("log_id", "=", log.id), ("state", "=", "error")], order="row_number")
        log.error_count = len(remaining)
        if remaining:
            log.raw_payload = "\n".join(f"row {ln.row_number}: {ln.error_message or ''}" for ln in remaining[:200])
            if log.state == "imported":
                log.state = "partial"
        else:
            log.raw_payload = False
            if log.state == "partial":
                log.state = "imported"

    def _ri_backdate_session_payments(self, session, gl_date, tag):
        """Re-stamp the settlement records the POS close emits alongside its own entry.

        With ``x24_decouple_payment`` OFF the orders are tendered against the real payment
        methods, so ``pos.session._create_account_move`` also emits an ``account.payment``
        per bank tender and an ``account.bank.statement.line`` per cash tender. Both are
        dated at close — i.e. the import day — while the sale they settle is dated on its
        trading day, which leaves the bank/cash journals in the wrong period. Both models
        carry ``pos_session_id``, so they are reachable from here.

        Decouple mode never reaches this: its SUSPENSE method is ``pay_later``, so the
        close writes no payment and no statement line.
        """
        env = self.env
        if "account.payment" in env:
            payments = env["account.payment"].sudo().search([("pos_session_id", "=", session.id)])
            for pay in payments:
                if pay.move_id:
                    self._ri_backdate_move(pay.move_id, gl_date, tag)
            if payments:
                env.cr.execute("UPDATE account_payment SET date=%s WHERE id = ANY(%s)", (gl_date, payments.ids))
                payments.invalidate_recordset(["date"])
        if "statement_line_ids" in session._fields:
            for line in session.statement_line_ids:
                if line.move_id:
                    self._ri_backdate_move(line.move_id, gl_date, tag)

    def _pos_close_and_backdate(self, sessions, errors, tag):
        """Close each open POS session (cash-control aware) and re-stamp its GL move to
        the latest order date. Gated by ``retail_import.x24_close_sessions``. Shared by
        X24 sales and X48 refunds. POS dates the close move at ``context_today`` and
        re-applies it on _post, so the backdate is done via SQL (safe within the same
        fiscal year — the annual sequence stays valid). Guards against stock moves
        (Decision B: imported POS must not touch inventory)."""
        icp = self.env["ir.config_parameter"].sudo()
        if icp.get_param("retail_import.x24_close_sessions", "0") not in ("1", "true", "True"):
            return
        for s in sessions:
            try:
                try:
                    s.post_closing_cash_details(s.cash_register_balance_end)
                    s.close_session_from_ui()
                except Exception as ce:
                    # Never swallow silently: a session left in `closing_control` produces
                    # NO GL at all, so its whole store's revenue/VAT would vanish from the
                    # books while the import still reports "imported".
                    _logger.error("%s POST: session %s (%s) close failed: %s", tag, s.id, s.config_id.name, ce)
                    errors.append((None, f"session {s.id} ({s.config_id.name}) close failed: {ce}"))
                    s.action_pos_session_closing_control()
                moves = s.order_ids.picking_ids.mapped("move_ids")
                if moves:
                    msg = (
                        f"session {s.id}: UNEXPECTED {len(moves)} stock move(s) on close "
                        f"(products became storable?) — inventory may be double-counted"
                    )
                    _logger.error("%s POST: %s", tag, msg)
                    errors.append((None, msg))
                elif s.move_id:
                    try:
                        dates = [d for d in s.order_ids.mapped("date_order") if d]
                        mv = s.move_id
                        if dates and mv.date and max(dates).date().year == mv.date.year:
                            gl = max(dates).date()
                            self._ri_restamp_move_date(mv, gl)
                            # The session itself is opened "now", so its own dates would
                            # keep pointing at the import day in the POS session list.
                            self.env.cr.execute(
                                "UPDATE pos_session SET start_at=%s, stop_at=%s WHERE id=%s",
                                (
                                    datetime(gl.year, gl.month, gl.day, 8, 0, 0),
                                    datetime(gl.year, gl.month, gl.day, 22, 0, 0),
                                    s.id,
                                ),
                            )
                            s.invalidate_recordset(["start_at", "stop_at"])
                            self._ri_backdate_session_payments(s, gl, tag)
                    except Exception as be:
                        _logger.warning("%s POST: session %s backdate skipped: %s", tag, s.id, be)
                    _logger.info(
                        "%s POST: session %s closed, journal %s dated %s (no stock moves)",
                        tag,
                        s.id,
                        s.move_id.name,
                        s.move_id.date,
                    )
            except Exception as e:
                errors.append((None, f"session {s.id} close failed: {e}"))

    # ------------------------------------------------------------------
    # Decision A — store_code -> pos.config mapping (posconfig_<store> xids)
    # ------------------------------------------------------------------
    @staticmethod
    def _norm_store_name(name):
        """Normalise a store/config label to its bare mall name for matching.

        Strips the outlet-type prefix ('OLS SES -', 'OLS SCU -', 'OLS -', 'OLS')
        and collapses whitespace/case so 'OLS SES - TUNJUNGAN PLAZA 3' and
        'OLS SCU - TUNJUNGAN PLAZA 3' both reduce to 'TUNJUNGAN PLAZA 3'.
        """
        s = " ".join(str(name or "").upper().split())
        for pre in ("OLS SES -", "OLS SCU -", "OLS SES", "OLS SCU", "OLS -", "OLS"):
            if s.startswith(pre):
                s = s[len(pre) :].strip()
                break
        return " ".join(s.split())

    def _x24_stores_from_staged(self):
        """Distinct {store_code: (sap, store_name)} from the latest staged X24 log."""
        Prof = self.env["retail.import.profile"]
        p24 = Prof.search([("file_type", "=", "x24")], limit=1)
        Log = self.env["retail.import.log"].sudo()
        log = Log.search([("profile_id", "=", p24.id)], order="id desc", limit=1) if p24 else Log
        stores = {}
        if not log:
            return stores
        for ln in self.env["retail.import.line"].sudo().search([("log_id", "=", log.id)]):
            try:
                r = json.loads(ln.raw_data_json or "{}")
            except Exception:
                continue
            sc = str(r.get("store_code") or "").strip()
            if not sc or not sc.isdigit():
                continue
            stores[sc] = (str(r.get("sap_store_code") or "").strip(), str(r.get("store_name") or "").strip())
        return stores

    def _x24_map_stores_to_configs(self, stores=None, commit=False):
        """Map store codes to pos.config by normalised name; write posconfig_<code> xids.

        Two-tier, correctness-first: (1) unique **exact** normalised-name match;
        (2) unique **containment** match (one config whose bare name contains, or is
        contained by, the store's) flagged 'fuzzy'; otherwise 'ambiguous'/'unmatched'
        and left for a human. Returns a list of
        (store_code, store_name, config_id, config_name, method). Writes xids only
        for exact+fuzzy matches when ``commit`` (idempotent — skips existing).
        """
        p24 = self.env["retail.import.profile"].search([("file_type", "=", "x24")], limit=1)
        ns = p24.namespace if p24 else "retail"
        if stores is None:
            stores = self._x24_stores_from_staged()
        norm_cfg = defaultdict(list)
        for c in self._ri_pos_configs():
            norm_cfg[self._norm_store_name(c.name)].append(c)

        report, written = [], 0
        for code in sorted(stores):
            _sap, name = stores[code]
            sn = self._norm_store_name(name)
            cfg, method = False, "unmatched"
            if sn and len(norm_cfg.get(sn, [])) == 1:
                cfg, method = norm_cfg[sn][0], "exact"
            elif sn:
                cands = {
                    c.id: c
                    for nn, cs in norm_cfg.items()
                    if nn and (nn.startswith(sn) or sn.startswith(nn))
                    for c in cs
                }
                if len(cands) == 1:
                    cfg, method = list(cands.values())[0], "fuzzy"
                elif len(cands) > 1:
                    method = "ambiguous"
            report.append((code, name, cfg.id if cfg else None, cfg.name if cfg else None, method))
            if commit and cfg:
                xn = self._safe_xid("posconfig_", code)
                if not self._xid_get(ns, xn, "pos.config"):
                    self._xid_set(ns, xn, "pos.config", cfg.id)
                    written += 1
        if commit:
            self.env.cr.commit()
            _logger.info("x24 store->config: %s xids written", written)
        return report

    def _x24_automap_missing(self, ns, records):
        """Auto-map stores in this batch that have no ``posconfig_<code>`` xid yet.

        A newly opened (or reactivated) store would otherwise fail every row with
        "no pos.config" until someone runs ``_x24_map_stores_to_configs`` by hand.
        Delegates to that mapper, so only a UNIQUE exact/containment name match is
        written; ambiguous/unmatched stores are left alone and their rows keep
        failing with the same actionable message as before.
        """
        missing, seen = {}, set()
        for r in records:
            sc = str(r.get("store_code") or "").strip()
            if not sc or not sc.isdigit() or sc in seen:
                continue
            seen.add(sc)
            if not self._xid_get(ns, self._safe_xid("posconfig_", sc), "pos.config"):
                missing[sc] = (
                    str(r.get("sap_store_code") or "").strip(),
                    str(r.get("store_name") or "").strip(),
                )
        if not missing:
            return
        for code, name, _cfg_id, cfg_name, method in self._x24_map_stores_to_configs(stores=missing, commit=True):
            _logger.info("x24 automap store %s (%s) -> %s [%s]", code, name, cfg_name, method)

    def _x24_seed_payment_methods(self):
        """Idempotently create the 4 tender methods missing from the seed (Decision D).

        Creates AMEX/OVO/SODEXO (+ folds OTHER_CARD into OFFLINE_OTHER_CREDITCARD) per
        company that already has POS methods, and links them to that company's configs.
        Returns a summary dict. Safe to call repeatedly.
        """
        Method = self.env["pos.payment.method"]
        created = {}
        companies = self._ri_pos_configs().mapped("company_id")
        for company in companies:
            existing = Method.search([("company_id", "=", company.id)])
            names = set(existing.mapped("name"))
            template = existing.filtered(lambda m: m.name == "OFFLINE_OTHER_CREDITCARD")[:1] or existing[:1]
            for tname in self._X24_SEED_TENDERS:
                if tname in names:
                    continue
                vals = {"name": tname, "company_id": company.id}
                if template and template.journal_id:
                    vals["journal_id"] = template.journal_id.id
                m = Method.create(vals)
                created.setdefault(company.id, []).append(tname)
                # attach to that company's configs so map_method finds it
                cfgs = self._ri_pos_configs([("company_id", "=", company.id)])
                for c in cfgs:
                    c.write({"payment_method_ids": [(4, m.id)]})
        # newly-seeded methods must also get their own GL receivable
        self._x24_ensure_method_gl_split()
        return created

    # ------------------------------------------------------------------
    # GL payment separation — one receivable account per tender type
    # ------------------------------------------------------------------
    def _x24_next_free_account_code(self, company, base):
        """First unused account code at/after ``base`` for ``company`` (codes are
        company-dependent in Odoo 19, so search under with_company)."""
        Account = self.env["account.account"].sudo().with_company(company)
        try:
            start = int(str(base)) + 100
        except (TypeError, ValueError):
            start = 1106000101
        for i in range(start, start + 5000):
            code = str(i)
            if not Account.search([("code", "=", code)], limit=1):
                return code
        raise UserError(_("Could not allocate a free account code near %s") % base)

    def _x24_recv_account_for(self, company, tender):
        """Get/create a distinct POS-receivable account for a tender type (reuse-first).

        Cloned from the company's default POS receivable (same account_type, reconcile)
        so it behaves identically but keeps each tender on its own GL line.
        """
        Account = self.env["account.account"].sudo().with_company(company)
        label = "POS Receivable - %s" % tender
        acc = Account.search([("name", "=", label)], limit=1)
        if acc:
            return acc
        default_recv = company.account_default_pos_receivable_account_id
        base = default_recv.with_company(company).code if default_recv else "1106000001"
        return Account.create(
            {
                "name": label,
                "code": self._x24_next_free_account_code(company, base),
                "account_type": (default_recv.account_type if default_recv else "asset_receivable"),
                "reconcile": True,
                "company_ids": [(4, company.id)],
            }
        )

    def _x24_ensure_method_gl_split(self):
        """Give every non-cash POS tender method its own receivable account so card /
        e-wallet settlements rest on distinct GL lines (awaiting bank settlement /
        X70T reconciliation) instead of the single shared default Trade Receivables.

        CASH is skipped on purpose: cash settles to its cash journal (Cash-on-hand) at
        close, so forcing a receivable on a cash method leaves the cash receivable
        uncleared and books a spurious Cash Difference. Auto per tender type
        (grouped by method name), reuse-first, idempotent.
        """
        Method = self.env["pos.payment.method"].sudo()
        summary = {}
        for company in self._ri_pos_configs().mapped("company_id"):
            default_recv = company.account_default_pos_receivable_account_id
            acc_by_tender = {}
            for m in Method.search([("company_id", "=", company.id)]):
                if m.is_cash_count:
                    # Leave cash on the default POS receivable: POS' cash-control close
                    # settles it via the cash statement, and pointing a cash method at its
                    # own Cash-on-hand account instead makes the close hang in
                    # closing_control (move unbalanced by the cash amount).
                    continue
                if m.receivable_account_id and m.receivable_account_id != default_recv:
                    continue  # already separated
                tender = (m.name or "").strip() or "OTHER"
                if tender not in acc_by_tender:
                    acc_by_tender[tender] = self._x24_recv_account_for(company, tender)
                m.receivable_account_id = acc_by_tender[tender].id
                summary.setdefault(company.id, {})[tender] = summary.get(company.id, {}).get(tender, 0) + 1
        return summary

    # ------------------------------------------------------------------
    # Decouple mode — POS Suspense Clearing (sales posted before payment)
    # ------------------------------------------------------------------
    _X24_SUSPENSE_METHOD = "SUSPENSE"
    _X24_SUSPENSE_LABEL = "POS Suspense Clearing"

    def _x24_suspense_account(self, company):
        """Get/create the reconcilable POS Suspense Clearing account for a company.

        Sales in decouple mode book Dr Suspense / Cr Revenue+Tax at session close; the
        later X70D transfer credits it back per tender and reconciles. Cloned from the
        default POS receivable (same account_type, reconcile=True). Reuse-first by name.
        Mirrors _x24_recv_account_for."""
        Account = self.env["account.account"].sudo().with_company(company)
        acc = Account.search([("name", "=", self._X24_SUSPENSE_LABEL)], limit=1)
        if acc:
            return acc
        default_recv = company.account_default_pos_receivable_account_id
        base = default_recv.with_company(company).code if default_recv else "1106000001"
        return Account.create(
            {
                "name": self._X24_SUSPENSE_LABEL,
                "code": self._x24_next_free_account_code(company, base),
                "account_type": (default_recv.account_type if default_recv else "asset_receivable"),
                "reconcile": True,
                "company_ids": [(4, company.id)],
            }
        )

    def _x24_ensure_suspense_method(self):
        """Get/create a non-cash SUSPENSE pos.payment.method per company, backed by the
        POS Suspense Clearing account, attached to every config. Idempotent. Mirrors
        _x24_seed_payment_methods. Must NOT be is_cash_count (else the cash-control
        close misbehaves and _x24_ensure_method_gl_split would skip it).

        journal_id MUST stay empty: pos.payment.method.type is computed from the
        journal type, and a bank journal makes pos.session._create_bank_payment_moves
        emit an account.payment (Dr Outstanding Receipts / Cr Suspense) at close,
        draining the suspense the same second X24 fills it -- and dated today, not in
        the sales period. With no journal the method is 'pay_later': close only writes
        the suspense receivable line, which is exactly what X70D later transfers and
        reconciles."""
        Method = self.env["pos.payment.method"].sudo()
        by_company = {}
        for company in self._ri_pos_configs().mapped("company_id"):
            m = Method.search([("company_id", "=", company.id), ("name", "=", self._X24_SUSPENSE_METHOD)], limit=1)
            if not m:
                m = Method.create({"name": self._X24_SUSPENSE_METHOD, "company_id": company.id, "journal_id": False})
            elif m.journal_id:
                # Heal methods created before this was understood.
                m.journal_id = False
            # Non-cash + own reconcilable suspense receivable.
            susp = self._x24_suspense_account(company)
            if m.receivable_account_id != susp:
                m.receivable_account_id = susp.id
            for c in self._ri_pos_configs([("company_id", "=", company.id)]):
                if m not in c.payment_method_ids:
                    c.write({"payment_method_ids": [(4, m.id)]})
            by_company[company.id] = m
        return by_company

    def _aggregate_x24_by_sku_day(self, records):
        """Aggregate X24 rows to (store_code, closing_date, item_code) level.

        Multiple transactions (different transnum) on the same day for the same SKU
        are summed here. Returns a dict keyed by (store, date, sku) with aggregated
        qty, amount, unit price, and the source row_numbers for backtracking.
        """
        agg = {}
        for r in records:
            store = str(r.get("store_code") or "").strip()
            date = str(r.get("trans_date") or "").strip()
            sku = str(r.get("item_code") or r.get("sku") or "").strip()
            if not store or not sku:
                continue
            key = (store, date, sku)
            if key not in agg:
                agg[key] = {"qty": 0.0, "amount": 0.0, "unit_price": 0.0, "row_nums": []}
            entry = agg[key]
            qty = float(r.get("net_qty") or 0)
            amt = float(r.get("net_amount") or 0)
            entry["qty"] += qty
            entry["amount"] += amt
            if qty:
                entry["unit_price"] = amt / qty  # last non-zero row wins; close enough for staging
            entry["row_nums"].append(r.get("_row"))
        return agg

    def _group_x24(self, profile, file_b64):
        """Parse X24 and group rows into individual transactions (Phase-5 internal use).

        Returns {(store, date, register, transnum): [rows]} — one pos.order per key.
        For line-level aggregation use _aggregate_x24_by_sku_day() instead.
        """
        data = profile.read_records(file_b64)
        orders = defaultdict(list)
        for r in data["records"]:
            key = (r.get("store_code"), r.get("trans_date"), r.get("register"), r.get("transnum"))
            orders[key].append(r)
        return orders

    # ==================================================================
    # X70D — Tender detail (Phase 5, staged until X24 is enabled)
    # ==================================================================
    def _load_x70d(self, profile, file_b64, log):
        # Decouple mode: X70D drives the settlement — post the tender transfer
        # (Dr per-tender receivable / Cr Suspense) and reconcile the suspense lines.
        if self._x24_decouple_enabled():
            self._post_x70d_reconcile(profile, file_b64, log)
        else:
            self._stage_only(
                profile,
                file_b64,
                log,
                "X70D tender detail: staged (Phase-5 gated — depends on X24 enablement).",
            )

    def _post_x70d_reconcile(self, profile, file_b64, log):
        """Decouple-mode settlement: transfer POS Suspense Clearing to the real per-tender
        receivables and reconcile the suspense lines against the session-close debits.

        Prereq: X24 was posted in decouple mode (sales already sit on Suspense). Posts one
        RIREC ``account.move`` **per tender date** (Dr per-tender receivable / Cr Suspense)
        so the payment lands on the same day as the sale whose suspense it clears, then
        reconciles all open suspense lines. Both legs carry the store's Operating Unit
        analytic. Residual on the suspense account = sales whose payment never arrived
        (surfaced in the log note)."""
        ns = profile.namespace
        company = profile.company_id
        AML = self.env["account.move.line"].sudo()

        # Whole-file idempotency guard (mirror _post_x24 / _post_x31): refuse to re-run a
        # second X70D transfer over the same suspense balance — archive the prior first.
        prior = self.env["retail.import.log"].search(
            [("profile_id", "=", profile.id), ("state", "=", "imported"), ("id", "!=", log.id)], limit=1
        )
        if prior:
            raise UserError(
                _(
                    "X70D already reconciled (log #%s). Archive it before re-posting to "
                    "avoid double-crediting the suspense account."
                )
                % prior.id
            )

        # Stage the rows (audit trail) exactly like _stage_only, without the early return.
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)
        row_to_line = self._persist_lines(log, records) if records else {}
        if row_to_line:
            self.env["retail.import.line"].browse([ln.id for ln in row_to_line.values()]).write({"state": "skipped"})
        self.env.cr.commit()

        self._x24_automap_missing(ns, records)
        susp = self._x24_suspense_account(company)
        # (trading day) -> (tender, operating-unit id) -> amount. The store code resolves
        # to its pos.config (and thus its OU) through the same xid X24 posts against.
        by_day = defaultdict(lambda: defaultdict(float))
        cfg_cache, ou_cache = {}, {}
        x70d_keys = set()

        def resolve_config(store):
            if store not in cfg_cache:
                cid = self._xid_get(ns, self._safe_xid("posconfig_", store), "pos.config")
                cfg_cache[store] = self.env["pos.config"].with_context(active_test=False).browse(cid) if cid else False
            return cfg_cache[store]

        for r in records:
            tt = str(r.get("tender_type") or "").strip()
            if not tt:
                continue  # blank tender_type = file total, not a payment
            tt = self._X24_TENDER_FOLD.get(tt, tt)
            try:
                amt = float(profile._parse_amount(r.get("tender_amount")) or 0)
            except Exception:
                amt = 0.0
            store = str(r.get("store_code") or "").strip()
            if store not in ou_cache:
                ou_cache[store] = self._ri_config_ou(resolve_config(store))
            ou = ou_cache[store]
            d = profile._parse_date(r.get("trans_date"))
            by_day[d][(tt, ou.id if ou else False)] += amt
            x70d_keys.add(
                self._safe_xid(
                    "posorder_",
                    "%s_%s_%s_%s"
                    % (
                        store,
                        str(r.get("trans_date") or "").strip(),
                        str(r.get("register") or "").strip(),
                        str(r.get("transnum") or "").strip(),
                    ),
                )
            )

        posted, total = 0, 0.0
        if not any(round(a, 2) for day in by_day.values() for a in day.values()):
            log.records_skipped = len(records)
            log.error_message = "X70D: no postable tenders (empty/zero amounts)."
            self.env.cr.commit()
            return

        # One transfer entry per day: Dr each per-tender receivable / Cr Suspense. RIREC.
        Journal = self.env["account.journal"].sudo()
        journal = Journal.search([("code", "=", "RIREC"), ("company_id", "=", company.id)], limit=1)
        if not journal:
            journal = Journal.create(
                {
                    "name": "Retail Import Reconciliation",
                    "code": "RIREC",
                    "type": "general",
                    "company_id": company.id,
                }
            )
        AnalyticAccount = self.env["account.analytic.account"]
        fallback = datetime.now().date()
        for day in sorted(by_day, key=lambda d: d or fallback):
            gl_date = day or fallback
            by_tender = {k: round(a, 2) for k, a in by_day[day].items() if round(a, 2)}
            if not by_tender:
                continue
            line_ids = []
            # Credit the suspense per OU as well, so the entry balances *within* each
            # Operating Unit instead of leaving one store debited and another credited.
            susp_by_ou = defaultdict(float)
            for (tender, ou_id), amt in sorted(by_tender.items()):
                ou = AnalyticAccount.browse(ou_id) if ou_id else AnalyticAccount
                recv = self._x24_recv_account_for(company, tender)
                line_ids.append(
                    (
                        0,
                        0,
                        dict(
                            self._ri_ou_line_vals(ou),
                            account_id=recv.id,
                            debit=amt,
                            credit=0.0,
                            partner_id=False,
                            name=f"X70D settlement {tender}",
                        ),
                    )
                )
                susp_by_ou[ou_id] += amt
            for ou_id, amt in sorted(susp_by_ou.items(), key=lambda kv: kv[0] or 0):
                ou = AnalyticAccount.browse(ou_id) if ou_id else AnalyticAccount
                line_ids.append(
                    (
                        0,
                        0,
                        dict(
                            self._ri_ou_line_vals(ou),
                            account_id=susp.id,
                            debit=0.0,
                            credit=round(amt, 2),
                            partner_id=False,
                            name="X70D settlement (Suspense clearing)",
                        ),
                    )
                )
            move = (
                self.env["account.move"]
                .sudo()
                .create(
                    {
                        "move_type": "entry",
                        "journal_id": journal.id,
                        "date": gl_date,
                        "company_id": company.id,
                        "ref": f"X70D settlement transfer {gl_date} (log {log.id})",
                        "line_ids": line_ids,
                    }
                )
            )
            move.action_post()
            # Odoo bumps a past-dated entry to today on post; re-stamp within the same FY.
            self._ri_backdate_move(move, gl_date, "x70d")
            self._xid_set(ns, self._safe_xid("x70dreconcile_", f"{log.id}_{gl_date}"), "account.move", move.id)
            posted += 1
            total += sum(by_tender.values())
            _logger.info("x70d reconcile: move %s dated %s (%s tender lines)", move.name, gl_date, len(by_tender))
        total = round(total, 2)

        # Reconcile all open suspense lines (session-close debits + this credit). Group by
        # partner because reconcile requires a single partner per receivable batch; POS
        # close lines are normally partner-less, matching our partner_id=False credit.
        open_lines = AML.search(
            [
                ("account_id", "=", susp.id),
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
            ]
        )
        by_partner = defaultdict(lambda: AML.browse())
        for ln in open_lines:
            by_partner[ln.partner_id.id] += ln
        reconciled_groups = 0
        for grp in by_partner.values():
            if len(grp) > 1:
                try:
                    grp.reconcile()
                    reconciled_groups += 1
                except Exception as re:
                    _logger.warning("x70d reconcile: group reconcile skipped: %s", re)

        # Per-transaction "sales without payment" report: posted X24 orders whose txn key
        # has no matching X70D tender (both sides run through the same _safe_xid transform).
        posted_xids = set(
            self.env["ir.model.data"]
            .sudo()
            .search(
                [
                    ("module", "=", ns),
                    ("model", "=", "pos.order"),
                    ("name", "=like", "posorder_%"),
                ]
            )
            .mapped("name")
        )
        unpaid = posted_xids - x70d_keys
        # Re-read post-reconcile: fully-matched lines drop out; the sum of remaining
        # residuals is the true net open balance on the suspense account.
        still_open = AML.search(
            [
                ("account_id", "=", susp.id),
                ("company_id", "=", company.id),
                ("parent_state", "=", "posted"),
                ("reconciled", "=", False),
            ]
        )
        residual = round(sum(still_open.mapped("amount_residual")), 2)

        log.records_created = posted
        log.records_skipped = len(records)
        note = (
            f"X70D settlement: {posted} daily transfer move(s) posted "
            f"(Dr tender receivables {total:.2f} / Cr Suspense); "
            f"reconciled {reconciled_groups} suspense group(s); "
            f"suspense residual {residual:.2f}."
        )
        if unpaid:
            note += f" WARNING: {len(unpaid)} posted X24 sale(s) have no matching X70D tender (unpaid/unreconciled)."
        log.error_message = note
        self.env.cr.commit()
        _logger.info(
            "x70d reconcile: %s daily move(s), total %.2f, residual %.2f, %s unpaid",
            posted,
            total,
            residual,
            len(unpaid),
        )

    # ==================================================================
    # Staged / reference-only loaders (parse + count + keep attachment)
    # ==================================================================
    def _stage_only(self, profile, file_b64, log, note):
        data = profile.read_records(file_b64)
        records = data["records"]
        log.line_count = len(records)
        log.records_skipped = len(records)
        log.error_message = note
        if records:
            row_to_line = self._persist_lines(log, records)
            line_ids = [ln.id for ln in row_to_line.values()]
            if line_ids:
                self.env["retail.import.line"].browse(line_ids).write({"state": "skipped"})
            self.env.cr.commit()
        _logger.info("%s: staged %s rows (no model writes)", profile.file_type, len(records))

    def _load_x70t(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X70T settlement: staged for reconciliation (Phase 5 decision).")

    def _x31_post_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x31_post_enabled", "0") in (
            "1",
            "true",
            "True",
        )

    def _load_x31(self, profile, file_b64, log):
        """Post X31 promo discounts as a contra-revenue reclassification when
        ``retail_import.x31_post_enabled``; otherwise stage (legacy default)."""
        self._ri_assert_single_discount_source()
        data = profile.read_records(file_b64)
        records = self._ri_drop_footer_rows(data["records"])
        log.line_count = len(records)
        if not records:
            log.records_skipped = 0
            return
        row_to_line = self._persist_lines(log, records)
        if self._x31_post_enabled():
            self._post_x31(profile, records, log, row_to_line)
        else:
            log.records_skipped = len(records)
            log.error_message = "X31 discount journal: staged (set retail_import.x31_post_enabled=1 to post)."
            self.env["retail.import.line"].search([("log_id", "=", log.id)]).write({"state": "skipped"})
            self.env.cr.commit()

    def _ri_income_account(self, company, prod):
        """The product's sale income account (the Gross Sales-<category> account X24
        posts revenue to). Use the full POS/accounting resolution chain (product →
        category → company fallback) via ``_get_product_accounts`` — the bare property
        is often unset and relies on that fallback."""
        p = prod.with_company(company)
        acc = p.property_account_income_id or p.categ_id.property_account_income_categ_id
        if not acc:
            try:
                acc = p._get_product_accounts().get("income")
            except Exception:
                acc = False
        if not acc:
            # Same generic fallback POS uses at close for products with no income
            # account (Gross Sales - Others), so the reclass grosses up the account
            # X24 actually credited.
            Account = self.env["account.account"].sudo().with_company(company)
            acc = Account.search(
                [("account_type", "=", "income"), ("name", "ilike", "gross sales"), ("name", "ilike", "other")], limit=1
            ) or Account.search([("account_type", "=", "income"), ("name", "ilike", "gross sales")], limit=1)
        return acc

    # Keyword fallback for tenants whose product categories carry no explicit
    # discount/return account (the apparel brand COA is seeded by 34_coa_categ_tree.py).
    _RI_CONTRA_KIND = {
        "discount": "sales discount",
        "return": "sales return",
    }
    # Matched in order against the income account's name; first hit wins. "other" has to
    # be tried before the generic "misc" tail, otherwise an uncategorised product — whose
    # income resolves to "Gross Sales-Others" — reclassifies to Miscellaneous.
    _RI_CONTRA_KEYWORDS = (
        ("textile", "textile"),
        ("footwear", "footwear"),
        ("shoe", "footwear"),
        ("access", "accessor"),
        ("labor", "labor"),
        ("service", "labor"),
        ("merchandise", "merchandise"),
        ("wholesale", "wholesale"),
        ("e-commerce", "e-commerce"),
        ("clearance", "clearance"),
        ("distributor", "distributor"),
        ("other", "other"),
        ("misc", "misc"),
    )

    def _ri_contra_account_fallback(self, company, income_acct, kind):
        """Category contra account matched by name against a Gross Sales income account.

        Only reached when the product's category carries no explicit contra account.
        """
        Account = self.env["account.account"].sudo().with_company(company)
        label = self._RI_CONTRA_KIND[kind]
        nm = (income_acct.name or "").lower()
        for needle, kw in self._RI_CONTRA_KEYWORDS:
            if needle in nm:
                match = Account.search([("name", "ilike", label), ("name", "ilike", kw)], limit=1)
                if match:
                    return match
                break
        return Account.search([("name", "ilike", label), ("name", "ilike", "misc")], limit=1) or Account.search(
            [("name", "ilike", label)], limit=1
        )

    def _ri_category_account(self, company, prod, kind):
        """Resolve the ``Sales Discount-<cat>`` / ``Sales Return-<cat>`` contra account
        for a product, per its product category.

        ``kind`` is ``'discount'`` or ``'return'``. Resolution order:
        the category's explicit property (seeded from the COA bucket table), then its
        parent chain, then a name-keyword fallback against the income account so
        tenants without the seeded properties keep working.
        """
        field = "property_account_sales_%s_categ_id" % kind
        categ = prod.with_company(company).categ_id
        while categ:
            acc = categ[field]
            if acc:
                return acc
            categ = categ.parent_id
        income = self._ri_income_account(company, prod)
        if not income:
            return self.env["account.account"].browse()
        return self._ri_contra_account_fallback(company, income, kind)

    def _ri_adjustment_journal(self, company):
        """Dedicated journal so a period-dated reclass is not bumped to today by Odoo's
        sequence-date monotonicity against unrelated later moves in MISC."""
        Journal = self.env["account.journal"].sudo()
        journal = Journal.search([("code", "=", "RIADJ"), ("company_id", "=", company.id)], limit=1)
        if not journal:
            journal = Journal.create(
                {
                    "name": "Retail Import Adjustments",
                    "code": "RIADJ",
                    "type": "general",
                    "company_id": company.id,
                }
            )
        return journal

    def _ri_restamp_move_date(self, move, gl_date):
        """Force ``move`` and everything derived from it onto ``gl_date`` via SQL.

        Three tables, not one. ``account.analytic.line`` is the one that keeps being
        forgotten: ``account.move.line._create_analytic_lines`` copies the line's date at
        **post** time, so an entry posted today and backdated afterwards leaves its
        Analytic Items dated today — the per-OU P&L then reports the whole import in the
        wrong period even though the journal entry looks right.
        """
        self.env.cr.execute("UPDATE account_move SET date=%s WHERE id=%s", (gl_date, move.id))
        self.env.cr.execute("UPDATE account_move_line SET date=%s WHERE move_id=%s", (gl_date, move.id))
        self.env.cr.execute(
            "UPDATE account_analytic_line SET date=%s WHERE move_line_id IN "
            "(SELECT id FROM account_move_line WHERE move_id=%s)",
            (gl_date, move.id),
        )
        move.invalidate_recordset(["date"])
        move.line_ids.invalidate_recordset(["date"])
        move.line_ids.analytic_line_ids.invalidate_recordset(["date"])

    def _ri_backdate_move(self, move, gl_date, tag):
        """Odoo bumps a past-dated entry to today on post (sequence-date monotonicity).
        Re-stamp the move + lines + analytic items to the source period via SQL within the
        same fiscal year, aligning the sequence name's month so it stays consistent."""
        try:
            if gl_date and move.date and gl_date.year == move.date.year and gl_date != move.date:
                newname = move.name
                mtag = "/%02d/" % move.date.month
                if newname and mtag in newname:
                    cand = newname.replace(mtag, "/%02d/" % gl_date.month)
                    if not self.env["account.move"].search_count(
                        [("journal_id", "=", move.journal_id.id), ("name", "=", cand), ("id", "!=", move.id)]
                    ):
                        newname = cand
                self._ri_restamp_move_date(move, gl_date)
                self.env.cr.execute("UPDATE account_move SET name=%s WHERE id=%s", (newname, move.id))
                move.invalidate_recordset(["name"])
        except Exception as be:
            _logger.warning("%s POST: backdate skipped: %s", tag, be)

    def _ri_post_discount_reclass(self, company, journal, by_pair, gl_date, ref, tag):
        """Post the net-neutral contra-revenue reclass:
        per category, Dr ``Sales Discount-<cat>`` / Cr ``Gross Sales-<cat>``.

        ``by_pair`` maps a key to the discount amount **taken verbatim from the source
        file**. Both legs carry the identical figure, so the entry balances exactly and
        no rounding selisih is introduced.

        The key is ``(income_account_id, discount_account_id)`` optionally extended with
        ``(operating_unit_id, discount_code, discount_description)``: X24DN knows which
        store granted the discount and under which promo, so both legs can carry the OU
        analytic and name the promo. X31 has neither and passes the bare 2-tuple.

        Returns an empty recordset when every amount rounds to zero (a day whose
        discounts and their reversals cancel out): Odoo refuses to post a line-less move.
        """
        AnalyticAccount = self.env["account.analytic.account"]
        line_ids = []
        for key, amt in by_pair.items():
            amt = round(amt, 2)
            if not amt:
                continue
            inc_id, dacc_id = key[0], key[1]
            ou = AnalyticAccount.browse(key[2]) if len(key) > 2 and key[2] else AnalyticAccount
            label = " ".join(filter(None, key[3:5])) if len(key) > 3 else ""
            suffix = " — %s" % label if label else ""
            # Each line gets its own vals dict (the analytic distribution is a mutable
            # Json value, never share one across two create commands).
            line_ids.append(
                (
                    0,
                    0,
                    dict(
                        self._ri_ou_line_vals(ou),
                        account_id=dacc_id,
                        debit=amt,
                        credit=0.0,
                        name=("POS discount (%s)%s" % (tag.upper(), suffix))[:200],
                    ),
                )
            )
            line_ids.append(
                (
                    0,
                    0,
                    dict(
                        self._ri_ou_line_vals(ou),
                        account_id=inc_id,
                        debit=0.0,
                        credit=amt,
                        name=("POS discount gross-up (%s)%s" % (tag.upper(), suffix))[:200],
                    ),
                )
            )
        if not line_ids:
            return self.env["account.move"]
        move = self.env["account.move"].create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "date": gl_date,
                "ref": ref,
                "line_ids": line_ids,
            }
        )
        move.action_post()
        self._ri_backdate_move(move, gl_date, tag)
        return move

    # ------------------------------------------------------------------
    # X24DN discount: validation against X31, then per-category reclass
    # ------------------------------------------------------------------
    @staticmethod
    def _ri_discount_key(store, date, txn, code, waist, inseam):
        def norm(v):
            if v is None:
                return ""
            if isinstance(v, float) and v == int(v):
                v = int(v)
            return str(v).strip()

        return (norm(store), norm(date)[:10], norm(txn), norm(code), norm(waist), norm(inseam))

    def _ri_x31_discount_index(self, profile):
        """{key: discount_amount} from the most recent staged/imported X31 log."""
        x31_profile = self.env["retail.import.profile"].search(
            [("file_type", "=", "x31"), ("company_id", "=", profile.company_id.id)], limit=1
        )
        idx = defaultdict(float)
        if not x31_profile:
            return idx
        log = (
            self.env["retail.import.log"].sudo().search([("profile_id", "=", x31_profile.id)], order="id desc", limit=1)
        )
        if not log:
            return idx
        for ln in self.env["retail.import.line"].sudo().search([("log_id", "=", log.id)]):
            try:
                r = json.loads(ln.raw_data_json or "{}")
            except Exception:
                continue
            code = str(r.get("product_code") or "").strip()
            if not code:
                continue
            key = self._ri_discount_key(
                r.get("store_code"), r.get("trans_date"), r.get("transnum"), code, r.get("waist"), r.get("inseam")
            )
            try:
                idx[key] += float(r.get("discount_amount") or 0)
            except Exception:
                continue
        return idx

    def _ri_validate_discounts(self, profile, records):
        """Cross-check X24DN's NET DISCOUNT AMOUNT against the X31 Discount Journal.

        Returns a summary dict. X31 is expected to cover every discounted X24 line;
        anything else (an X24-only discount, a large per-key delta, X31 rows with no
        X24 counterpart) means the two extracts disagree and the reclass must not post
        blind. Per-key rounding of +/-1 rupiah is normal and absorbed by the tolerance.
        """
        icp = self.env["ir.config_parameter"].sudo()
        tol = float(icp.get_param("retail_import.discount_validation_tolerance", "1.0"))

        x31 = self._ri_x31_discount_index(profile)
        x24 = defaultdict(float)
        for r in records:
            disc = float(profile._parse_amount(r.get("net_discount")) or 0)
            if not disc:
                continue
            key = self._ri_discount_key(
                r.get("store_code"),
                r.get("trans_date"),
                r.get("transnum"),
                r.get("item_code"),
                r.get("waist"),
                r.get("inseam"),
            )
            x24[key] += disc

        matched = [k for k in x24 if k in x31]
        over_tol = [k for k in matched if abs(x24[k] - x31[k]) > tol]
        x24_only = [k for k in x24 if k not in x31]
        x31_only = [k for k in x31 if k not in x24 and x31[k]]
        return {
            "x24_total": sum(x24.values()),
            "x31_total": sum(x31.values()),
            "matched": len(matched),
            "over_tolerance": len(over_tol),
            "over_tolerance_delta": sum(x24[k] - x31[k] for k in over_tol),
            "x24_only": len(x24_only),
            "x24_only_total": sum(x24[k] for k in x24_only),
            "x31_only": len(x31_only),
            "x31_only_total": sum(x31[k] for k in x31_only),
            "tolerance": tol,
            "x31_available": bool(x31),
        }

    @staticmethod
    def _ri_format_discount_validation(v):
        if not v["x31_available"]:
            return "X31 not imported — X24DN discounts posted without cross-validation."
        return (
            "X24DN vs X31 discount validation: X24={x24_total:,.2f} X31={x31_total:,.2f} "
            "delta={delta:,.2f} | matched={matched} within +/-{tolerance:g} "
            "(over tolerance: {over_tolerance}, delta {over_tolerance_delta:,.2f}) | "
            "X24-only={x24_only} ({x24_only_total:,.2f}) | "
            "X31-only={x31_only} ({x31_only_total:,.2f})"
        ).format(delta=v["x24_total"] - v["x31_total"], **v)

    def _post_x24_discount_reclass(self, profile, records, posted_disc, log):
        """Validate X24DN's NET DISCOUNT AMOUNT against X31 and report it on the log.

        The reclass itself (Dr Sales Discount-<cat> / Cr Gross Sales-<cat>, verbatim) is
        **no longer a separate journal entry**. ``pos.session._create_account_move`` in
        ``custom_retail_import_pos`` appends the two legs to the store's own closing entry
        while it is still draft, so the discount sits next to the Gross Sales, VAT and
        Suspense lines of the same store on the same trading day, carrying that store's
        Operating Unit. A summary RIADJ move could never be tied back to a store.

        ``posted_disc`` holds one dict per posted discounted line — the product resolved
        by ``_post_x24``'s full EAN/composite matcher, the amount, the trading day, the
        store's pos.config and the source discount slots. It is used here only to report
        the discount that reached no income/discount account and was therefore dropped.
        """
        company = profile.company_id
        icp = self.env["ir.config_parameter"].sudo()

        verdict = self._ri_validate_discounts(profile, records)
        summary = self._ri_format_discount_validation(verdict)
        _logger.info("x24 discount reclass: %s", summary)

        max_delta = icp.get_param("retail_import.discount_validation_max_delta")
        if verdict["x31_available"] and max_delta:
            delta = abs(verdict["x24_total"] - verdict["x31_total"])
            if delta > float(max_delta):
                raise UserError(
                    _(
                        "X24DN/X31 discount totals differ by %(delta)s, above "
                        "retail_import.discount_validation_max_delta (%(max)s). "
                        "Reconcile the two extracts before posting.\n\n%(summary)s",
                        delta="{:,.2f}".format(delta),
                        max=max_delta,
                        summary=summary,
                    )
                )

        # The reclass lines were written into each session's closing entry. All that is
        # left is to warn about discount that never reached an account: the session hook
        # silently skips a product whose category resolves no income/discount pair.
        acct_cache = {}
        skipped = skipped_amount = 0.0
        for item in posted_disc:
            prod, disc = item["product"], item["amount"]
            if prod.id not in acct_cache:
                inc = self._ri_income_account(company, prod)
                dacc = self._ri_category_account(company, prod, "discount")
                acct_cache[prod.id] = bool(inc and dacc)
            if not acct_cache[prod.id]:
                skipped += 1
                skipped_amount += disc

        if skipped:
            summary += (
                "\nWARNING: %d discounted line(s) worth %s had no income/discount "
                "account and were NOT reclassified." % (skipped, "{:,.2f}".format(skipped_amount))
            )
            _logger.warning("x24 discount reclass: %d lines (%.2f) unmapped", skipped, skipped_amount)
        log.error_message = "\n".join(filter(None, [log.error_message, summary]))
        _logger.info(
            "x24 discount reclass: booked inside the POS closing entries (%s discounted lines, %s unmapped)",
            len(posted_disc),
            skipped,
        )

    def _post_x31(self, profile, records, log, row_to_line):
        """Post X31 promo discounts as a NET-NEUTRAL contra-revenue reclassification:
        per category, Dr ``Sales Discount-<cat>`` / Cr ``Gross Sales-<cat>`` for the
        source DISCOUNT AMOUNT, booked verbatim. This grosses revenue back up and books
        the discount as a category contra-revenue, so the P&L shows gross sales +
        discounts by category WITHOUT double-counting X24's net sales.

        Mutually exclusive with ``retail_import.x24_discount_reclass``: X24DN's
        NET DISCOUNT AMOUNT covers every X31 discount, so posting both would gross
        revenue up twice. One journal entry per import, dated at the latest discount
        date; idempotent via the whole-file guard + an ``x31entry_<log>`` xid."""
        ns = profile.namespace
        company = profile.company_id
        Product = self.env["product.product"]

        prior = self.env["retail.import.log"].search(
            [("profile_id", "=", profile.id), ("state", "=", "imported"), ("id", "!=", log.id)], limit=1
        )
        if prior:
            raise UserError(
                _("X31 already posted (log #%s). Archive it before re-posting to avoid duplicate reclass.") % prior.id
            )
        journal = self._ri_adjustment_journal(company)

        prod_cache, by_pair, dates, ok_rows, errors = {}, defaultdict(float), [], [], []
        skipped = 0
        for r in records:
            code = str(r.get("product_code") or "").strip()
            disc = float(profile._parse_amount(r.get("discount_amount")) or 0)
            if not disc or not code:
                skipped += 1
                continue
            if code not in prod_cache:
                prod_cache[code] = Product.search([("default_code", "=", code)], limit=1)
            prod = prod_cache[code]
            inc = self._ri_income_account(company, prod) if prod else False
            dacc = self._ri_category_account(company, prod, "discount") if prod else False
            if not prod or not inc or not dacc:
                skipped += 1
                rn = r.get("_row")
                if rn in row_to_line:
                    row_to_line[rn].write(
                        {
                            "state": "error",
                            "error_message": (f"X31: unpostable (product/income/discount acct) code={code}")[:250],
                        }
                    )
                errors.append((r.get("_row"), f"X31: no income/discount account for {code}"))
                continue
            # DISCOUNT AMOUNT is booked verbatim — never divided by (1 + tax rate).
            # Both legs of the reclass carry the same figure, so it balances exactly and
            # ``Sales Discount-<cat>`` ties to the source workbook to the rupiah.
            by_pair[(inc.id, dacc.id)] += disc
            raw_dt = str(r.get("trans_date") or "").strip()
            d = profile._parse_date(raw_dt)
            if not d and raw_dt:
                try:
                    d = datetime.strptime(raw_dt[:10], "%Y-%m-%d").date()
                except ValueError:
                    d = None
            if d:
                dates.append(d)
            ok_rows.append(r.get("_row"))

        if not by_pair:
            log.records_skipped = len(records)
            log.error_message = "X31: no postable discounts (no resolvable products / amounts)."
            self.env.cr.commit()
            return

        gl_date = max(dates) if dates else datetime.now().date()
        move = self._ri_post_discount_reclass(
            company,
            journal,
            by_pair,
            gl_date,
            ref=f"X31 promo discount reclass (log {log.id})",
            tag="x31",
        )
        if not move:
            log.records_skipped = len(records)
            log.error_message = "X31: every discount cancelled out to zero; nothing posted."
            self.env.cr.commit()
            return
        self._xid_set(ns, self._safe_xid("x31entry_", str(log.id)), "account.move", move.id)
        for rn in ok_rows:
            if rn in row_to_line:
                row_to_line[rn].write({"state": "ok", "target_model": "account.move", "target_res_id": move.id})
        log.records_created = 1
        log.records_skipped = skipped
        log.set_errors(errors)
        self.env.cr.commit()
        _logger.info(
            "x31 POST: reclass move %s posted (%s categories, %s rows, %s skipped)",
            move.name,
            len(by_pair),
            len(ok_rows),
            skipped,
        )

    def _load_x32p(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X32P stock movement: reference/audit only (not replayed; see plan).")

    def _load_store_master(self, profile, file_b64, log):
        self._stage_only(
            profile,
            file_b64,
            log,
            "Store Master: warehouse creation is handled by the Track A odoo-shell loader "
            "(header-wise store columns). This profile is for row-wise enrichment only.",
        )

    def _load_x70(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X70 tender breakdown: staged for settlement reconciliation.")

    def _load_x26(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X26 shipping: staged for transfer-order mapping.")

    def _load_x29(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X29 inventory adjustment: staged for stock adjustment mapping.")

    def _x48_post_enabled(self):
        return self.env["ir.config_parameter"].sudo().get_param("retail_import.x48_post_enabled", "0") in (
            "1",
            "true",
            "True",
        )

    def _load_x48(self, profile, file_b64, log):
        """Post X48 customer returns as refund pos.orders when
        ``retail_import.x48_post_enabled``; otherwise stage (legacy default)."""
        data = profile.read_records(file_b64)
        records = self._ri_drop_footer_rows(data["records"])
        log.line_count = len(records)
        if not records:
            log.records_skipped = 0
            return
        row_to_line = self._persist_lines(log, records)
        if self._x48_post_enabled():
            self._post_x48(profile, records, log, row_to_line)
        else:
            log.records_skipped = len(records)
            log.error_message = "X48 customer return: staged (set retail_import.x48_post_enabled=1 to post refunds)."
            self.env["retail.import.line"].search([("log_id", "=", log.id)]).write({"state": "skipped"})
            self.env.cr.commit()

    def _post_x48(self, profile, records, log, row_to_line):
        """Post X48 customer returns as refund (negative) pos.orders, paid by a negative
        cash refund. Mirrors _post_x24 (product resolution + lazy-create, price-included
        tax, one session/config, close + backdate, idempotency). X48 carries no tender,
        so the refund is booked to the store CASH method by default."""
        ns = profile.namespace
        Product = self.env["product.product"]
        Order = self.env["pos.order"]

        prior = self.env["retail.import.log"].search(
            [("profile_id", "=", profile.id), ("state", "=", "imported"), ("id", "!=", log.id)], limit=1
        )
        if prior:
            raise UserError(
                _("X48 already posted (log #%s). Archive it before re-posting to avoid duplicate refunds.") % prior.id
            )

        self._x24_ensure_method_gl_split()
        self._x24_automap_missing(ns, records)
        tax = self._x24_resolve_tax()
        orders = self._x24_group_orders(records)

        cfg_cache, sess_cache, prod_bc, prod_dc = {}, {}, {}, {}
        created = skipped = 0
        errors = []
        strict_products = self._x24_strict_product_enabled()

        def resolve_config(store):
            if store not in cfg_cache:
                cid = self._xid_get(ns, self._safe_xid("posconfig_", store), "pos.config")
                cfg_cache[store] = self.env["pos.config"].browse(cid) if cid else False
            return cfg_cache[store]

        def get_session(cfg):
            if cfg.id not in sess_cache:
                s = self.env["pos.session"].create({"config_id": cfg.id, "user_id": self.env.uid})
                # Decision B: imported refunds are financial-only, no stock move
                # (matches X24). Override the company-derived flag post-create.
                if s.update_stock_at_closing:
                    s.update_stock_at_closing = False
                if s.state != "opened":
                    try:
                        s.set_opening_control(0, None)
                    except Exception:
                        pass
                    if s.state != "opened":
                        s.write({"state": "opened"})
                sess_cache[cfg.id] = s
            return sess_cache[cfg.id]

        def resolve_product(r):
            ean = str(r.get("ean") or "").strip()
            code = str(r.get("product_code") or r.get("item_code") or "").strip()
            if ean:
                if ean not in prod_bc:
                    prod_bc[ean] = Product._resolve_barcode(ean)
                if prod_bc[ean]:
                    return prod_bc[ean]
            if code:
                if code not in prod_dc:
                    prod_dc[code] = Product.search([("default_code", "=", code)], limit=1)
                if prod_dc[code]:
                    return prod_dc[code]
            # Strict mode: refuse to invent a product for an unregistered SKU — return
            # empty so the caller parks the whole refund until X101 is synced.
            if strict_products:
                return Product.browse()
            key = code or ean
            if not key:
                return Product.browse()
            xid = self._safe_xid("x24prod_", key)
            pid = self._xid_get(ns, xid, "product.product")
            if pid:
                p = Product.browse(pid)
            else:
                tmpl_vals = {
                    "name": (str(r.get("item_description") or "").strip() or key)[:200],
                    "default_code": code or False,
                    "type": "consu",
                    "sale_ok": True,
                }
                # Only genuine non-merchandise gets a revenue bucket. With strict mode off
                # this same path also lazy-creates unmatched *garments*, and filing those
                # under "Others" would misstate their revenue, COGS and valuation.
                if self._x24_is_non_merch(r):
                    np_categ = self._x24_np_category(code)
                    if np_categ:
                        tmpl_vals["categ_id"] = np_categ.id
                tmpl = (
                    self.env["product.template"]
                    .with_context(tracking_disable=True, mail_create_nolog=True)
                    .create(tmpl_vals)
                )
                p = tmpl.product_variant_id
                if ean and not p.barcode and not Product.search_count([("barcode", "=", ean)]):
                    try:
                        p.barcode = ean
                    except Exception:
                        pass
                self._xid_set(ns, xid, "product.product", p.id)
            if code:
                prod_dc[code] = p
            if ean:
                prod_bc[ean] = p
            return p

        def fail_rows(rows, msg):
            for r in rows:
                rn = r.get("_row")
                if rn in row_to_line:
                    row_to_line[rn].write({"state": "error", "error_message": msg[:250]})
            errors.append((rows[0].get("_row"), msg))

        # Preflight: an archived store accepts orders but its session can never post.
        self._ri_assert_stores_postable({cfg for cfg in (resolve_config(k[0]) for k in orders) if cfg})

        rate_val = (tax.amount / 100.0) if tax else 0.0
        # Same per-day batching as _post_x24: a return posts to a closing entry dated on
        # the day it was returned, not on the file's last date.
        for _batch_date, batch_orders in self._x24_day_batches(profile, orders):
            sess_cache = {}
            for key, rows in batch_orders.items():
                store, date, reg, txn = key
                oxid = self._safe_xid("posreturn_", f"{store}_{date}_{reg}_{txn}")
                if self._xid_get(ns, oxid, "pos.order"):
                    continue
                cfg = resolve_config(store)
                if not cfg:
                    fail_rows(rows, f"store {store}: no pos.config (map xid posconfig_{store})")
                    skipped += len(rows)
                    continue
                cash = cfg.payment_method_ids.filtered(lambda m: m.is_cash_count)[:1]
                if not cash:
                    fail_rows(rows, f"store {store}: no cash method for refund")
                    skipped += len(rows)
                    continue

                line_cmds, order_net, order_incl, missing = [], 0.0, 0.0, []
                for r in rows:
                    prod = resolve_product(r)
                    if not prod:
                        missing.append(str(r.get("product_code") or r.get("item_code") or r.get("ean") or "?"))
                        rn = r.get("_row")
                        if rn in row_to_line:
                            row_to_line[rn].write(
                                {
                                    "state": "error",
                                    "error_message": f"not in X101 master: ean={r.get('ean')!r} code={r.get('product_code')!r}"[
                                        :250
                                    ],
                                }
                            )
                        continue
                    qty = float(profile._parse_amount(r.get("net_qty")))  # negative
                    incl = float(profile._parse_amount(r.get("total_amount")))  # negative (refund)
                    # X48 carries NET SOLD AMOUNT / TAX AMOUNT (both negative) but no TAX RATE
                    # column, so a line is taxed iff it has a tax amount. As with X24, the
                    # file's own figures win over a recomputation.
                    src_net = float(profile._parse_amount(r.get("net_amount")) or 0)
                    src_tax = float(profile._parse_amount(r.get("tax_amount")) or 0)
                    if not (src_net or src_tax):
                        src_net, src_tax = incl / (1.0 + rate_val), incl - incl / (1.0 + rate_val)
                    taxed = bool(tax) and bool(src_tax)
                    gl_net = src_net
                    unit = incl / qty if qty else incl  # positive unit price
                    line_cmds.append(
                        (
                            0,
                            0,
                            dict(
                                {
                                    "product_id": prod.id,
                                    "qty": qty,
                                    "price_unit": unit,
                                    "discount": 0.0,
                                    "tax_ids": [(6, 0, [tax.id] if taxed else [])],
                                    "price_subtotal": gl_net,
                                    "price_subtotal_incl": incl,
                                },
                                **self._ri_src_line_vals(net=src_net, tax=src_tax, is_return=True),
                            ),
                        )
                    )
                    order_net += gl_net
                    order_incl += incl

                # Strict mode / any unresolved line: park the WHOLE refund rather than
                # posting a partial return.
                if missing:
                    sku_list = ", ".join(sorted(set(missing))[:10])
                    fail_rows(
                        rows,
                        f"store {store} txn {txn}: produk belum teregister di master X101 ({sku_list}) — sync X101 dulu",
                    )
                    skipped += len(rows)
                    continue
                if not line_cmds:
                    fail_rows(rows, f"store {store} txn {txn}: no resolvable product")
                    skipped += len(rows)
                    continue

                d = profile._parse_date(date)
                dt = datetime(d.year, d.month, d.day, 12, 0, 0) if d else datetime.now()
                sess = get_session(cfg)
                try:
                    with self.env.cr.savepoint():
                        order = Order.create(
                            {
                                "session_id": sess.id,
                                "company_id": cfg.company_id.id,
                                "pricelist_id": cfg.pricelist_id.id or False,
                                "date_order": dt,
                                "pos_reference": f"RET-{store}-{reg}-{txn}",
                                "lines": line_cmds,
                                "amount_tax": order_incl - order_net,
                                "amount_total": order_incl,
                                "amount_paid": 0.0,
                                "amount_return": 0.0,
                            }
                        )
                        order.add_payment(
                            {
                                "pos_order_id": order.id,
                                "payment_method_id": cash.id,
                                "amount": order_incl,
                                "payment_date": dt,
                            }
                        )
                        order.invalidate_recordset(["payment_ids", "amount_paid"])
                        order.amount_paid = sum(order.payment_ids.mapped("amount"))
                        if order.amount_total and abs(order.amount_paid - order.amount_total) <= self._X24_BALANCE_TOL:
                            order.action_pos_order_paid()
                        self._xid_set(ns, oxid, "pos.order", order.id)
                except Exception as e:
                    fail_rows(rows, f"store {store} txn {txn}: refund post failed: {e}")
                    skipped += len(rows)
                    continue
                for r in rows:
                    rn = r.get("_row")
                    if rn in row_to_line:
                        row_to_line[rn].write(
                            {
                                "state": "ok",
                                "target_model": "pos.order",
                                "target_res_id": order.id,
                                "aggregate_key": f"RET|{store}|{date}|{txn}",
                            }
                        )
                created += 1
                if created % 200 == 0:
                    self.env.cr.commit()

            self._pos_close_and_backdate(sess_cache.values(), errors, "x48")

        self.env.cr.commit()
        log.records_created = created
        log.records_skipped = skipped
        log.set_errors(errors)
        _logger.info("x48 POST: %s refund orders created, %s rows skipped, %s errors", created, skipped, len(errors))

    def _load_x53(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X53 RTV: staged for vendor return mapping.")

    def _load_x53_ebr(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X53-EBR RTV (alternate format): staged for vendor return mapping.")

    def _load_x21(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X21 payment summary: staged for payment reconciliation.")

    def _load_x25n(self, profile, file_b64, log):
        self._stage_only(profile, file_b64, log, "X25N receiving: staged for PO receipt mapping.")
