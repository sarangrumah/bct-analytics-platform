# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class PpobProviderBucket(models.Model):
    _name = "custom.ppob.provider.bucket"
    _description = "PPOB Provider Bucket Inventory (Rupiah ex-tax)"
    _order = "provider_id, mode, product_id"
    _rec_name = "display_name"

    provider_id = fields.Many2one(
        comodel_name="custom.ppob.provider",
        required=True,
        ondelete="cascade",
        index=True,
    )
    mode = fields.Selection(
        selection=[
            ("fixed_denom", "Fixed Denom (per product)"),
            ("bulky", "Bulky (single bucket for all products)"),
        ],
        required=True,
    )
    product_id = fields.Many2one(
        comodel_name="custom.ppob.product",
        ondelete="restrict",
        index=True,
        help="Required for fixed_denom mode; must be empty for bulky mode.",
    )
    denom_value = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        help="Snapshot of product.denom for reporting.",
    )
    balance = fields.Monetary(
        currency_field="currency_id",
        readonly=True,
        copy=False,
        default=0.0,
        help="Available inventory in Rupiah ex-tax.",
    )
    low_water_mark = fields.Monetary(
        currency_field="currency_id",
        default=0.0,
        help="Warning threshold; transactions still allowed below this but an alert is raised to ops.",
    )
    account_id = fields.Many2one(
        comodel_name="account.account",
        string="Inventory Asset Account",
        required=True,
        domain="[('account_type', '=', 'asset_current')]",
        help="Asset account holding the bucket inventory value.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Journal",
        required=True,
        domain="[('type', '=', 'general')]",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ("active", "Active"),
            ("archived", "Archived"),
        ],
        default="active",
        required=True,
    )
    move_ids = fields.One2many(
        comodel_name="custom.ppob.provider.bucket.move",
        inverse_name="bucket_id",
    )
    inventory_product_id = fields.Many2one(
        comodel_name="product.product",
        string="Inventory Product",
        ondelete="restrict",
        help="Optional: stockable product mirroring this bucket. When set, "
        "provider topups create incoming stock.picking and mitra "
        "transactions create outgoing stock.picking using this product. "
        "For digital deposits, set qty unit = Rp 1. Leave blank to keep "
        "accounting-only buckets without stock.",
    )
    inventory_warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        help="Warehouse used for stock pickings tied to this bucket. Defaults to the company default when blank.",
    )
    display_name = fields.Char(compute="_compute_display_name", store=True)

    @api.depends("provider_id.code", "mode", "product_id.code", "balance")
    def _compute_display_name(self):
        for b in self:
            tag = b.product_id.code if b.mode == "fixed_denom" and b.product_id else "*"
            b.display_name = f"{b.provider_id.code or '?'}/{b.mode}/{tag}/Rp {b.balance:,.0f}"

    @api.constrains("mode", "product_id")
    def _check_product_per_mode(self):
        for b in self:
            if b.mode == "fixed_denom" and not b.product_id:
                raise ValidationError(_("FIXED_DENOM bucket requires a product."))
            if b.mode == "bulky" and b.product_id:
                raise ValidationError(_("BULKY bucket must not have a product."))

    @api.constrains("balance")
    def _check_non_negative(self):
        for b in self:
            if b.balance < 0:
                raise ValidationError(_("Bucket balance cannot be negative."))

    @api.model
    def init(self):
        # Partial unique indexes that vanilla _sql_constraints cannot express.
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS custom_ppob_provider_bucket_fixed_uniq
            ON custom_ppob_provider_bucket (provider_id, product_id, company_id)
            WHERE mode = 'fixed_denom'
        """)
        self.env.cr.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS custom_ppob_provider_bucket_bulky_uniq
            ON custom_ppob_provider_bucket (provider_id, company_id)
            WHERE mode = 'bulky'
        """)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("mode") == "fixed_denom" and vals.get("product_id") and not vals.get("denom_value"):
                product = self.env["custom.ppob.product"].browse(vals["product_id"])
                vals["denom_value"] = product.denom or 0.0
        return super().create(vals_list)

    def _lock(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id, balance FROM custom_ppob_provider_bucket WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        row = self.env.cr.fetchone()
        if not row:
            raise UserError(_("Bucket %s not found.") % self.id)
        return row[1] or 0.0

    def _post_journal(self, dpp_amount, tax_amount, move_type, reason, counterpart_account, partner=None):
        """Topup (credit_bucket): Dr bucket / Dr PPN Masukan / Cr counterpart (gross).
        Usage/refund (debit_bucket / refund_bucket): 2-line move bucket <-> counterpart."""
        self.ensure_one()
        dpp_amount = abs(dpp_amount or 0.0)
        tax_amount = abs(tax_amount or 0.0)
        partner = partner or self.provider_id.partner_id
        if move_type == "credit_bucket":
            if tax_amount and not self.provider_id.input_vat_account_id:
                raise UserError(_("Provider %s has no Input VAT account configured.") % self.provider_id.code)
            gross = dpp_amount + tax_amount
            lines = [
                (
                    0,
                    0,
                    {
                        "account_id": self.account_id.id,
                        "partner_id": partner.id if partner else False,
                        "name": reason,
                        "debit": dpp_amount,
                        "credit": 0.0,
                    },
                ),
            ]
            if tax_amount:
                lines.append(
                    (
                        0,
                        0,
                        {
                            "account_id": self.provider_id.input_vat_account_id.id,
                            "partner_id": partner.id if partner else False,
                            "name": f"{reason} (PPN Masukan)",
                            "debit": tax_amount,
                            "credit": 0.0,
                        },
                    )
                )
            lines.append(
                (
                    0,
                    0,
                    {
                        "account_id": counterpart_account.id,
                        "partner_id": partner.id if partner else False,
                        "name": reason,
                        "debit": 0.0,
                        "credit": gross,
                    },
                )
            )
        elif move_type in ("debit_bucket", "refund_bucket"):
            # Refunds reuse this branch with positive dpp_amount; PPN is not
            # reversed (already claimed). 2-line entry only.
            amount = dpp_amount
            if move_type == "debit_bucket":
                debit_account = counterpart_account
                credit_account = self.account_id
            else:
                debit_account = self.account_id
                credit_account = counterpart_account
            lines = [
                (
                    0,
                    0,
                    {
                        "account_id": debit_account.id,
                        "partner_id": partner.id if partner else False,
                        "name": reason,
                        "debit": amount,
                        "credit": 0.0,
                    },
                ),
                (
                    0,
                    0,
                    {
                        "account_id": credit_account.id,
                        "partner_id": partner.id if partner else False,
                        "name": reason,
                        "debit": 0.0,
                        "credit": amount,
                    },
                ),
            ]
        else:
            raise UserError(_("Unknown bucket journal move type: %s") % move_type)
        move = self.env["account.move"].create(
            {
                "journal_id": self.journal_id.id,
                "move_type": "entry",
                "ref": reason,
                "partner_id": partner.id if partner else False,
                "line_ids": lines,
            }
        )
        move.action_post()
        return move

    def _atomic_credit(self, dpp_amount, reason, counterpart_account, *, tax_amount=0.0, gross_amount=None, **extras):
        """Topup the bucket with a Rupiah ex-tax amount. ``tax_amount`` is the
        Input VAT split off the gross paid amount. The bucket balance only grows
        by ``dpp_amount``; PPN goes to a separate asset account."""
        self.ensure_one()
        if dpp_amount <= 0:
            raise UserError(_("Credit amount must be positive."))
        fresh = self._lock()
        move_type = extras.pop("move_type", "topup")
        journal_type = "credit_bucket" if move_type == "topup" else "refund_bucket"
        if journal_type == "refund_bucket":
            # Refund credit-back: 2-line, no PPN split.
            tax_amount = 0.0
        move = self._post_journal(
            dpp_amount,
            tax_amount,
            journal_type,
            reason,
            counterpart_account,
        )
        new_balance = fresh + dpp_amount
        vals = {
            "bucket_id": self.id,
            "type": move_type,
            "amount_signed": dpp_amount,
            "tax_amount": tax_amount if move_type == "topup" else 0.0,
            "gross_amount": (
                gross_amount if gross_amount is not None else dpp_amount + (tax_amount if move_type == "topup" else 0.0)
            ),
            "balance_after": new_balance,
            "ref": reason,
            "move_id": move.id,
            "state": "posted",
        }
        if (
            extras.get("ppob_transaction_id") is not None
            and "ppob_transaction_id" in self.env["custom.ppob.provider.bucket.move"]._fields
        ):
            vals["ppob_transaction_id"] = extras["ppob_transaction_id"]
        bucket_move = self.env["custom.ppob.provider.bucket.move"].create(vals)
        self.env.cr.execute(
            "UPDATE custom_ppob_provider_bucket SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance", "display_name"])
        self.modified(["balance"])
        return bucket_move

    def _atomic_credit_from_move(
        self, move, *, dpp_amount, tax_amount=0.0, gross_amount=None, ref=None, move_type="topup"
    ):
        """Sister of ``_atomic_credit`` for callers that already created the
        ``account.move``. Skips ``_post_journal`` (caller is responsible for
        posting) but still records the subledger row + balance update under a
        row-level lock.

        Idempotency: if a bucket move with the same ``move_id`` already exists,
        return it unchanged.
        """
        self.ensure_one()
        existing = self.env["custom.ppob.provider.bucket.move"].search(
            [("move_id", "=", move.id)],
            limit=1,
        )
        if existing:
            return existing
        if dpp_amount <= 0:
            raise UserError(_("Credit amount must be positive."))
        fresh = self._lock()
        new_balance = fresh + dpp_amount
        bucket_move = self.env["custom.ppob.provider.bucket.move"].create(
            {
                "bucket_id": self.id,
                "type": move_type,
                "amount_signed": dpp_amount,
                "tax_amount": tax_amount,
                "gross_amount": (gross_amount if gross_amount is not None else dpp_amount + tax_amount),
                "balance_after": new_balance,
                "ref": ref or move.ref or move.name or "",
                "move_id": move.id,
                "state": "posted",
            }
        )
        self.env.cr.execute(
            "UPDATE custom_ppob_provider_bucket SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance", "display_name"])
        self.modified(["balance"])
        return bucket_move

    def _atomic_debit(self, amount, reason, counterpart_account, **extras):
        self.ensure_one()
        if amount <= 0:
            raise UserError(_("Debit amount must be positive."))
        fresh = self._lock()
        if amount > fresh:
            raise UserError(
                _("Insufficient bucket balance for %(b)s. Requested: %(req).2f. Available: %(avail).2f.")
                % {"b": self.display_name, "req": amount, "avail": fresh}
            )
        move_type = extras.pop("move_type", "usage")
        move = self._post_journal(amount, 0.0, "debit_bucket", reason, counterpart_account)
        new_balance = fresh - amount
        vals = {
            "bucket_id": self.id,
            "type": move_type,
            "amount_signed": -amount,
            "balance_after": new_balance,
            "ref": reason,
            "move_id": move.id,
            "state": "posted",
        }
        if (
            extras.get("ppob_transaction_id") is not None
            and "ppob_transaction_id" in self.env["custom.ppob.provider.bucket.move"]._fields
        ):
            vals["ppob_transaction_id"] = extras["ppob_transaction_id"]
        bucket_move = self.env["custom.ppob.provider.bucket.move"].create(vals)
        self.env.cr.execute(
            "UPDATE custom_ppob_provider_bucket SET balance = %s WHERE id = %s",
            (new_balance, self.id),
        )
        self.invalidate_recordset(["balance", "display_name"])
        self.modified(["balance"])
        return bucket_move

    def action_recompute_balance(self):
        for bucket in self:
            total = sum(m.amount_signed for m in bucket.move_ids if m.state == "posted")
            bucket.env.cr.execute(
                "UPDATE custom_ppob_provider_bucket SET balance = %s WHERE id = %s",
                (total, bucket.id),
            )
            bucket.invalidate_recordset(["balance", "display_name"])
        return True
