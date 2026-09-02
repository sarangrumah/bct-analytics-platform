# -*- coding: utf-8 -*-
"""Auto-classify newly created documents via the AI classify endpoint."""

from __future__ import annotations

import base64
import logging

from odoo import api, models

_logger = logging.getLogger(__name__)


class DocumentAutoClassify(models.Model):
    _inherit = "document.document"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for rec in records:
            try:
                rec._ai_auto_classify()
            except Exception:
                _logger.exception("doc.auto_classify failed for %s", rec.id)
        return records

    def _ai_auto_classify(self):
        """Suggest a pdp.classification + tags based on filename/content."""
        self.ensure_one()
        if self.classification_id:
            # Workspace default already set it — respect existing decision
            return
        AI = self.env["custom.ai"].sudo()
        text_excerpt = self._extract_text_excerpt()
        try:
            result = AI._classify_document(
                filename=self.filename or self.name,
                mimetype=self.mimetype,
                text_excerpt=text_excerpt,
            )
        except Exception:
            return

        code = result.get("classification_code")
        if code:
            classification = (
                self.env["pdp.classification"]
                .sudo()
                .search(
                    [("code", "=", code)],
                    limit=1,
                )
            )
            if classification:
                self.classification_id = classification.id

        suggested_tags = result.get("tags") or []
        if suggested_tags:
            Tag = self.env["document.tag"].sudo()
            tag_ids = []
            for t in suggested_tags:
                existing = Tag.search([("name", "=", t)], limit=1)
                tag_ids.append(existing.id if existing else Tag.create({"name": t}).id)
            if tag_ids:
                self.tag_ids = [(4, tid) for tid in tag_ids]

        if result.get("rationale"):
            self.message_post(
                body=f"<b>AI classification:</b> {code} (confidence {result.get('confidence', 0):.2f}) — "
                f"{result['rationale']}",
            )

    def _extract_text_excerpt(self) -> str | None:
        """Best-effort: decode the attachment if it's plain text. PDFs etc. skipped."""
        self.ensure_one()
        if not self.attachment_id or not self.attachment_id.datas:
            return None
        mt = (self.mimetype or "").lower()
        if "text" not in mt and "json" not in mt and "xml" not in mt:
            return None
        try:
            raw = base64.b64decode(self.attachment_id.datas)
            return raw.decode("utf-8", errors="replace")[:8000]
        except Exception:
            return None
