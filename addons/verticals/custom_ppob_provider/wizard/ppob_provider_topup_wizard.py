# -*- coding: utf-8 -*-
"""DP 100% + Pelunasan vendor bill wizard for provider deposit topup.

Self-contained: it posts its own DP + Pelunasan ``in_invoice`` pair and the
``account.move._post()`` hook advances the bucket subledger at the configured
timing. It does NOT depend on any third-party advance-payment module (the ERA
source's tk_purchase_advance_payment was never referenced from code).
"""

import uuid

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PpobProviderTopupWizard(models.TransientModel):
    _name = "custom.ppob.provider.topup.wizard"
    _description = "Top-up provider bucket via DP 100% + Pelunasan vendor bills"

    provider_id = fields.Many2one("custom.ppob.provider", required=True)
    mode = fields.Selection(related="provider_id.bucket_mode", readonly=True)
    bucket_id = fields.Many2one(
        comodel_name="custom.ppob.provider.bucket",
        string="Bucket",
        domain="[('provider_id', '=', provider_id), ('state', '=', 'active')]",
        required=True,
    )
    request_uid = fields.Char(
        default=lambda self: "PRV-TOPUP-" + uuid.uuid4().hex[:12].upper(),
        required=True,
        copy=False,
        help="Stable key. Re-running the wizard with the same value is a no-op (returns existing DP/Pelunasan pair).",
    )
    gross_amount = fields.Monetary(
        string="Gross Amount (incl. PPN, face value)",
        currency_field="currency_id",
        required=True,
    )
    discount_amount = fields.Monetary(
        string="Discount",
        currency_field="currency_id",
        default=0.0,
        help="Vendor discount on this topup. The deposit value stays at the "
        "gross face; discount is journaled per provider config (income or "
        "reduce_inventory).",
    )
    paid_amount = fields.Monetary(
        string="Paid Amount",
        currency_field="currency_id",
        compute="_compute_split",
        help="Cash outflow = gross - discount.",
    )
    coretax_method = fields.Selection(related="provider_id.coretax_method", readonly=True)
    timing = fields.Selection(related="provider_id.topup_dp_timing", readonly=True)
    dpp_amount = fields.Monetary(
        string="DPP (deposit value)",
        currency_field="currency_id",
        compute="_compute_split",
    )
    tax_amount = fields.Monetary(
        string="PPN Masukan",
        currency_field="currency_id",
        compute="_compute_split",
    )
    bank_account_id = fields.Many2one(
        "account.account",
        string="Source Bank Account",
        domain="[('account_type', 'in', ['asset_cash', 'asset_current'])]",
        help="For reference only; the bill is created in draft and the user "
        "pays it via Register Payment after the wizard finishes.",
    )
    reason = fields.Char(required=True, default="Provider deposit topup")
    currency_id = fields.Many2one(related="provider_id.currency_id", readonly=True)

    @api.depends(
        "gross_amount",
        "discount_amount",
        "provider_id",
        "provider_id.coretax_method",
        "provider_id.dpp_factor",
        "provider_id.ppn_rate",
    )
    def _compute_split(self):
        for w in self:
            gross = w.gross_amount or 0.0
            discount = w.discount_amount or 0.0
            w.paid_amount = gross - discount
            if w.provider_id and gross > 0:
                dpp, ppn = w.provider_id._coretax_split(gross)
                w.dpp_amount = dpp
                w.tax_amount = ppn
            else:
                w.dpp_amount = 0.0
                w.tax_amount = 0.0

    @api.onchange("provider_id")
    def _onchange_provider_id(self):
        if not self.provider_id:
            self.bucket_id = False
            return
        if self.provider_id.bucket_mode == "bulky":
            bucket = self.env["custom.ppob.provider.bucket"].search(
                [
                    ("provider_id", "=", self.provider_id.id),
                    ("mode", "=", "bulky"),
                    ("state", "=", "active"),
                ],
                limit=1,
            )
            self.bucket_id = bucket.id if bucket else False
        else:
            self.bucket_id = False

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def action_apply(self):
        self.ensure_one()
        provider = self.provider_id
        bucket = self.bucket_id
        if not bucket:
            raise UserError(_("Select a bucket to topup."))
        if bucket.provider_id != provider:
            raise UserError(_("Selected bucket does not belong to the chosen provider."))
        self._validate_provider_config(provider)

        # Idempotency: existing log row -> return its pair
        Log = self.env["custom.ppob.provider.topup.log"]
        existing = Log.search([("request_uid", "=", self.request_uid)], limit=1)
        if existing and existing.dp_invoice_id:
            return self._return_pair_action(existing.dp_invoice_id, existing.pelunasan_invoice_id)

        if not provider.dp_purchase_tax_id:
            raise UserError(
                _(
                    "Provider %s has no Purchase PPN Tax configured "
                    "(dp_purchase_tax_id). Set one before running this wizard."
                )
                % provider.code
            )
        if not provider.partner_id:
            raise UserError(_("Provider %s has no vendor partner configured.") % provider.code)

        log = existing or Log.create(self._prepare_log_vals())
        log.write(
            {
                "gross_amount": self.gross_amount,
                "discount_amount": self.discount_amount,
                "dpp_amount": self.dpp_amount,
                "ppn_amount": self.tax_amount,
                "coretax_method": provider.coretax_method,
                "timing": provider.topup_dp_timing,
                "reason": self.reason,
            }
        )

        AccountMove = self.env["account.move"]
        dp_invoice = AccountMove.create(self._prepare_dp_vals(log))
        pelunasan_invoice = AccountMove.create(self._prepare_pelunasan_vals(log, dp_invoice))

        # Two-way link
        dp_invoice.x_custom_ppob_pelunasan_bill_id = pelunasan_invoice.id
        log.write(
            {
                "dp_invoice_id": dp_invoice.id,
                "pelunasan_invoice_id": pelunasan_invoice.id,
            }
        )

        # Post DP first; bucket credit fires via _post hook for dp_post timing.
        dp_invoice.action_post()
        # Pelunasan posted next; closes any vendor advance for pelunasan_post.
        pelunasan_invoice.action_post()

        # Stock integration: incoming picking when bucket has an inventory
        # product. qty equals DPP (deposit face value, IDR) so 1 unit = Rp 1.
        if bucket.inventory_product_id:
            picking_invoice = dp_invoice if provider.topup_dp_timing == "dp_post" else pelunasan_invoice
            picking_qty = log.dpp_amount or self.dpp_amount
            bucket._stock_picking_incoming(
                qty=picking_qty,
                origin=self.request_uid,
                invoice=picking_invoice,
                partner=provider.partner_id,
            )

        if log.state == "draft":
            log.state = "posted"
        return self._return_pair_action(dp_invoice, pelunasan_invoice)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _validate_provider_config(self, provider):
        if provider.topup_dp_timing == "pelunasan_post" and not provider.vendor_advance_account_id:
            raise UserError(
                _("Provider %s: Vendor Advance Account is required for topup_dp_timing=pelunasan_post.") % provider.code
            )
        if (
            (self.discount_amount or 0.0) > 0
            and provider.discount_handling == "income"
            and not provider.discount_income_account_id
        ):
            raise UserError(
                _(
                    "Provider %s: Discount Income Account is required when "
                    "discount_handling=income and a discount is applied."
                )
                % provider.code
            )
        if not _bucket_inventory_account(provider, self.bucket_id):
            raise UserError(_("Bucket %s has no asset account; cannot post DP bill.") % self.bucket_id.display_name)

    def _prepare_log_vals(self):
        return {
            "provider_id": self.provider_id.id,
            "bucket_id": self.bucket_id.id,
            "request_uid": self.request_uid,
            "gross_amount": self.gross_amount,
            "discount_amount": self.discount_amount,
            "dpp_amount": self.dpp_amount,
            "ppn_amount": self.tax_amount,
            "coretax_method": self.provider_id.coretax_method,
            "timing": self.provider_id.topup_dp_timing,
            "reason": self.reason,
            "state": "draft",
        }

    def _dp_line_account(self, provider, bucket):
        """account_id for the deposit line on the DP bill.

        dp_post -> bucket inventory account; pelunasan_post -> vendor advance.
        """
        if provider.topup_dp_timing == "dp_post":
            return bucket.account_id
        return provider.vendor_advance_account_id

    def _prepare_dp_vals(self, log):
        provider = self.provider_id
        bucket = self.bucket_id
        ppn_tax = provider.dp_purchase_tax_id
        line_account = self._dp_line_account(provider, bucket)
        dp_product = self.env.ref("custom_ppob_provider.product_provider_dp_100")

        lines = [
            (
                0,
                0,
                {
                    "product_id": dp_product.id,
                    "name": _("Provider DP 100%% — %s") % (self.reason or self.request_uid),
                    "quantity": 1.0,
                    "price_unit": self.gross_amount,
                    "account_id": line_account.id,
                    "tax_ids": [(6, 0, ppn_tax.ids)] if ppn_tax else [(6, 0, [])],
                },
            )
        ]

        if (self.discount_amount or 0.0) > 0:
            if provider.discount_handling == "income":
                disc_account = provider.discount_income_account_id
                lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": dp_product.id,
                            "name": _("Vendor discount — %s") % self.request_uid,
                            "quantity": 1.0,
                            "price_unit": -self.discount_amount,
                            "account_id": disc_account.id,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                )
            else:  # reduce_inventory
                lines.append(
                    (
                        0,
                        0,
                        {
                            "product_id": dp_product.id,
                            "name": _("Vendor discount (reduces inventory) — %s") % self.request_uid,
                            "quantity": 1.0,
                            "price_unit": -self.discount_amount,
                            "account_id": line_account.id,
                            "tax_ids": [(6, 0, [])],
                        },
                    )
                )

        return {
            "move_type": "in_invoice",
            "partner_id": provider.partner_id.id,
            "currency_id": provider.currency_id.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_origin": self.request_uid,
            "narration": _("Provider DP 100%% topup for bucket %s") % bucket.display_name,
            "invoice_line_ids": lines,
            "x_custom_ppob_dp_role": "dp_100",
            "x_custom_ppob_bucket_id": bucket.id,
            "x_custom_ppob_topup_log_id": log.id,
        }

    def _prepare_pelunasan_vals(self, log, dp_invoice):
        provider = self.provider_id
        bucket = self.bucket_id
        no_tax = [(6, 0, [])]
        product_pelunasan = self.env.ref("custom_ppob_provider.product_provider_pelunasan")

        if provider.topup_dp_timing == "dp_post":
            # Net-zero pair on the bucket account: reversal without changing
            # balances. PPN already booked on DP.
            lines = [
                (
                    0,
                    0,
                    {
                        "product_id": product_pelunasan.id,
                        "name": _("Pelunasan offset (DP-post mode) — %s") % self.request_uid,
                        "quantity": 1.0,
                        "price_unit": self.gross_amount,
                        "account_id": bucket.account_id.id,
                        "tax_ids": no_tax,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "product_id": product_pelunasan.id,
                        "name": _("Pelunasan reversal — %s") % self.request_uid,
                        "quantity": 1.0,
                        "price_unit": -self.gross_amount,
                        "account_id": bucket.account_id.id,
                        "tax_ids": no_tax,
                    },
                ),
            ]
        else:  # pelunasan_post
            # Move Vendor Advance -> Bucket Inventory at Pelunasan post.
            lines = [
                (
                    0,
                    0,
                    {
                        "product_id": product_pelunasan.id,
                        "name": _("Pelunasan: recognise bucket inventory — %s") % self.request_uid,
                        "quantity": 1.0,
                        "price_unit": self.dpp_amount,
                        "account_id": bucket.account_id.id,
                        "tax_ids": no_tax,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "product_id": product_pelunasan.id,
                        "name": _("Pelunasan: clear vendor advance — %s") % self.request_uid,
                        "quantity": 1.0,
                        "price_unit": -self.dpp_amount,
                        "account_id": provider.vendor_advance_account_id.id,
                        "tax_ids": no_tax,
                    },
                ),
            ]

        return {
            "move_type": "in_invoice",
            "partner_id": provider.partner_id.id,
            "currency_id": provider.currency_id.id,
            "invoice_date": fields.Date.context_today(self),
            "invoice_origin": self.request_uid,
            "narration": _("Pelunasan against DP %s") % (dp_invoice.name or _("(draft)")),
            "invoice_line_ids": lines,
            "x_custom_ppob_dp_role": "pelunasan",
            "x_custom_ppob_dp_bill_id": dp_invoice.id,
            "x_custom_ppob_bucket_id": bucket.id,
            "x_custom_ppob_topup_log_id": log.id,
        }

    def _return_pair_action(self, dp_invoice, pelunasan_invoice):
        return {
            "type": "ir.actions.act_window",
            "name": _("Provider DP 100%% Topup"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", (dp_invoice | pelunasan_invoice).ids)],
            "target": "current",
        }


def _bucket_inventory_account(provider, bucket):
    return bucket.account_id or provider.bucket_inventory_account_id
