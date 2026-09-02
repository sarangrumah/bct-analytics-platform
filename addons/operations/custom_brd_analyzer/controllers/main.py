# -*- coding: utf-8 -*-
"""HTTP controllers for the BRD analyzer report."""

from __future__ import annotations

import json
import logging

from odoo import http
from odoo.exceptions import AccessError
from odoo.http import request

_logger = logging.getLogger(__name__)


class BrdReportController(http.Controller):
    # ---- helpers ----

    def _get_doc_for_user(self, doc_id: int):
        doc = request.env["brd.document"].browse(doc_id)
        try:
            doc.check_access("read")
        except AccessError:
            return None
        return doc.exists() and doc or None

    def _get_doc_by_token(self, token: str):
        if not token:
            return None
        doc = request.env["brd.document"].sudo().search([("token_uuid", "=", token)], limit=1)
        return doc or None

    # ---- routes ----

    @http.route("/brd/<int:doc_id>/report", type="http", auth="user", website=False)
    def report_view(self, doc_id, **kwargs):
        doc = self._get_doc_for_user(doc_id)
        if not doc:
            return request.not_found()
        return self._render(doc)

    @http.route("/brd/share/<string:token>", type="http", auth="public", website=False)
    def report_share(self, token, **kwargs):
        doc = self._get_doc_by_token(token)
        if not doc:
            return request.not_found()
        return self._render(doc, public=True)

    @http.route("/brd/<int:doc_id>/report.pdf", type="http", auth="user")
    def report_pdf(self, doc_id, **kwargs):
        doc = self._get_doc_for_user(doc_id)
        if not doc:
            return request.not_found()
        try:
            pdf, _ct = (
                request.env["ir.actions.report"]
                .sudo()
                ._render_qweb_pdf("custom_brd_analyzer.action_report_brd", [doc.id])
            )
        except Exception as exc:  # pragma: no cover - depends on wkhtmltopdf
            _logger.warning("BRD PDF render failed: %s", exc)
            return request.make_response("PDF rendering failed: %s" % exc, [("Content-Type", "text/plain")])
        return request.make_response(
            pdf,
            headers=[
                ("Content-Type", "application/pdf"),
                ("Content-Disposition", f'attachment; filename="brd_{doc.id}_report.pdf"'),
            ],
        )

    @http.route("/brd/<int:doc_id>/share", type="jsonrpc", auth="user", methods=["POST"])
    def report_share_create(self, doc_id, **kwargs):
        doc = self._get_doc_for_user(doc_id)
        if not doc:
            return {"error": "not_found"}
        if not doc.token_uuid:
            doc.action_generate_share_token()
        return {"url": doc.get_share_url(), "token": doc.token_uuid}

    # ---- render ----

    def _render(self, doc, public: bool = False):
        sections = doc.section_ids.sorted("sequence")
        analyses = {a.section_id.id: a for a in doc.analysis_ids}
        recs = doc.recommendation_ids.sorted("sequence")
        # Build a tiny mermaid diagram source (best-effort, harmless if mermaid lib absent).
        mermaid_lines = ["graph LR"]
        for r in recs:
            safe = (r.name or "rec").replace("-", "_")
            mermaid_lines.append(f'    {safe}["{r.name}"]')
            for d in r.depends_on_module_ids:
                mermaid_lines.append(f"    {safe} --> {d.module_name}")
            for i in r.impact_module_ids:
                mermaid_lines.append(f"    {safe} -.-> {i.module_name}")
        mermaid_src = "\n".join(mermaid_lines)

        values = {
            "doc": doc,
            "sections": sections,
            "analyses": analyses,
            "recommendations": recs,
            "mermaid_src": mermaid_src,
            "public": public,
            "json_payload": json.dumps(
                {
                    "doc_id": doc.id,
                    "name": doc.name,
                    "overall_fit_pct": doc.overall_fit_pct,
                    "severity_summary": doc.severity_summary,
                    "sections": [
                        {
                            "id": s.id,
                            "title": s.title,
                            "level": s.level,
                            "page": s.page_or_slide,
                            "fit_score": analyses.get(s.id) and analyses[s.id].fit_score or 0,
                            "gap_status": analyses.get(s.id) and analyses[s.id].gap_status or "unclear",
                            "gap_severity": analyses.get(s.id) and analyses[s.id].gap_severity or "should_have",
                            "mapped": analyses.get(s.id)
                            and analyses[s.id].mapped_module_ids.mapped("module_name")
                            or [],
                            "notes": analyses.get(s.id) and analyses[s.id].notes or "",
                        }
                        for s in sections
                    ],
                    "recommendations": [
                        {
                            "id": r.id,
                            "name": r.name,
                            "severity": r.severity,
                            "estimated_md": r.estimated_md,
                            "scope": r.scope or "",
                            "justification": r.justification or "",
                            "depends": r.depends_on_module_ids.mapped("module_name"),
                            "impact": r.impact_module_ids.mapped("module_name"),
                            "tags": r.capability_tag_ids.mapped("technical_code"),
                        }
                        for r in recs
                    ],
                }
            ),
        }
        return request.render("custom_brd_analyzer.brd_report_page", values)
