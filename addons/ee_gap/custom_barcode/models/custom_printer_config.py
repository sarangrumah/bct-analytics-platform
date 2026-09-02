# -*- coding: utf-8 -*-
import logging
import socket

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class CustomPrinterConfig(models.Model):
    """Configured physical or virtual label printer."""

    _name = "custom.printer.config"
    _description = "Printer Configuration"
    _order = "name"

    name = fields.Char(required=True)
    printer_type = fields.Selection(
        [
            ("zebra_network", "Zebra (network, raw 9100)"),
            ("zebra_usb", "Zebra (USB via local agent)"),
            ("escpos_network", "ESC/POS (network)"),
            ("cups", "CUPS queue"),
        ],
        default="zebra_network",
        required=True,
    )
    host = fields.Char(help="Hostname or IP for network printers.")
    port = fields.Integer(default=9100)
    cups_queue = fields.Char(help="CUPS queue name when printer_type=cups.")
    label_template_id = fields.Many2one("custom.label.template")
    status = fields.Selection(
        [
            ("active", "Active"),
            ("disabled", "Disabled"),
            ("error", "Error"),
        ],
        default="active",
        required=True,
    )
    last_error = fields.Text(readonly=True)
    company_id = fields.Many2one(
        "res.company",
        default=lambda s: s.env.company,
    )

    def send_raw(self, payload):
        """Send raw bytes to the printer using its configured transport.

        Returns True on success. On error, records `last_error` and raises
        UserError (or returns False if called silently).
        """
        self.ensure_one()
        if self.status == "disabled":
            raise UserError(_("Printer %s is disabled.") % self.name)
        if self.printer_type in ("zebra_network", "escpos_network"):
            return self._send_socket(payload)
        if self.printer_type == "cups":
            return self._send_cups(payload)
        if self.printer_type == "zebra_usb":
            # Local agent (out of process). We can only stage to print queue.
            raise UserError(_("USB printers require a local agent to consume the queue."))
        raise UserError(_("Unsupported printer type: %s") % self.printer_type)

    def _send_socket(self, payload):
        self.ensure_one()
        if not self.host:
            raise UserError(_("Printer %s has no host configured.") % self.name)
        port = self.port or 9100
        try:
            with socket.create_connection((self.host, port), timeout=5.0) as sk:
                sk.sendall(payload if isinstance(payload, (bytes, bytearray)) else payload.encode("utf-8"))
            self.write({"status": "active", "last_error": False})
            return True
        except Exception as e:
            _logger.warning("Print send failed %s:%s -- %s", self.host, port, e)
            self.write({"status": "error", "last_error": str(e)})
            raise UserError(_("Failed to send to %s:%s -- %s") % (self.host, port, e)) from e

    def _send_cups(self, payload):
        self.ensure_one()
        if not self.cups_queue:
            raise UserError(_("Printer %s has no CUPS queue configured.") % self.name)
        # No-op stub. Production deployments typically use python-cups in a
        # local worker; we just record the staged payload size.
        _logger.info("[cups stub] would print %d bytes to queue %s", len(payload), self.cups_queue)
        return True
