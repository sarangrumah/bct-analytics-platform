# -*- coding: utf-8 -*-
"""Inbound staging for the MDM product-master feed.

The controller does no business logic: it validates the shape of the request, writes
it here, enqueues a job and answers. Three reasons the payload is staged rather than
applied inline:

* **The sender must not wait.** Upserting one the apparel brand SKU can mean creating a template
  and generating its whole Size x Inseam variant matrix. A synchronous timeout would
  make Mulesoft retry a request that actually succeeded.
* **Replay.** ``custom.adapter.call.log`` only keeps ``sha256(body)``, so the payload
  itself would be gone. Keeping it means a mapping bug is fixed by editing code and
  pressing Replay, never by asking the apparel brand to retransmit.
* **Ordering.** One job at a time, plus an advisory lock, keeps two updates to the
  same SKU -- or a concurrent X101 file import -- from racing on the same external IDs.

The actual product writes go through ``retail.import.executor._x101_upsert_items``,
the same seam the XLSX import uses, so both routes produce identical records.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import uuid

from odoo import SUPERUSER_ID, _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5

#: Namespace for the external IDs. Matches the X101 import profile's namespace so the
#: API and the file address the very same templates and categories.
DEFAULT_NAMESPACE = "retail"


def _yes(value):
    """the apparel brand sends Yes/No strings, not JSON booleans."""
    if value is None or value == "":
        return None
    return str(value).strip().lower() in ("yes", "y", "true", "1")


def _num(value):
    """Parse a numeric payload field, refusing values that are not real numbers.

    ``float()`` cheerfully accepts "nan", "inf", "-inf" and anything that overflows
    to infinity ("1e400"). These fields become prices, costs and weights: a NaN price
    raises nothing, stores happily, and then silently poisons every comparison and
    sum downstream -- NaN compares false against everything, including itself.

    Non-finite values are therefore treated like any other unparseable input: 0.0,
    which the X101 data-quality check already surfaces as "invalid/zero price" rather
    than letting it through unnoticed.
    """
    if value in (None, "", False):
        return 0.0
    try:
        parsed = float(str(value).strip())
    except (TypeError, ValueError):
        return 0.0
    return parsed if math.isfinite(parsed) else 0.0


def _text(value, limit=None):
    out = "" if value is None else str(value).strip()
    return out[:limit] if limit else out


def canonical_hash(item):
    """Stable hash of one item, insensitive to key order and whitespace."""
    normalised = {str(k): ("" if v is None else str(v).strip()) for k, v in sorted(item.items())}
    return hashlib.sha256(json.dumps(normalised, sort_keys=True).encode("utf-8")).hexdigest()


class RetailMdmRequest(models.Model):
    _name = "retail.mdm.request"
    _description = "MDM Product-Master Inbound Request"
    _order = "id desc"
    _inherit = ["mail.thread"]

    name = fields.Char(compute="_compute_name", store=True)
    request_id = fields.Char(required=True, index=True, readonly=True, copy=False)
    dedupe_key = fields.Char(
        required=True,
        index=True,
        readonly=True,
        copy=False,
        help="X-Request-Id when the sender supplies one, else sha256 of the raw body.",
    )
    payload = fields.Json(readonly=True, help="The request exactly as received. Purged by the GC cron.")
    item_count = fields.Integer(readonly=True)
    source_ip = fields.Char(readonly=True)
    received_at = fields.Datetime(default=fields.Datetime.now, readonly=True, index=True)
    job_uuid = fields.Char(index=True, readonly=True)
    processed_at = fields.Datetime(readonly=True)

    state = fields.Selection(
        [
            ("received", "Received"),
            ("queued", "Queued"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("partial", "Partial"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="received",
        required=True,
        index=True,
        tracking=True,
    )
    ok_count = fields.Integer(readonly=True)
    dup_count = fields.Integer(readonly=True)
    error_count = fields.Integer(readonly=True)
    review_count = fields.Integer(readonly=True)
    attempt_count = fields.Integer(default=0, readonly=True)
    last_error = fields.Char(readonly=True)
    dry_run = fields.Boolean(readonly=True, help="Shadow mode: validated and mapped, but no product writes.")

    company_id = fields.Many2one("res.company", default=lambda s: s.env.company, required=True, index=True)
    item_ids = fields.One2many("retail.mdm.item", "request_id_fk", string="Items")

    # Odoo 19 silently ignores _sql_constraints; models.Constraint is the live form.
    # The dedupe constraint is the authoritative idempotency guarantee -- not the
    # in-process check in ingest(), which only avoids the exception in the common case.
    _request_id_uniq = models.Constraint("unique(request_id)", "MDM request id must be unique.")
    _dedupe_uniq = models.Constraint("unique(company_id, dedupe_key)", "This MDM message was already received.")

    @api.depends("request_id", "item_count")
    def _compute_name(self):
        for rec in self:
            rec.name = f"MDM/{rec.request_id or '?'}"

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------
    @api.model
    def _mdm_company(self):
        """The company this feed belongs to.

        The controller runs ``auth="none"``, so there is no user and ``env.company``
        is empty -- the company cannot be inferred from the request. It is taken from
        the X101 import profile instead, which is the same record the external-ID
        namespace comes from, so the API and the file import always agree on the
        tenant. ``retail_import.mdm_company_id`` overrides it if a database ever needs
        that; otherwise the first company is the last resort.
        """
        icp = self.env["ir.config_parameter"].sudo()
        forced = icp.get_param("retail_import.mdm_company_id", "0")
        Company = self.env["res.company"].sudo()
        if str(forced).isdigit() and int(forced):
            company = Company.browse(int(forced))
            if company.exists():
                return company
        profile = self.env["retail.import.profile"].sudo().search([("file_type", "=", "x101")], order="id", limit=1)
        if profile.company_id:
            return profile.company_id
        return Company.search([], order="id", limit=1)

    @api.model
    def ingest(self, items, dedupe_key, source_ip=None, raw=None):
        """Stage a validated batch and enqueue it, or return the existing request.

        Returns ``(request, duplicate)``. The create runs inside a savepoint so a
        concurrent request losing the unique-constraint race is answered as a
        duplicate rather than a 500.
        """
        company = self._mdm_company()
        # with_user, not just sudo(): the controller runs auth="none", so there is no
        # uid at all. sudo() raises privileges but leaves uid unset, and core stamps
        # create_uid / product.value.user_id from it -- the latter is NOT NULL, so
        # creating a variant aborts the transaction. Pin OdooBot as the acting user.
        self = self.with_user(SUPERUSER_ID).with_company(company)
        existing = self.sudo().search([("company_id", "=", company.id), ("dedupe_key", "=", dedupe_key)], limit=1)
        if existing:
            return existing, True

        vals = {
            "request_id": uuid.uuid4().hex,
            "dedupe_key": dedupe_key,
            # Set explicitly, never left to the field default: with auth="none" there
            # is no user for env.company to resolve from.
            "company_id": company.id,
            "payload": raw if raw is not None else items,
            "item_count": len(items),
            "source_ip": source_ip or False,
            "dry_run": self._mdm_flag("mdm_dry_run"),
            "item_ids": [
                (
                    0,
                    0,
                    {
                        "sequence": index,
                        "sku_code": _text(item.get("skuCode"), 64) or False,
                        "prod_sku": _text(item.get("udf2"), 64) or False,
                        "template_code": _text(item.get("udf1"), 64) or False,
                        "ean": _text(item.get("upc_ean"), 64) or False,
                        "payload": item,
                        "content_hash": canonical_hash(item),
                    },
                )
                for index, item in enumerate(items)
            ],
        }
        try:
            with self.env.cr.savepoint():
                record = self.sudo().create(vals)
        except Exception:
            # Lost the race against a concurrent identical POST.
            record = self.sudo().search([("company_id", "=", company.id), ("dedupe_key", "=", dedupe_key)], limit=1)
            if not record:
                raise
            return record, True

        record.enqueue()
        return record, False

    def _mdm_flag(self, name, default="0"):
        return self.env["ir.config_parameter"].sudo().get_param(f"retail_import.{name}", default) in (
            "1",
            "true",
            "True",
        )

    def enqueue(self):
        # Opt-in synchronous mode: process in the request instead of deferring to a
        # worker. Meant for a test or demo database -- notably one that a *different*
        # Odoo instance's job runner can also see, where the two runners fight over
        # the same queue -- and for tenants whose volume does not justify a worker.
        # The caller then waits for the upsert, so leave it off for the real feed.
        if self._mdm_flag("mdm_sync_processing"):
            for rec in self:
                rec.write({"state": "queued"})
                rec._job_process()
            return True

        for rec in self:
            # Mark queued *before* dispatching. queue_job runs the job inline when
            # ``queue_job__no_delay`` is set (tests, and any caller that wants a
            # synchronous run), so writing the state afterwards would overwrite the
            # outcome the job just recorded.
            rec.write({"state": "queued"})
            runner = rec.with_delay(
                channel="root.retail_import.mdm",
                description=f"MDM product ingest {rec.request_id}",
            )
            job = runner._job_process()
            uuid_ = getattr(job, "uuid", False)
            if uuid_:
                rec.job_uuid = uuid_
        return True

    # ------------------------------------------------------------------
    # Processing
    # ------------------------------------------------------------------
    def _job_process(self):
        self.ensure_one()
        if self.state in ("done", "cancelled"):
            return True
        self.write({"state": "processing", "attempt_count": self.attempt_count + 1})
        try:
            # The upsert itself takes the shared X101 advisory lock, so a concurrently
            # running file import cannot interleave with this one on the same
            # category/template external IDs.
            # with_company: the job's env has no company either, and the processor
            # resolves the namespace and the category crosswalk per company.
            self.env["retail.mdm.processor"].with_company(self.company_id)._process(self)
        except Exception as exc:  # noqa: BLE001
            _logger.exception("MDM request %s failed", self.request_id)
            self.write({"state": "failed", "last_error": str(exc)[:255]})
            raise
        return True

    def _rollup(self):
        """Recompute the per-state counters and the request state from its items."""
        self.ensure_one()
        states = self.item_ids.mapped("state")
        self.write(
            {
                "ok_count": states.count("done"),
                "dup_count": states.count("duplicate") + states.count("skipped"),
                "error_count": states.count("error") + states.count("conflict"),
                "review_count": states.count("needs_review"),
                "processed_at": fields.Datetime.now(),
                "state": "partial" if any(s in ("error", "conflict", "needs_review") for s in states) else "done",
            }
        )

    # ------------------------------------------------------------------
    # Ops actions
    # ------------------------------------------------------------------
    def action_replay(self):
        """Reset the unsuccessful items and run again."""
        for rec in self:
            if rec.state == "cancelled":
                raise UserError(_("Request %s was cancelled.") % rec.request_id)
            if not rec.payload and not rec.item_ids:
                raise UserError(_("Request %s no longer has its payload (purged).") % rec.request_id)
            rec.item_ids.filtered(lambda i: i.state != "done").write({"state": "pending", "error": False})
            rec.write({"state": "received", "last_error": False})
        self.enqueue()
        return True

    def action_cancel(self):
        self.filtered(lambda r: r.state != "done").write({"state": "cancelled"})
        return True

    @api.model
    def _cron_retry_failed(self):
        """Re-drive failures that have attempts left, oldest first."""
        stuck = self.search(
            [("state", "=", "failed"), ("attempt_count", "<", MAX_ATTEMPTS)],
            order="received_at",
            limit=100,
        )
        for rec in stuck:
            try:
                rec.enqueue()
            except Exception:  # noqa: BLE001
                _logger.exception("MDM retry enqueue failed for %s", rec.request_id)
        return len(stuck)

    @api.model
    def _cron_gc(self):
        """Drop the stored payload of old finished requests, keeping the metadata."""
        days = self.env["ir.config_parameter"].sudo().get_param("retail_import.mdm_payload_retention_days", "90")
        try:
            days = max(1, int(days))
        except (TypeError, ValueError):
            days = 90
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), days=days)
        old = self.search(
            [("state", "in", ("done", "cancelled")), ("received_at", "<", cutoff), ("payload", "!=", False)],
            limit=1000,
        )
        old.write({"payload": False})
        old.item_ids.write({"payload": False})
        return len(old)


class RetailMdmItem(models.Model):
    _name = "retail.mdm.item"
    _description = "MDM Product-Master Item"
    _order = "request_id_fk desc, sequence"

    request_id_fk = fields.Many2one(
        "retail.mdm.request", string="Request", required=True, ondelete="cascade", index=True
    )
    sequence = fields.Integer(default=0)
    sku_code = fields.Char(index=True, help="MDM skuCode, as sent.")
    prod_sku = fields.Char(index=True, help="udf2 -- the value written to product.product.default_code.")
    template_code = fields.Char(index=True, help="udf1 -- the mainline code on product.template.")
    ean = fields.Char(index=True)
    payload = fields.Json()
    content_hash = fields.Char(index=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("done", "Done"),
            ("duplicate", "Duplicate (unchanged)"),
            ("skipped", "Skipped"),
            ("needs_review", "Needs Review"),
            ("conflict", "Conflict"),
            ("error", "Error"),
        ],
        default="pending",
        index=True,
    )
    error = fields.Char()
    product_id = fields.Many2one("product.product", ondelete="set null")
    template_id = fields.Many2one("product.template", ondelete="set null")
    processed_at = fields.Datetime()

    # Intra-batch dedup only. Cross-message idempotency is content-hash based, because
    # a unique (sku_code, content_hash) would wrongly reject a legitimate revert.
    _item_sku_uniq = models.Constraint(
        "unique(request_id_fk, sku_code)", "The same skuCode appears twice in one MDM message."
    )
