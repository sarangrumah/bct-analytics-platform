# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from . import ppob_provider_adapter_base


def _default_account(env, role):
    return env["custom.ppob.account.mapping"]._get_account(role, env.company).id or False


class PpobProvider(models.Model):
    _name = "custom.ppob.provider"
    _description = "PPOB Provider"
    _order = "failover_priority, code"

    code = fields.Char(required=True, help="Short uppercase code, e.g. TSEL, PLN, DIGIFLAZZ.")
    name = fields.Char(required=True)
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        string="Vendor",
        required=True,
        domain=[("x_custom_ppob_is_provider", "=", True)],
    )
    settlement_mode = fields.Selection(
        selection=[
            ("prepaid_deposit", "Prepaid Deposit"),
            ("postpaid", "Postpaid"),
        ],
        default="prepaid_deposit",
        required=True,
    )
    status = fields.Selection(
        selection=[
            ("active", "Active"),
            ("maintenance", "Maintenance"),
            ("disabled", "Disabled"),
        ],
        default="active",
        required=True,
    )
    failover_priority = fields.Integer(
        default=100,
        help="Lower number = preferred. Used when routing a transaction across providers that map to the same SKU.",
    )
    stale_threshold_minutes = fields.Integer(
        string="Stale Threshold (minutes)",
        default=10,
        required=True,
        help="Reaper considers a transaction stale if it has been in "
        "state=in_progress for longer than this. Tune per provider: "
        "prepaid pulsa adapters usually respond in seconds (5-10 min is "
        "safe), while postpaid PLN / PDAM may legitimately take 15-30 "
        "minutes. The cron uses MAX(this, 1) to avoid reaping mid-call. "
        "Set higher on flaky providers; never set to 0.",
    )
    bucket_mode = fields.Selection(
        selection=[
            ("fixed_denom", "Fixed Denom (per product)"),
            ("bulky", "Bulky (single bucket for all products)"),
        ],
        default="bulky",
        required=True,
        help="Determines how prepaid deposit is partitioned. fixed_denom "
        "creates one bucket per SKU; bulky uses a single bucket for all "
        "products of the provider.",
    )
    tax_rate_topup = fields.Float(
        string="Topup Tax Rate",
        default=0.11,
        help="VAT rate applied to the gross topup amount. The Input VAT is "
        "posted to the Input VAT account; only the ex-tax DPP grows the "
        "bucket balance.",
    )
    bucket_inventory_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Bucket Inventory Account",
        domain="[('account_type', '=', 'asset_current')]",
        default=lambda self: _default_account(self.env, "provider_deposit_default"),
        help="Default asset account used when auto-creating buckets.",
    )
    input_vat_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Input VAT (PPN Masukan) Account",
        domain="[('account_type', '=', 'asset_current')]",
        default=lambda self: _default_account(self.env, "ppn_masukan"),
        help="Account credited with the PPN portion of every topup.",
    )
    ap_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Accounts Payable",
        domain="[('account_type', '=', 'liability_payable')]",
        help="Used for postpaid settlement mode.",
    )
    journal_id = fields.Many2one(
        comodel_name="account.journal",
        string="Provider Journal",
        required=True,
        domain="[('type', '=', 'general')]",
        default=lambda self: self.env.ref("custom_ppob_provider.journal_ppob_provider", raise_if_not_found=False),
    )
    endpoint_url = fields.Char(
        help="Fallback base URL when no Adapter Config is linked.",
    )
    credential_ref = fields.Char(
        help="Fallback ir.config_parameter key holding the API secret, used "
        "only when no Adapter Config is linked. Never paste the secret here.",
    )
    adapter_config_id = fields.Many2one(
        comodel_name="custom.adapter.config",
        string="Adapter Config",
        help="Preferred source of base URL / credential / timeout for HTTP "
        "adapters (per-tenant, from custom_adapter_framework). Adapter "
        "calls are also logged to custom.adapter.call.log when set.",
    )
    adapter_class = fields.Selection(
        selection="_adapter_selection",
        default="ppob_mock",
        required=True,
    )
    mock_outcome = fields.Selection(
        selection=[
            ("success", "Success"),
            ("fail", "Fail"),
            ("timeout", "Timeout"),
        ],
        default="success",
        help="Only used when adapter_class = ppob_mock. For QA / dev.",
    )
    bucket_ids = fields.One2many(
        comodel_name="custom.ppob.provider.bucket",
        inverse_name="provider_id",
    )
    bucket_total_balance = fields.Monetary(
        compute="_compute_bucket_total_balance",
        currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        comodel_name="res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    sku_map_ids = fields.One2many(
        comodel_name="custom.ppob.provider.sku.map",
        inverse_name="provider_id",
    )

    # ---------- DP 100% topup configuration ----------
    topup_dp_timing = fields.Selection(
        selection=[
            ("dp_post", "Bucket credit at DP bill posting"),
            ("pelunasan_post", "Bucket credit at Pelunasan posting"),
        ],
        default="dp_post",
        required=True,
        help="When does the bucket subledger receive the credit during a DP "
        "100% topup? dp_post lets the DP bill carry the bucket movement; "
        "pelunasan_post defers it to Pelunasan with a Vendor Advance asset "
        "in between.",
    )
    vendor_advance_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Vendor Advance Account",
        domain="[('account_type', '=', 'asset_current')]",
        default=lambda self: _default_account(self.env, "vendor_advance"),
        help="Required when topup_dp_timing=pelunasan_post. Holds the pre-paid "
        "amount until the Pelunasan bill recognises it as bucket inventory.",
    )
    dp_purchase_tax_id = fields.Many2one(
        comodel_name="account.tax",
        string="Purchase PPN Tax (inclusive)",
        domain="[('type_tax_use', '=', 'purchase'), ('amount_type', '=', 'percent')]",
        help="Tax applied to DP 100% bill lines. Should be a tax-inclusive PPN "
        "with repartition routing the tax amount to PPN Masukan.",
    )
    coretax_method = fields.Selection(
        selection=[
            ("dpp_nilai_lain", "DPP nilai lain (DPP = gross x dpp_factor; PPN = DPP x ppn_rate)"),
            ("gross_minus_ppn", "Gross - PPN (PPN = gross x dpp_factor x ppn_rate)"),
        ],
        default="dpp_nilai_lain",
        required=True,
        help="Coretax DPP/PPN split formula. Default mirrors PMK 131/2024 DPP "
        "nilai lain. Switch to gross_minus_ppn for jurisdictions where the "
        "deposit value equals the gross less PPN.",
    )
    dpp_factor = fields.Float(
        string="DPP Factor",
        default=11.0 / 12.0,
        digits=(16, 12),
        help="Multiplier used in the Coretax formula. PMK 131/2024 uses 11/12 "
        "(~0.916666666667). Stored at 12 decimals to preserve precision on "
        "large gross amounts.",
    )
    ppn_rate = fields.Float(
        string="PPN Rate",
        default=0.12,
        help="Statutory VAT rate. PMK 131/2024 uses 12%.",
    )
    discount_handling = fields.Selection(
        selection=[
            ("income", "Discount Received (income)"),
            ("reduce_inventory", "Reduce inventory value"),
        ],
        default="income",
        required=True,
        help="How vendor discounts on a topup are journaled. income posts the "
        "saving to a Purchase Discount Received income account and keeps "
        "inventory at face value; reduce_inventory lowers the bucket credit "
        "by the discount amount.",
    )
    discount_income_account_id = fields.Many2one(
        comodel_name="account.account",
        string="Purchase Discount Income Account",
        domain="[('account_type', '=', 'income_other')]",
        default=lambda self: _default_account(self.env, "purchase_discount_income"),
        help="Required when discount_handling=income.",
    )

    _code_uniq = models.Constraint(
        "unique(code)",
        "Provider code must be unique.",
    )

    @api.constrains(
        "topup_dp_timing",
        "vendor_advance_account_id",
        "discount_handling",
        "discount_income_account_id",
        "coretax_method",
        "dpp_factor",
        "ppn_rate",
    )
    def _check_dp_topup_config(self):
        for p in self:
            if p.topup_dp_timing == "pelunasan_post" and not p.vendor_advance_account_id:
                raise ValidationError(
                    _("Provider %s: Vendor Advance Account is required when topup_dp_timing=pelunasan_post.") % p.code
                )
            if p.dpp_factor <= 0 or p.dpp_factor > 1.0:
                raise ValidationError(_("Provider %s: dpp_factor must be in (0, 1].") % p.code)
            if p.ppn_rate < 0 or p.ppn_rate > 1.0:
                raise ValidationError(_("Provider %s: ppn_rate must be in [0, 1].") % p.code)

    def _coretax_split(self, gross):
        """Apply this provider's Coretax formula to a tax-inclusive gross.

        Both modes treat the gross amount as the buyer-paid total (DPP + PPN).
        Effective tax rate = ``dpp_factor * ppn_rate`` (PMK 131/2024 default:
        11/12 * 12% = 11%).

        Returns (dpp, ppn) where dpp = bucket inventory value = gross - ppn.
        """
        self.ensure_one()
        gross = gross or 0.0
        effective = (self.dpp_factor or 0.0) * (self.ppn_rate or 0.0)
        if self.coretax_method == "gross_minus_ppn":
            ppn = gross * effective
            dpp = gross - ppn
        else:  # dpp_nilai_lain (default; matches Odoo tax_included engine)
            dpp = gross / (1.0 + effective) if effective > 0 else gross
            ppn = gross - dpp
        return round(dpp, 2), round(ppn, 2)

    @api.model
    def _adapter_selection(self):
        return [(n, n) for n in ppob_provider_adapter_base.list_adapter_classes()]

    @api.depends("bucket_ids.balance", "bucket_ids.state")
    def _compute_bucket_total_balance(self):
        for p in self:
            p.bucket_total_balance = sum(b.balance for b in p.bucket_ids if b.state == "active")

    @api.constrains("bucket_mode")
    def _check_mode_switch_safe(self):
        for p in self:
            non_zero = p.bucket_ids.filtered(lambda b: b.state == "active" and b.balance != 0)
            mismatch = p.bucket_ids.filtered(lambda b: b.state == "active" and b.mode != p.bucket_mode)
            if non_zero and mismatch:
                raise ValidationError(
                    _(
                        "Cannot switch bucket_mode while active buckets with "
                        "non-zero balance exist. Drain or archive them first."
                    )
                )

    def _get_adapter(self):
        self.ensure_one()
        cls = ppob_provider_adapter_base.get_adapter_class(self.adapter_class)
        if not cls:
            raise UserError(_("Adapter class %s is not registered.") % self.adapter_class)
        return cls(self)

    def _resolve_bucket_for(self, product):
        """Return the active bucket that should be debited when this provider
        sells ``product``. Raises if none found."""
        self.ensure_one()
        Bucket = self.env["custom.ppob.provider.bucket"].sudo()
        domain = [
            ("provider_id", "=", self.id),
            ("state", "=", "active"),
            ("mode", "=", self.bucket_mode),
        ]
        if self.bucket_mode == "fixed_denom":
            domain.append(("product_id", "=", product.id))
        bucket = Bucket.search(domain, limit=1)
        if not bucket:
            raise UserError(
                _("No active %(mode)s bucket for provider %(prov)s / product %(prod)s.")
                % {"mode": self.bucket_mode, "prov": self.code, "prod": product.code}
            )
        return bucket

    def action_ensure_buckets(self):
        """Idempotently create the bucket(s) implied by ``bucket_mode``."""
        Bucket = self.env["custom.ppob.provider.bucket"]
        for p in self:
            if p.settlement_mode != "prepaid_deposit":
                continue
            if not p.bucket_inventory_account_id:
                raise UserError(
                    _("Set Bucket Inventory Account on provider %s before creating buckets.") % p.display_name
                )
            if p.bucket_mode == "bulky":
                existing = Bucket.search(
                    [
                        ("provider_id", "=", p.id),
                        ("mode", "=", "bulky"),
                    ],
                    limit=1,
                )
                if existing:
                    continue
                Bucket.create(
                    {
                        "provider_id": p.id,
                        "mode": "bulky",
                        "account_id": p.bucket_inventory_account_id.id,
                        "journal_id": p.journal_id.id,
                    }
                )
            else:  # fixed_denom
                products = p.sku_map_ids.filtered(
                    lambda s: s.active and s.product_id and s.product_id.denom > 0
                ).mapped("product_id")
                for product in products:
                    existing = Bucket.search(
                        [
                            ("provider_id", "=", p.id),
                            ("mode", "=", "fixed_denom"),
                            ("product_id", "=", product.id),
                        ],
                        limit=1,
                    )
                    if existing:
                        continue
                    Bucket.create(
                        {
                            "provider_id": p.id,
                            "mode": "fixed_denom",
                            "product_id": product.id,
                            "account_id": p.bucket_inventory_account_id.id,
                            "journal_id": p.journal_id.id,
                        }
                    )
        return True

    def action_test_connection(self):
        self.ensure_one()
        adapter = self._get_adapter()
        try:
            result = adapter.status("TEST-PING")
        except NotImplementedError:
            raise UserError(_("Adapter does not implement status() - cannot onboard.")) from None
        except Exception as exc:
            raise UserError(_("Test call failed: %s") % exc) from exc
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Test Connection"),
                "message": _("ok=%s, ref=%s") % (result.ok, result.provider_ref),
                "type": "success" if result.ok else "warning",
            },
        }
