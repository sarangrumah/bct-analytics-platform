# -*- coding: utf-8 -*-
import json
import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers (re-usable by other addons: contacts, HR, KYC)
# ---------------------------------------------------------------------------

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)\.]+")
_NIK_RE = re.compile(r"^\d{16}$")
_PHONE_VALID_RE = re.compile(r"^\+62\d{8,13}$")


def _normalize_phone_id(value):
    """Normalize an Indonesian phone number to canonical ``+628xxxx`` form.

    Accepts inputs such as:
        - ``0812-3456-7890``
        - ``+62 812 3456 7890``
        - ``62-812-3456-7890``
        - ``0062812 34567890``
    Returns the canonical ``+62...`` string, or the cleaned input if it does
    not look like an Indonesian mobile/landline number.
    """
    if not value:
        return value
    cleaned = _PHONE_STRIP_RE.sub("", str(value))
    # Drop international call prefix variants
    if cleaned.startswith("0062"):
        cleaned = "+62" + cleaned[4:]
    elif cleaned.startswith("62") and not cleaned.startswith("+62"):
        cleaned = "+62" + cleaned[2:]
    elif cleaned.startswith("0"):
        cleaned = "+62" + cleaned[1:]
    elif cleaned.startswith("+62"):
        pass
    return cleaned


def _validate_nik(value):
    """Return True when ``value`` is a syntactically valid Indonesian NIK.

    Per Permendagri 19/2018, NIK is a 16-digit numeric identifier. We do not
    validate the embedded region/birthdate/sequence components here; that
    belongs in ``custom_pdp_taxonomy`` or a KYC module.
    """
    if not value:
        return False
    return bool(_NIK_RE.match(str(value).strip()))


def _is_valid_phone_id_format(value):
    """Lightweight check whether a value matches the canonical +62 format."""
    if not value:
        return False
    return bool(_PHONE_VALID_RE.match(str(value).strip()))


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


