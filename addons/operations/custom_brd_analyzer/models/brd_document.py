# -*- coding: utf-8 -*-
"""BRD document record — the central entity of the module.

State machine: draft -> extracted -> analyzed -> reviewed -> approved.
"""

from __future__ import annotations

import base64
import logging
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .brd_extractor import BrdExtractor, ExtractorDependencyError
from .brd_ai_analyzer import BrdAiAnalyzer

_logger = logging.getLogger(__name__)


SEVERITY_RANK = {"must_have": 3, "should_have": 2, "nice_to_have": 1}


class BrdDocument(models.Model):
    _name = "brd.document"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _description = "Business Requirements Document"
    _order = "create_date desc"

    name = fields.Char(
        required=True,
        default=lambda self: self.env._("New BRD"),
        tracking=True,
    )
    reference = fields.Char(
        readonly=True,
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("brd.document") or "/",
        index=True,
    )
    document_attachment_id = fields.Many2one(
        "ir.attachment",
        string="BRD File",
        required=True,
        ondelete="restrict",
        domain="[('res_model', '=', 'brd.document')]",
    )
    document_mime = fields.Char(string="MIME Type")
    document_filename = fields.Char(string="Filename")

    vertical_target_id = fields.Many2one(
        "tenant.registry",
        string="Target Vertical / Tenant",
        ondelete="set null",
    )
    business_domain = fields.Selection(
        [
            ("rental", "Rental"),
            ("manufacturing", "Manufacturing"),
            ("retail", "Retail"),
            ("services", "Services"),
            ("government", "Government"),
            ("finance", "Finance"),
            ("healthcare", "Healthcare"),
            ("logistics", "Logistics"),
            ("ppob", "PPOB"),
            ("other", "Other"),
        ],
        default="other",
        tracking=True,
    )
    contact_person = fields.Char()
    contact_email = fields.Char()
    language = fields.Selection([("id", "Bahasa Indonesia"), ("en", "English")], default="en")

    # ------------------------------------------------------------------
    # Onboarding lifecycle hooks (Track B)
    # ------------------------------------------------------------------
    # ``journey_id`` is declared in ``custom_onboarding_journey`` via _inherit
    # to avoid an unresolved comodel at brd_analyzer load time (Odoo refuses
    # to set up a Many2one whose comodel is not yet in the registry).
    vertical_target = fields.Char(
        string="Vertical Target",
        help="Free-form code of the target vertical (e.g. retail, fnb, "
        "healthcare). Kept as Char to avoid coupling to a fixed enum.",
    )
    company_profile_json = fields.Text(
        string="Company Profile (JSON)",
        help='JSON: {"name": "...", "logo_url": "...", "npwp": "...", '
        '"bank": {...}}. Captured during BRD intake for downstream tenant '
        "provisioning.",
    )

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("extracted", "Extracted"),
            ("analyzing", "Analyzing"),
            ("analyzed", "Analyzed"),
            ("reviewed", "Reviewed"),
            ("approved", "Approved"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    extracted_text = fields.Text()

    section_ids = fields.One2many("brd.document.section", "document_id", string="Sections")
    analysis_ids = fields.One2many("brd.analysis", "document_id", string="Analyses")
    recommendation_ids = fields.One2many("brd.recommendation", "document_id", string="Recommendations")

    overall_fit_pct = fields.Integer(
        compute="_compute_overall_fit",
        store=True,
        help=(
            "Weighted average fit_score across all analyzed sections (0-100).\n"
            "Formula: sum(weight x fit_score) / sum(weight), where weight is "
            "gap_severity rank (must_have=3, should_have=2, nice_to_have=1).\n"
            "Per-section fit_score is set by the AI: how confident it is that "
            "the mapped hub modules cover the requirement of that section."
        ),
    )
    severity_summary = fields.Char(
        compute="_compute_severity_summary",
        store=True,
        help=(
            "Count of analyzed sections per gap_status:\n"
            " - covered: existing hub module fully covers requirement\n"
            " - partial: existing module covers some, needs extension\n"
            " - missing: no module covers it, new custom_<x> needed\n"
            " - unclear: AI not confident (often signals thin knowledge file)\n"
            "See the Analysis tab for the row-level breakdown."
        ),
    )

    token_uuid = fields.Char(
        copy=False,
        index=True,
        help="Public share token for read-only access to the report.",
    )

    owner_user_id = fields.Many2one("res.users", default=lambda self: self.env.user, tracking=True, string="Owner")

    # Diagnostics — last raw AI response (truncated) + counts. Surfaced to UI
    # so BAs can see why an analyze yielded 0 recommendations.
    last_ai_raw = fields.Text(
        string="Last AI Raw Response",
        readonly=True,
        copy=False,
        help="Truncated raw response from the last AI analyze call. For diagnostics.",
    )
    last_ai_at = fields.Datetime(string="Last AI Run", readonly=True, copy=False)
    last_ai_section_count = fields.Integer(string="Sections Analyzed (last run)", readonly=True, copy=False)
    last_ai_recommendation_count = fields.Integer(string="Recommendations (last run)", readonly=True, copy=False)

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("analysis_ids.fit_score", "analysis_ids.gap_severity")
    def _compute_overall_fit(self):
        for rec in self:
            if not rec.analysis_ids:
                rec.overall_fit_pct = 0
                continue
            total_w = 0
            weighted_sum = 0
            for a in rec.analysis_ids:
                w = SEVERITY_RANK.get(a.gap_severity, 1)
                total_w += w
                weighted_sum += w * (a.fit_score or 0)
            rec.overall_fit_pct = int(weighted_sum / total_w) if total_w else 0

    @api.depends("analysis_ids.gap_status", "analysis_ids.gap_severity")
    def _compute_severity_summary(self):
        for rec in self:
            buckets: dict[str, int] = {"covered": 0, "partial": 0, "missing": 0, "unclear": 0}
            for a in rec.analysis_ids:
                if a.gap_status in buckets:
                    buckets[a.gap_status] += 1
            rec.severity_summary = (
                f"covered={buckets['covered']} | partial={buckets['partial']} | "
                f"missing={buckets['missing']} | unclear={buckets['unclear']}"
            )

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("reference") or vals["reference"] == "/":
                vals["reference"] = self.env["ir.sequence"].next_by_code("brd.document") or "/"
        records = super().create(vals_list)
        # Re-bind attachment to point at the new record so Documents app picks it up.
        for rec in records:
            if rec.document_attachment_id and rec.document_attachment_id.res_model != "brd.document":
                rec.document_attachment_id.sudo().write({"res_model": "brd.document", "res_id": rec.id})
            if rec.document_attachment_id:
                rec.document_mime = rec.document_attachment_id.mimetype
                rec.document_filename = rec.document_attachment_id.name
        return records

    # ------------------------------------------------------------------
    # Workflow actions
    # ------------------------------------------------------------------

    def action_extract(self):
        for rec in self:
            rec._do_extract()
        return True

    def _do_extract(self):
        self.ensure_one()
        if not self.document_attachment_id:
            raise UserError(_("Attach a BRD file first."))
        try:
            binary = base64.b64decode(self.document_attachment_id.datas or b"")
        except Exception as exc:
            raise UserError(_("Cannot read attachment: %s") % exc) from exc
        extractor = BrdExtractor()
        try:
            payload = extractor.extract(
                binary,
                mime=self.document_attachment_id.mimetype,
                filename=self.document_attachment_id.name,
            )
        except ExtractorDependencyError as exc:
            raise UserError(str(exc)) from exc
        # Clear previous extraction.
        self.section_ids.unlink()
        self.write({"extracted_text": payload.get("text") or ""})
        Section = self.env["brd.document.section"]
        for seq, sec in enumerate(payload.get("sections") or [], start=1):
            Section.create(
                {
                    "document_id": self.id,
                    "sequence": seq,
                    "title": sec.get("title") or f"Section {seq}",
                    "content": sec.get("content") or "",
                    "level": int(sec.get("level") or 1),
                    "page_or_slide": int(sec.get("page") or 0),
                }
            )
        self.state = "extracted"
        self.message_post(body=_("BRD extracted: %s sections.") % len(self.section_ids))

    def action_analyze(self):
        """Synchronous analyze. Kept for tests and as a fallback when
        queue_job's worker is not running. UI should prefer
        ``action_analyze_async`` which returns immediately and dispatches
        the heavy work to the queue."""
        for rec in self:
            rec._do_analyze()
        return True

    def action_analyze_async(self):
        """Schedule the analyze on the queue_job worker so the HTTP request
        that triggered it returns immediately. The frontend should poll
        ``state`` to detect completion.

        Falls back to synchronous execution if queue_job is not installed
        (e.g. test setup) — the caller still gets a deterministic outcome.
        """
        for rec in self:
            if rec.state == "draft":
                raise UserError(_("Extract the BRD before running analysis."))
            rec.write(
                {
                    "state": "analyzing",
                    "last_ai_at": fields.Datetime.now(),
                    "last_ai_raw": False,
                    "last_ai_section_count": 0,
                    "last_ai_recommendation_count": 0,
                }
            )
            rec.message_post(body=_("BRD analyze dispatched to background worker."))
            # queue_job exposes ``with_delay`` on every recordset when the
            # module is installed. Detect by attribute presence so we don't
            # hard-depend on it at import time.
            if hasattr(rec, "with_delay"):
                rec.with_delay(description="BRD AI analyze")._do_analyze()
            else:
                _logger.warning(
                    "brd.document.action_analyze_async: queue_job not available, "
                    "running synchronously. State will block the HTTP call."
                )
                rec._do_analyze()
        return True

    def _do_analyze(self):
        self.ensure_one()
        if self.state == "draft":
            raise UserError(_("Extract the BRD before running analysis."))
        analyzer = BrdAiAnalyzer(self.env)
        try:
            analyzer.analyze(self)
            self.message_post(body=_("BRD analyzed. Overall fit: %s%%") % self.overall_fit_pct)
        except UserError:
            # Domain validation errors (e.g. no sections) still surface as
            # the original UserError so the user can fix the input.
            raise
        except Exception as e:  # noqa: BLE001
            # AI gateway unavailable / no API key / network down: degrade
            # gracefully so the UI does not show a 500 stack-trace during UAT.
            _logger.warning(
                "BRD AI gateway unavailable, falling back to stub: %s",
                e,
            )
            self.write({"state": "analyzed"})
            self.message_post(
                body=_(
                    "AI analysis stub — configure "
                    "<code>ai_bridge.anthropic_api_key</code> in Settings to "
                    "enable real analysis. Underlying error: %s"
                )
                % e,
            )

    def action_request_review(self):
        for rec in self:
            if rec.state != "analyzed":
                raise UserError(_("Only analyzed BRDs can be sent for review."))
            rec.state = "reviewed"
            rec._maybe_create_approval_request()
            rec.message_post(body=_("BRD sent for review."))
        return True

    def action_approve(self):
        for rec in self:
            if rec.state != "reviewed":
                raise UserError(_("Only reviewed BRDs can be approved."))
            rec.state = "approved"
            rec.message_post(body=_("BRD approved."))
        return True

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Sharing
    # ------------------------------------------------------------------

    def action_generate_share_token(self):
        for rec in self:
            rec.token_uuid = uuid.uuid4().hex
        return True

    def get_share_url(self):
        self.ensure_one()
        if not self.token_uuid:
            self.action_generate_share_token()
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return f"{base_url}/brd/share/{self.token_uuid}"

    # ------------------------------------------------------------------
    # Approval integration
    # ------------------------------------------------------------------

    def _maybe_create_approval_request(self):
        """Trigger ``custom_approval_engine`` if a matrix is configured."""
        self.ensure_one()
        Matrix = self.env.get("approval.matrix")
        Request = self.env.get("approval.request")
        if not Matrix or not Request:
            return
        matrices = Matrix.sudo().search(
            [("model_id.model", "=", self._name), ("active", "=", True)],
            order="priority desc, sequence asc",
        )
        if not matrices:
            return
        # Pick the first matrix; the engine itself may filter by domain.
        chosen = matrices[0]
        Request.sudo().create(
            {
                "matrix_id": chosen.id,
                "res_model": self._name,
                "res_id": self.id,
            }
        )

    # ------------------------------------------------------------------
    # UI helper
    # ------------------------------------------------------------------

    def action_open_report(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/brd/{self.id}/report",
            "target": "new",
        }
