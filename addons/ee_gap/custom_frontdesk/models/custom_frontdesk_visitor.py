# -*- coding: utf-8 -*-
import base64
import io
import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomFrontdeskVisitor(models.Model):
    _name = "custom.frontdesk.visitor"
    _description = "Frontdesk Visitor"
    _inherit = ["mail.thread", "pdp.audited.mixin"]
    _order = "check_in_time desc, id desc"

    name = fields.Char(required=True, tracking=True)
    visitor_company = fields.Char(string="Visitor Company")
    phone = fields.Char()
    email = fields.Char()
    id_number = fields.Char(
        string="KTP/Passport No",
        groups="custom_frontdesk.group_manager",
    )
    photo = fields.Binary(attachment=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Visitor Partner",
        index=True,
        help="Optional link to a res.partner so historical visits aggregate on that contact.",
    )
    host_employee_id = fields.Many2one(
        "hr.employee",
        string="Host",
        required=True,
        tracking=True,
    )
    station_id = fields.Many2one(
        "custom.frontdesk.station",
        string="Station",
        required=True,
    )
    purpose = fields.Char()
    check_in_time = fields.Datetime(
        default=fields.Datetime.now,
        tracking=True,
    )
    check_out_time = fields.Datetime(tracking=True)
    state = fields.Selection(
        [
            ("expected", "Expected"),
            ("checked_in", "Checked In"),
            ("checked_out", "Checked Out"),
            ("cancelled", "Cancelled"),
        ],
        default="expected",
        required=True,
        tracking=True,
    )
    badge_number = fields.Char(
        copy=False,
        default=lambda self: self.env["ir.sequence"].next_by_code("custom.frontdesk.visitor.badge") or "/",
    )
    notes = fields.Text()
    whatsapp_notified = fields.Boolean(default=False)

    # ---------- pre-registration / kiosk check-in via QR ----------

    kiosk_token = fields.Char(
        copy=False,
        index=True,
        help="One-time token used in the QR code printed on the "
        "pre-registration email. Visitor scans it at the kiosk to "
        "self-check-in.",
    )
    kiosk_token_used = fields.Boolean(default=False)
    qr_code_image = fields.Binary(
        compute="_compute_qr_code_image",
        string="QR Code",
        help="PNG QR encoding the kiosk_token. Embedded in the badge and pre-registration email.",
    )

    @api.depends("kiosk_token")
    def _compute_qr_code_image(self):
        """Render a PNG QR for the kiosk token using the qrcode library.

        Falls back to an empty value if the optional dependency is missing
        so the report still renders (placeholder).
        """
        for rec in self:
            if not rec.kiosk_token:
                rec.qr_code_image = False
                continue
            try:
                import qrcode  # type: ignore

                base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")
                payload = "%s/custom_frontdesk/kiosk_checkin/%s" % (
                    base.rstrip("/"),
                    rec.kiosk_token,
                )
                buf = io.BytesIO()
                qrcode.make(payload).save(buf, format="PNG")
                rec.qr_code_image = base64.b64encode(buf.getvalue())
            except Exception as e:  # pragma: no cover - optional dep
                _logger.debug(
                    "Frontdesk: QR render skipped (%s); install python qrcode",
                    e,
                )
                rec.qr_code_image = False

    @staticmethod
    def _new_kiosk_token() -> str:
        return secrets.token_urlsafe(24)

    # ---------- workflow buttons ----------

    def action_check_in(self):
        for rec in self:
            rec.write(
                {
                    "state": "checked_in",
                    "check_in_time": fields.Datetime.now(),
                }
            )
            rec._notify_host_whatsapp()
        return True

    def action_check_out(self):
        for rec in self:
            rec.write(
                {
                    "state": "checked_out",
                    "check_out_time": fields.Datetime.now(),
                }
            )
        return True

    def action_cancel(self):
        self.write({"state": "cancelled"})
        return True

    def action_preregister_visitor(self):
        """Host-side: generate kiosk QR token and email/WhatsApp the visitor."""
        Template = self.env.ref(
            "custom_frontdesk.mail_template_preregister_visitor",
            raise_if_not_found=False,
        )
        for rec in self:
            if rec.state != "expected":
                raise UserError(_("Only visitors in state 'Expected' can be pre-registered."))
            if not rec.kiosk_token:
                rec.kiosk_token = self._new_kiosk_token()
                rec.kiosk_token_used = False
            # Email (best-effort)
            if Template and rec.email:
                try:
                    Template.send_mail(rec.id, force_send=False)
                except Exception as e:  # pragma: no cover
                    _logger.warning(
                        "Frontdesk: pre-register email failed for %s: %s",
                        rec.name,
                        e,
                    )
            # WhatsApp via template (best-effort)
            rec._send_preregister_whatsapp()
        return True

    def action_print_badge(self):
        """Return the QWeb-HTML badge report action."""
        self.ensure_one()
        return self.env.ref("custom_frontdesk.action_report_visitor_badge").report_action(self)

    def action_view_visits_for_partner(self):
        """Open a list of all visits for the same partner."""
        self.ensure_one()
        if not self.partner_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Visits — %s") % self.partner_id.display_name,
            "res_model": "custom.frontdesk.visitor",
            "view_mode": "list,form",
            "domain": [("partner_id", "=", self.partner_id.id)],
        }

    # ---------- kiosk-token-driven check-in ----------

    @api.model
    def _check_in_by_token(self, token: str):
        """Locate a visitor by single-use kiosk_token and check them in.

        Returns the visitor record. Raises UserError for missing / used /
        cancelled tokens so the controller can render a friendly page.
        """
        if not token:
            raise UserError(_("Missing kiosk token."))
        visitor = self.sudo().search(
            [("kiosk_token", "=", token)],
            limit=1,
        )
        if not visitor:
            raise UserError(_("Unknown or expired QR code."))
        if visitor.kiosk_token_used:
            raise UserError(_("This QR code has already been used."))
        if visitor.state == "cancelled":
            raise UserError(_("This visit was cancelled."))
        if visitor.state == "expected":
            visitor.write(
                {
                    "state": "checked_in",
                    "check_in_time": fields.Datetime.now(),
                    "kiosk_token_used": True,
                }
            )
            visitor._notify_host_whatsapp()
        else:
            visitor.write({"kiosk_token_used": True})
        return visitor

    # ---------- export anonymized (compliance) ----------

    def export_anonymized(self):
        """Return a list of dicts with id_number masked.

        Used by the compliance wizard / programmatic consumers to dump a
        visitor list without leaking KTP/passport numbers. The id_number
        field itself is groups-protected; this method enforces masking
        regardless of caller permissions.
        """
        rows = []
        for rec in self.sudo():
            masked = None
            if rec.id_number:
                tail = rec.id_number[-4:] if len(rec.id_number) > 4 else ""
                masked = ("*" * max(0, len(rec.id_number) - 4)) + tail
            rows.append(
                {
                    "id": rec.id,
                    "name": rec.name,
                    "visitor_company": rec.visitor_company or "",
                    "host": rec.host_employee_id.name or "",
                    "station": rec.station_id.name or "",
                    "check_in_time": fields.Datetime.to_string(rec.check_in_time) if rec.check_in_time else "",
                    "check_out_time": fields.Datetime.to_string(rec.check_out_time) if rec.check_out_time else "",
                    "state": rec.state,
                    "id_number_masked": masked,
                }
            )
        return rows

    # ---------- whatsapp notifications ----------

    def _notify_host_whatsapp(self):
        """Notify host via WhatsApp using the dedicated template if available.

        Falls back to a draft free-text whatsapp.message when the template
        record is missing (e.g. fresh DB before data load).
        """
        Message = self.env["whatsapp.message"]
        Account = self.env["whatsapp.account"]
        template = self.env.ref(
            "custom_frontdesk.whatsapp_template_host_notify",
            raise_if_not_found=False,
        )
        for rec in self:
            host = rec.host_employee_id
            host_phone = (host.mobile_phone or host.work_phone) if host else False
            if not host_phone:
                _logger.info(
                    "Frontdesk: host %s has no phone; skipping WhatsApp notify",
                    host.name if host else "?",
                )
                continue
            try:
                account = Account.search([("active", "=", True)], limit=1)
                if not account:
                    _logger.info(
                        "Frontdesk: no active whatsapp.account; skipping notify for %s",
                        rec.name,
                    )
                    continue
                vals = {
                    "account_id": account.id,
                    "to_phone": host_phone,
                    "body": rec._render_host_notify_body(template),
                }
                if template:
                    vals["template_id"] = template.id
                Message.sudo().create(vals)
                rec.whatsapp_notified = True
                _logger.info(
                    "Frontdesk: queued WhatsApp host notification for visitor %s",
                    rec.name,
                )
            except Exception as e:  # pragma: no cover
                _logger.warning(
                    "Frontdesk: WhatsApp notify failed for visitor %s: %s",
                    rec.name,
                    e,
                )
        return True

    def _render_host_notify_body(self, template):
        """Substitute visitor fields into the template body.

        Template uses {{name}}, {{company}}, {{station}} placeholders for
        readability (different from Meta's {{1}} positional form, which the
        whatsapp.template Meta sync handles separately).
        """
        self.ensure_one()
        if template and template.body_text:
            body = template.body_text
        else:
            body = _("Tamu Anda {{name}} dari {{company}} sudah check-in di {{station}}.")
        return (
            body.replace("{{name}}", self.name or "")
            .replace("{{company}}", self.visitor_company or "-")
            .replace("{{station}}", self.station_id.name or "-")
            .replace("{{purpose}}", self.purpose or "-")
        )

    def _send_preregister_whatsapp(self):
        """Send the visitor a WhatsApp with kiosk QR link (best-effort)."""
        self.ensure_one()
        if not self.phone:
            return False
        Message = self.env["whatsapp.message"]
        Account = self.env["whatsapp.account"]
        account = Account.search([("active", "=", True)], limit=1)
        if not account:
            return False
        base = self.env["ir.config_parameter"].sudo().get_param("web.base.url", default="")
        link = "%s/custom_frontdesk/kiosk_checkin/%s" % (
            base.rstrip("/"),
            self.kiosk_token or "",
        )
        body = _(
            "Halo %(name)s, kunjungan Anda ke %(station)s telah "
            "didaftarkan. Tunjukkan QR berikut di kiosk untuk check-in: %(link)s"
        ) % {
            "name": self.name or "",
            "station": self.station_id.name or "",
            "link": link,
        }
        try:
            Message.sudo().create(
                {
                    "account_id": account.id,
                    "to_phone": self.phone,
                    "body": body,
                }
            )
        except Exception as e:  # pragma: no cover
            _logger.warning(
                "Frontdesk: pre-register WA failed for %s: %s",
                self.name,
                e,
            )
        return True