class CustomDedupRule(models.Model):
    _name = "custom.dedup.rule"
    _description = "Custom Deduplication Rule"
    _inherit = ["mail.thread"]
    _order = "name"

    name = fields.Char(
        string="Name",
        required=True,
        tracking=True,
    )
    model_name = fields.Char(
        string="Model",
        required=True,
        help="Technical name e.g. res.partner",
    )
    match_fields = fields.Char(
        string="Match Fields",
        required=True,
        help="Comma-separated field names",
    )
    normalize_phone_id = fields.Boolean(
        string="Normalize Indonesian Phone",
        default=True,
        help="Normalize Indonesian phone to +62 format before compare",
    )
    normalize_email_case = fields.Boolean(
        string="Normalize Email Case",
        default=True,
    )
    is_active = fields.Boolean(
        string="Active",
        default=True,
        tracking=True,
    )
    cron_active = fields.Boolean(
        string="Scheduled",
        default=False,
        tracking=True,
        help="Run this rule daily via ir.cron.",
    )
    cron_id = fields.Many2one(
        comodel_name="ir.cron",
        string="Cron Job",
        readonly=True,
        ondelete="set null",
    )
    last_run_at = fields.Datetime(
        string="Last Run",
        readonly=True,
    )
    last_match_count = fields.Integer(
        string="Last Match Count",
        readonly=True,
    )
    candidate_ids = fields.One2many(
        comodel_name="custom.dedup.candidate",
        inverse_name="rule_id",
        string="Candidates",
    )
    candidate_count = fields.Integer(
        string="Candidate Count",
        compute="_compute_candidate_count",
    )

    @api.depends("candidate_ids")
    def _compute_candidate_count(self):
        for rule in self:
            rule.candidate_count = len(rule.candidate_ids)

    # ------------------------------------------------------------------
    # Normalization helpers
    # ------------------------------------------------------------------

    def _normalize_value(self, field_name, value):
        """Normalize a single value depending on rule options and field name."""
        if value in (None, False, ""):
            return ""
        text = str(value).strip()
        lower_field = (field_name or "").lower()
        if self.normalize_phone_id and lower_field in ("phone", "mobile", "phone_id", "x_phone", "x_mobile"):
            return _normalize_phone_id(text) or ""
        if self.normalize_email_case and lower_field in ("email", "email_normalized", "x_email"):
            return text.lower().strip()
        return text.lower().strip()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _parse_match_fields(self):
        self.ensure_one()
        return [f.strip() for f in (self.match_fields or "").split(",") if f.strip()]

    def action_run_scan(self):
        """Real deduplication scan.

        Groups records by tuple of normalized match-field values; emits one
        ``custom.dedup.candidate`` per group whose size > 1.
        """
        for rule in self:
            fields_list = rule._parse_match_fields()
            if not fields_list:
                raise UserError(_("Rule '%s' has no match fields configured.") % rule.name)
            if not rule.model_name or rule.model_name not in self.env:
                raise UserError(_("Model '%s' is not available in this database.") % rule.model_name)

            Model = self.env[rule.model_name].sudo()
            # Drop existing pending candidates so re-scans are idempotent
            rule.candidate_ids.filtered(lambda c: c.state == "pending").unlink()

            try:
                records = Model.search_read([], fields_list + ["id"])
            except Exception as exc:  # pragma: no cover — defensive
                _logger.exception(
                    "custom_data_cleaning: scan failed to read %s: %s",
                    rule.model_name,
                    exc,
                )
                rule.write(
                    {
                        "last_run_at": fields.Datetime.now(),
                        "last_match_count": 0,
                    }
                )
                continue

            buckets = {}
            for rec in records:
                try:
                    key_parts = []
                    for fname in fields_list:
                        key_parts.append(rule._normalize_value(fname, rec.get(fname)))
                    key = tuple(key_parts)
                    if not any(key):
                        continue  # skip records where every match field is empty
                    buckets.setdefault(key, []).append(rec)
                except Exception as exc:  # pragma: no cover
                    _logger.warning(
                        "custom_data_cleaning: skip rec %s on rule %s: %s",
                        rec.get("id"),
                        rule.name,
                        exc,
                    )

            Candidate = self.env["custom.dedup.candidate"]
            created = 0
            for key, group in buckets.items():
                if len(group) < 2:
                    continue
                ids = [r["id"] for r in group]
                preview_parts = []
                for r in group[:3]:
                    label = r.get("display_name") or r.get("name") or r.get(fields_list[0]) or "ID %s" % r["id"]
                    preview_parts.append(str(label))
                preview = " | ".join(preview_parts)
                if len(group) > 3:
                    preview += " (+%d more)" % (len(group) - 3)
                Candidate.create(
                    {
                        "rule_id": rule.id,
                        "res_ids_json": json.dumps(ids),
                        "preview": preview[:255],
                        "match_key": " || ".join(str(k) for k in key)[:255],
                    }
                )
                created += 1

            rule.write(
                {
                    "last_run_at": fields.Datetime.now(),
                    "last_match_count": created,
                }
            )
            rule.message_post(
                body=_("Scan complete: %(n)d duplicate group(s) found across %(t)d record(s).")
                % {
                    "n": created,
                    "t": len(records),
                }
            )
        return True

    # ------------------------------------------------------------------
    # Cron integration
    # ------------------------------------------------------------------

    def _cron_code(self):
        # ir.actions.server / ir.cron code must avoid `from datetime import`
        return (
            "rule = env['custom.dedup.rule'].browse(%d)\n"
            "if rule.exists() and rule.is_active and rule.cron_active:\n"
            "    rule.action_run_scan()\n"
        ) % self.id

    def _create_cron_if_active(self):
        Cron = self.env["ir.cron"].sudo()
        Model = self.env["ir.model"].sudo()
        model_rec = Model.search([("model", "=", "custom.dedup.rule")], limit=1)
        for rule in self:
            if rule.cron_active and rule.is_active:
                vals = {
                    "name": "Dedup Scan: %s" % rule.name,
                    "model_id": model_rec.id,
                    "state": "code",
                    "code": rule._cron_code(),
                    "interval_number": 1,
                    "interval_type": "days",
                    "active": True,
                }
                if rule.cron_id:
                    rule.cron_id.write(vals)
                else:
                    cron = Cron.create(vals)
                    rule.with_context(_skip_cron_sync=True).cron_id = cron.id
            else:
                if rule.cron_id:
                    rule.cron_id.unlink()
                    rule.with_context(_skip_cron_sync=True).cron_id = False
        return True

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules._create_cron_if_active()
        return rules

    def write(self, vals):
        res = super().write(vals)
        if self.env.context.get("_skip_cron_sync"):
            return res
        if {"cron_active", "is_active", "name"} & set(vals.keys()):
            self._create_cron_if_active()
        return res

    def unlink(self):
        for rule in self:
            if rule.cron_id:
                rule.cron_id.unlink()
        return super().unlink()
