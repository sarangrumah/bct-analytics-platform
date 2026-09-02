# -*- coding: utf-8 -*-
from odoo import _, api, fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    x_custom_show_date = fields.Date(
        string="Show Date",
        copy=True,
        tracking=True,
        help="Event/show date for this order. Propagated to the customer "
        "invoice; when the company has Show Date enabled, the invoice "
        "payment-term due dates are anchored to this date.",
    )
    x_custom_show_date_required = fields.Boolean(
        string="Show Date Required",
        compute="_compute_x_custom_show_date_required",
        help="Technical helper: True when this order's company has Show Date "
        "enabled. Drives the form 'required'/'invisible' attributes.",
    )
    # The three fields below feed the order-line description block (see
    # sale_order_line.py). They are captured once per order because a show is
    # one event: every line of the order belongs to the same event.
    x_custom_event_name = fields.Char(
        string="Event",
        copy=True,
        tracking=True,
        help='Event name printed in the line description, e.g. "Danone".',
    )
    x_custom_event_location = fields.Char(
        string="Lokasi Event",
        copy=True,
        tracking=True,
        help='Venue printed in the line description, e.g. "Taman Bhagawan Bali".',
    )
    x_custom_dp_note = fields.Char(
        string="Keterangan DP / Pelunasan",
        copy=True,
        tracking=True,
        help='Free text closing the line description, e.g. "DP 50%" or "PELUNASAN 50%". Left out when empty.',
    )

    def _custom_event_description(self, include_dp_note=True):
        """The event line appended to each product line's description.

        ``include_dp_note`` is turned off by the down-payment line, which already
        states the down payment in its own trailing marker ("(Uang Muka 50%)");
        repeating the free-text note there would print "DP 50% (Uang Muka 50%)".

        Returns "" when the company gate is off or nothing has been captured,
        so the caller can leave the core description untouched.
        """
        self.ensure_one()
        if not self.company_id.x_custom_show_date_enabled:
            return ""
        parts = []
        if self.x_custom_event_name:
            parts.append("Event %s" % self.x_custom_event_name)
        if self.x_custom_event_location:
            parts.append("Lokasi %s" % self.x_custom_event_location)
        if self.x_custom_show_date:
            # dd.mm.yy — the format the client writes in their own samples.
            parts.append(self.x_custom_show_date.strftime("%d.%m.%y"))
        if include_dp_note and self.x_custom_dp_note:
            parts.append(self.x_custom_dp_note)
        return ", ".join(parts)

    def _custom_down_payment_description(self, dp_marker=""):
        """Description printed on the down-payment invoice line.

        Core labels that line "Down payment of 50.00%", and that wording reaches
        both the invoice PDF and the Faktur Pajak "Nama Barang Jasa" cell — the
        coretax exporter reads ``line.product_id.name or line.name`` and a DP
        line carries no product, so it falls through to the name. the tenant bills the
        customer for the show, so the products being down-paid lead, the event
        block follows, and the down payment itself is reduced to a trailing
        marker::

            Jasa Drone Show 250 Unit, Event Danone, Lokasi Taman Bhagawan Bali,
            07.08.26 (Uang Muka 50%)

        Deliberately a SINGLE line: the same string lands in one cell of the
        coretax import file, where an embedded newline is not safe. The invoice
        PDF wraps it to the column width instead.

        Returns "" when the company gate is off or the order has no product
        line, so the caller keeps the core wording.
        """
        self.ensure_one()
        if not self.company_id.x_custom_show_date_enabled:
            return ""
        names = []
        for line in self.order_line:
            if line.display_type or line.is_downpayment:
                continue
            # First line only: sale_order_line.py appends the event block to
            # every product line, and taking it from each of them would repeat
            # the same event once per product. It is added below, once.
            label = (line.name or line.product_id.name or "").split("\n")[0].strip()
            if label and label not in names:
                names.append(label)
        if not names:
            return ""
        parts = [", ".join(names)]
        # The event detail must reach the DP printouts too — the invoice PDF and
        # the Faktur Pajak both read this one string.
        event = self._custom_event_description(include_dp_note=False)
        if event:
            parts.append(event)
        description = ", ".join(parts)
        return "%s %s" % (description, dp_marker) if dp_marker else description

    @api.depends("company_id", "company_id.x_custom_show_date_enabled")
    def _compute_x_custom_show_date_required(self):
        for order in self:
            order.x_custom_show_date_required = bool(order.company_id.x_custom_show_date_enabled)

    def _confirmation_error_message(self):
        # Preserve core confirmation checks first.
        msg = super()._confirmation_error_message()
        if msg:
            return msg
        if self.company_id.x_custom_show_date_enabled and not self.x_custom_show_date:
            return _("Please set the Show Date before confirming this order.")
        return False

    def _prepare_invoice(self):
        values = super()._prepare_invoice()
        # Harmless when the company flag is off: the field just carries over;
        # only the due-date anchoring (account.move) is gated on the flag.
        values["x_custom_show_date"] = self.x_custom_show_date
        return values
