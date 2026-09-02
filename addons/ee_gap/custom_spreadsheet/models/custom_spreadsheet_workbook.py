# -*- coding: utf-8 -*-
import base64
import csv
import io
import json
import logging
import secrets
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)

_DEFAULT_DATA_JSON = '{"sheets":[{"name":"Sheet1","cells":{}}]}'
_AI_PAYLOAD_MAX_CHARS = 4000
_MAX_IMPORT_ROWS = 10000
_MAX_LOAD_RECORDS = 10000


class CustomSpreadsheetWorkbook(models.Model):
    _name = "custom.spreadsheet.workbook"
    _description = "Spreadsheet Workbook"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "create_date desc"

    name = fields.Char(required=True, tracking=True)
    owner_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    description = fields.Text()
    data_json = fields.Text(
        string="Grid Data (JSON)",
        default=_DEFAULT_DATA_JSON,
        help="JSON-encoded grid data",
    )
    thumbnail = fields.Binary(attachment=True)
    is_published = fields.Boolean(default=False)
    shared_user_ids = fields.Many2many(
        "res.users",
        relation="custom_spreadsheet_share_rel",
        column1="workbook_id",
        column2="user_id",
        string="Shared With",
    )
    tag_ids = fields.Many2many(
        "custom.spreadsheet.tag",
        string="Tags",
    )

    # ---------- AI-related fields ----------
    suggested_formulas = fields.Text(
        string="AI Formula Suggestions",
        readonly=True,
        help="Latest formula suggestions returned by AI.",
    )
    ai_clean_report = fields.Text(
        string="AI Data Cleaning Report",
        readonly=True,
        help="Latest data cleaning / outlier report from AI.",
    )

    # ---------- Sharing ----------
    share_token = fields.Char(
        string="Share Token",
        copy=False,
        readonly=True,
        index=True,
    )
    share_url = fields.Char(
        string="Share URL",
        compute="_compute_share_url",
    )

    # ---------- Versions ----------
    version_ids = fields.One2many(
        "custom.spreadsheet.version",
        "workbook_id",
        string="Versions",
    )
    version_count = fields.Integer(
        string="Version Count",
        compute="_compute_version_count",
    )

    # ---------- computes ----------

    @api.depends("share_token")
    def _compute_share_url(self):
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="").rstrip("/")
        for rec in self:
            if rec.share_token:
                rec.share_url = "%s/custom_spreadsheet/share/%s" % (base, quote(rec.share_token))
            else:
                rec.share_url = False

    @api.depends("version_ids")
    def _compute_version_count(self):
        for rec in self:
            rec.version_count = len(rec.version_ids)

    # ---------- CRUD overrides (versioning) ----------

    def write(self, vals):
        track_data = "data_json" in vals and not self.env.context.get("spreadsheet_skip_versioning")
        if track_data:
            for rec in self:
                old = rec.data_json or ""
                new = vals.get("data_json") or ""
                if old != new:
                    rec._snapshot_version(old, note="auto-pre-write")
        return super().write(vals)

    def _snapshot_version(self, data_json, note=None):
        self.ensure_one()
        Version = self.env["custom.spreadsheet.version"].sudo()
        last = Version.search([("workbook_id", "=", self.id)], order="version_no desc", limit=1)
        next_no = (last.version_no if last else 0) + 1
        Version.create(
            {
                "workbook_id": self.id,
                "version_no": next_no,
                "data_json_snapshot": data_json or _DEFAULT_DATA_JSON,
                "saved_by": self.env.user.id,
                "note": note or "",
            }
        )

    # ---------- AI ----------

    def _data_summary(self):
        """Compute lightweight stats over the workbook for AI payloads."""
        self.ensure_one()
        try:
            data = json.loads(self.data_json or _DEFAULT_DATA_JSON)
        except (ValueError, TypeError):
            return {"sheets": [], "parse_error": True}
        out = []
        for sheet in data.get("sheets", []):
            cells = sheet.get("cells") or {}
            rows = set()
            cols = set()
            sample = []
            for k, v in list(cells.items())[:25]:
                # key format "row_col"
                if "_" in str(k):
                    try:
                        r, c = str(k).split("_", 1)
                        rows.add(int(r))
                        cols.add(int(c))
                    except (ValueError, TypeError):
                        pass
                sample.append({"cell": k, "value": v})
            out.append(
                {
                    "name": sheet.get("name") or "Sheet",
                    "row_count": len(rows),
                    "col_count": len(cols),
                    "cell_count": len(cells),
                    "sample": sample,
                }
            )
        return {"sheets": out}

    def _custom_ai_payload(self, question, mode="ask", extra=None):
        self.ensure_one()
        payload = {
            "workbook": self.name,
            "description": (self.description or "")[:1000],
            "tags": self.tag_ids.mapped("name"),
            "question": question or "",
            "mode": mode,
            "data_summary": self._data_summary(),
            "data_json_excerpt": (self.data_json or "")[:_AI_PAYLOAD_MAX_CHARS],
            "data_json_truncated": bool(self.data_json and len(self.data_json) > _AI_PAYLOAD_MAX_CHARS),
        }
        if extra:
            payload.update(extra)
        return payload

    def _call_ai(self, payload):
        self.ensure_one()
        return self.env["custom.ai"]._recommend(
            model="custom.spreadsheet.workbook",
            res_id=self.id,
            payload=payload,
        )

    def _extract_ai_text(self, result):
        return result.get("response") or result.get("text") or result.get("summary") or json.dumps(result)[:2000]

    def action_ask_ai(self, question=None):
        self.ensure_one()
        try:
            result = self._call_ai(self._custom_ai_payload(question, mode="ask"))
        except Exception as e:
            _logger.error("Spreadsheet AI ask failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = self._extract_ai_text(result)
        body_q = question or _("(no question)")
        self.message_post(
            body=_("<b>Ask the spreadsheet</b><br/><i>Question:</i> %(q)s<br/><i>Answer:</i> %(a)s")
            % {"q": body_q, "a": text},
            subtype_xmlid="mail.mt_note",
        )
        return True

    def action_ai_formula_suggest(self, cell_ref=None, context_text=None):
        self.ensure_one()
        extra = {
            "cell_ref": cell_ref or "",
            "cell_context": (context_text or "")[:1000],
        }
        try:
            result = self._call_ai(
                self._custom_ai_payload(
                    question=_("Suggest a formula for cell %s") % (cell_ref or "?"),
                    mode="formula",
                    extra=extra,
                )
            )
        except Exception as e:
            _logger.error("Spreadsheet AI formula suggest failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = self._extract_ai_text(result)
        self.suggested_formulas = text
        self.message_post(
            body=_("<b>AI formula suggestion</b> (cell <code>%(c)s</code>)<br/><pre>%(t)s</pre>")
            % {"c": cell_ref or "?", "t": text},
            subtype_xmlid="mail.mt_note",
        )
        return True

    def action_ai_data_clean(self):
        self.ensure_one()
        try:
            result = self._call_ai(
                self._custom_ai_payload(
                    question=_(
                        "Identify outliers, missing values, type inconsistencies, and duplicate rows in this data."
                    ),
                    mode="clean",
                )
            )
        except Exception as e:
            _logger.error("Spreadsheet AI data clean failed: %s", e)
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("AI Unavailable"),
                    "message": str(e),
                    "type": "warning",
                },
            }
        text = self._extract_ai_text(result)
        self.ai_clean_report = text
        self.message_post(
            body=_("<b>AI data cleaning report</b><br/><pre>%(t)s</pre>") % {"t": text},
            subtype_xmlid="mail.mt_note",
        )
        return True

    # ---------- CSV import / export ----------

    def _load_data(self):
        self.ensure_one()
        try:
            return json.loads(self.data_json or _DEFAULT_DATA_JSON)
        except (ValueError, TypeError):
            return json.loads(_DEFAULT_DATA_JSON)

    def _dump_data(self, data):
        self.ensure_one()
        self.write({"data_json": json.dumps(data, ensure_ascii=False)})

    def _set_sheet(self, data, sheet_name, cells):
        sheets = data.setdefault("sheets", [])
        for s in sheets:
            if s.get("name") == sheet_name:
                s["cells"] = cells
                return
        sheets.append({"name": sheet_name, "cells": cells})

    def action_export_csv(self):
        """Export the first sheet of the workbook as a downloadable CSV."""
        self.ensure_one()
        data = self._load_data()
        sheets = data.get("sheets") or []
        if not sheets:
            raise UserError(_("This workbook has no sheets to export."))
        sheet = sheets[0]
        cells = sheet.get("cells") or {}

        # Determine grid dimensions
        max_row = -1
        max_col = -1
        parsed = {}
        for k, v in cells.items():
            try:
                r_s, c_s = str(k).split("_", 1)
                r, c = int(r_s), int(c_s)
            except (ValueError, TypeError):
                continue
            parsed[(r, c)] = v
            if r > max_row:
                max_row = r
            if c > max_col:
                max_col = c

        buf = io.StringIO()
        writer = csv.writer(buf)
        if max_row < 0:
            writer.writerow([])
        else:
            for r in range(max_row + 1):
                row = []
                for c in range(max_col + 1):
                    val = parsed.get((r, c), "")
                    if val is None:
                        val = ""
                    row.append(val)
                writer.writerow(row)

        # utf-8-sig, not utf-8: the BOM tells Excel the file is UTF-8 instead of
        # letting it fall back to the system codepage and render mojibake. The
        # import wizard decodes with utf-8-sig, so a round-trip is unaffected.
        content = buf.getvalue().encode("utf-8-sig")
        fname = "%s.csv" % (self.name or "workbook")
        attachment = self.env["ir.attachment"].create(
            {
                "name": fname,
                "type": "binary",
                "datas": base64.b64encode(content),
                "res_model": self._name,
                "res_id": self.id,
                "mimetype": "text/csv",
            }
        )
        self.message_post(
            body=_("<b>CSV exported</b>: %s rows, %s columns") % (max_row + 1, max_col + 1),
            attachment_ids=[attachment.id],
            subtype_xmlid="mail.mt_note",
        )
        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=1" % attachment.id,
            "target": "self",
        }

    def _apply_csv_rows(self, rows, sheet_name="Sheet1"):
        """Replace the named sheet's cells with the provided 2D row list."""
        self.ensure_one()
        if len(rows) > _MAX_IMPORT_ROWS:
            raise ValidationError(_("CSV exceeds maximum of %s rows.") % _MAX_IMPORT_ROWS)
        cells = {}
        for r_idx, row in enumerate(rows):
            for c_idx, val in enumerate(row):
                cells["%d_%d" % (r_idx, c_idx)] = val
        data = self._load_data()
        self._set_sheet(data, sheet_name, cells)
        self._dump_data(data)
        return True

    # ---------- Load from model ----------

    def action_load_from_model(self, model_name, domain=None, fields_list=None, sheet_name="Data", append=False):
        """Pull records from `model_name` into the workbook as a table."""
        self.ensure_one()
        if not model_name:
            raise UserError(_("A model name is required."))
        if model_name not in self.env:
            raise UserError(_("Unknown model: %s") % model_name)
        Model = self.env[model_name]

        # Domain
        if isinstance(domain, str) and domain.strip():
            try:
                domain_list = json.loads(domain) if domain.strip().startswith("[") else self._eval_domain(domain)
            except (ValueError, TypeError):
                raise UserError(_("Invalid domain expression: %s") % domain) from None
        elif isinstance(domain, list):
            domain_list = domain
        else:
            domain_list = []

        # Fields
        if isinstance(fields_list, str):
            fields_list = [f.strip() for f in fields_list.split(",") if f.strip()]
        if not fields_list:
            fields_list = ["id", "display_name"]
        # Drop unknown fields
        valid_fields = [f for f in fields_list if f in Model._fields]
        if not valid_fields:
            raise UserError(_("None of the requested fields exist on %s.") % model_name)

        records = Model.sudo().search(domain_list, limit=_MAX_LOAD_RECORDS + 1)
        if len(records) > _MAX_LOAD_RECORDS:
            raise ValidationError(_("Result set exceeds maximum of %s records.") % _MAX_LOAD_RECORDS)

        # Build rows
        rows = [list(valid_fields)]
        for rec in records:
            row = []
            for fname in valid_fields:
                v = rec[fname]
                if hasattr(v, "_name") and hasattr(v, "ids"):
                    # recordset → display name(s)
                    row.append(", ".join(v.mapped("display_name")) if v else "")
                else:
                    row.append("" if v is False or v is None else str(v))
            rows.append(row)

        data = self._load_data()
        if append:
            existing_sheet = next(
                (s for s in data.get("sheets", []) if s.get("name") == sheet_name),
                None,
            )
            base_row = 0
            if existing_sheet:
                cells = existing_sheet.get("cells") or {}
                for k in cells.keys():
                    try:
                        r = int(str(k).split("_", 1)[0])
                        if r + 1 > base_row:
                            base_row = r + 1
                    except (ValueError, TypeError):
                        pass
                new_cells = dict(cells)
                # When appending, skip header row
                data_rows = rows[1:] if base_row > 0 else rows
                for r_idx, row in enumerate(data_rows):
                    for c_idx, val in enumerate(row):
                        new_cells["%d_%d" % (base_row + r_idx, c_idx)] = val
                self._set_sheet(data, sheet_name, new_cells)
            else:
                cells = {}
                for r_idx, row in enumerate(rows):
                    for c_idx, val in enumerate(row):
                        cells["%d_%d" % (r_idx, c_idx)] = val
                self._set_sheet(data, sheet_name, cells)
        else:
            cells = {}
            for r_idx, row in enumerate(rows):
                for c_idx, val in enumerate(row):
                    cells["%d_%d" % (r_idx, c_idx)] = val
            self._set_sheet(data, sheet_name, cells)

        self._dump_data(data)
        self.message_post(
            body=_("<b>Loaded from <code>%(m)s</code></b>: %(n)s record(s), %(c)s column(s) → sheet <i>%(s)s</i>")
            % {
                "m": model_name,
                "n": len(records),
                "c": len(valid_fields),
                "s": sheet_name,
            },
            subtype_xmlid="mail.mt_note",
        )
        return True

    @api.model
    def _eval_domain(self, expr):
        """Very small safe domain evaluator (only literals + lists/tuples)."""
        try:
            import ast

            node = ast.literal_eval(expr)
            if isinstance(node, list):
                return node
        except (ValueError, SyntaxError):
            pass
        raise UserError(_("Domain must be a list literal."))

    # ---------- Sharing ----------

    def action_generate_share_token(self):
        for rec in self:
            rec.share_token = secrets.token_urlsafe(24)
        return True

    def action_revoke_share_token(self):
        for rec in self:
            rec.share_token = False
        return True

    # ---------- Versions UI helpers ----------

    def action_view_versions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Versions"),
            "res_model": "custom.spreadsheet.version",
            "view_mode": "list,form",
            "domain": [("workbook_id", "=", self.id)],
            "context": {"default_workbook_id": self.id},
        }

    def action_open_import_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import CSV"),
            "res_model": "custom.spreadsheet.import.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_workbook_id": self.id},
        }

    def action_open_load_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Load from Model"),
            "res_model": "custom.spreadsheet.load.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_workbook_id": self.id},
        }
