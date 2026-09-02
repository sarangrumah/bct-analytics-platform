# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    custom_scrap_batch_id = fields.Many2one(
        comodel_name="custom.scrap.batch",
        string="Scrap Batch",
        index=True,
        ondelete="set null",
        copy=False,
    )


class CustomScrapBatch(models.Model):
    """Scrap several products in one action and post ONE consolidated valuation
    journal.

    This build has no location-level valuation accounts, so core ``stock.scrap``
    reduces the stock valuation layer but posts NO ``account.move`` (same gap the
    GR journal works around). Each line here drives a real ``stock.scrap`` — so
    on-hand quantity and the SVL stay correct — and once all lines are scrapped a
    single ``account.move`` is booked:

        Dr Scrap Loss (P&L)                      total
            Cr Stock Valuation (per category)        per-account subtotal

    mirroring ``stock.move._retail_book_valuation_entry`` (real-time categories
    only, OU analytic stamped, idempotent by ``ref`` + ``move_id``).
    """

    _name = "custom.scrap.batch"
    _description = "Scrap Batch (Multiple Products)"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date desc, id desc"

    name = fields.Char(
        string="Reference",
        default=lambda self: self.env._("New"),
        copy=False,
        readonly=True,
    )
    company_id = fields.Many2one(
        comodel_name="res.company",
        default=lambda self: self.env.company,
        required=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    date = fields.Date(
        default=fields.Date.context_today,
        required=True,
        tracking=True,
    )
    warehouse_id = fields.Many2one(
        comodel_name="stock.warehouse",
        string="Warehouse",
        required=True,
        tracking=True,
        help="Store being scrapped from. Drives the source location and the "
        "Operating-Unit analytic stamped on the journal.",
    )
    location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Source Location",
        domain="[('usage', '=', 'internal'), ('company_id', 'in', (company_id, False))]",
        help="Internal location the products are removed from.",
    )
    scrap_location_id = fields.Many2one(
        comodel_name="stock.location",
        string="Scrap Location",
        domain="[('usage', '=', 'inventory'), ('company_id', 'in', (company_id, False))]",
    )
    origin = fields.Char(string="Source Document")
    reason_tag_ids = fields.Many2many(
        comodel_name="stock.scrap.reason.tag",
        string="Reasons",
    )
    state = fields.Selection(
        selection=[
            ("draft", "Draft"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        copy=False,
    )
    line_ids = fields.One2many(
        comodel_name="custom.scrap.batch.line",
        inverse_name="batch_id",
        string="Products",
        copy=True,
    )
    scrap_ids = fields.One2many(
        comodel_name="stock.scrap",
        inverse_name="custom_scrap_batch_id",
        string="Scraps",
        copy=False,
    )
    scrap_count = fields.Integer(compute="_compute_counts")
    move_id = fields.Many2one(
        comodel_name="account.move",
        string="Journal Entry",
        readonly=True,
        copy=False,
    )
    amount_total = fields.Monetary(
        compute="_compute_amount_total",
        currency_field="currency_id",
    )

    _JOURNAL_REF = "SCRAP-VAL:%s"  # per batch, for idempotency
    _SCRAP_LOSS_PARAM = "custom_retail_localization.scrap_loss_account_code"

    # ------------------------------------------------------------------
    # Defaults / computes
    # ------------------------------------------------------------------
    @api.onchange("warehouse_id")
    def _onchange_warehouse_id(self):
        if self.warehouse_id:
            self.location_id = self.warehouse_id.lot_stock_id
            if not self.scrap_location_id:
                self.scrap_location_id = self.env["stock.location"].search(
                    [
                        ("usage", "=", "inventory"),
                        ("company_id", "in", (self.company_id.id, False)),
                    ],
                    limit=1,
                )

    @api.depends("scrap_ids")
    def _compute_counts(self):
        for rec in self:
            rec.scrap_count = len(rec.scrap_ids)

    @api.depends("line_ids.value")
    def _compute_amount_total(self):
        for rec in self:
            rec.amount_total = sum(rec.line_ids.mapped("value"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("custom.scrap.batch") or _("New")
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_except_done(self):
        if any(rec.state == "done" for rec in self):
            raise UserError(_("You cannot delete a scrap batch that has been validated."))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def action_validate(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only a draft scrap batch can be validated."))
        if self.move_id:
            return self.action_view_move()
        if not self.line_ids:
            raise UserError(_("Add at least one product line to scrap."))
        if not self.location_id or not self.scrap_location_id:
            raise UserError(_("Set both the source and the scrap location."))

        Scrap = self.env["stock.scrap"]
        for line in self.line_ids:
            if line.scrap_qty <= 0:
                raise UserError(_("Quantity for '%s' must be positive.", line.product_id.display_name))
            scrap = Scrap.create(line._prepare_scrap_vals())
            result = scrap.action_validate()
            if isinstance(result, dict):
                # action_validate returned the insufficient-qty wizard action.
                scrap.unlink()
                raise UserError(
                    _(
                        "Not enough on-hand quantity to scrap '%(product)s' (%(qty)s %(uom)s) at %(loc)s.",
                        product=line.product_id.display_name,
                        qty=line.scrap_qty,
                        uom=line.product_uom_id.name,
                        loc=self.location_id.display_name,
                    )
                )
            line.scrap_id = scrap.id

        self._post_scrap_journal()
        self.state = "done"
        self.message_post(
            body=_(
                "Scrap batch validated: %(n)s product(s) scrapped, journal %(je)s.",
                n=len(self.line_ids),
                je=self.move_id.name if self.move_id else _("none (no valued lines)"),
            )
        )
        return True

    def _scrap_loss_account(self, company):
        """Resolve the P&L scrap-loss account for ``company`` from the configured
        account code (company-dependent). Returns an ``account.account`` or raises.
        """
        code = self.env["ir.config_parameter"].sudo().get_param(self._SCRAP_LOSS_PARAM, "").strip()
        if not code:
            raise UserError(
                _(
                    "No scrap-loss account is configured. Set the system parameter "
                    "'%s' to the P&L account code used for scrap losses.",
                    self._SCRAP_LOSS_PARAM,
                )
            )
        account = self.env["account.account"].with_company(company).search([("code", "=", code)], limit=1)
        if not account:
            raise UserError(
                _(
                    "Scrap-loss account code '%(code)s' was not found in company %(company)s.",
                    code=code,
                    company=company.display_name,
                )
            )
        return account

    def _post_scrap_journal(self):
        """Book ONE consolidated valuation entry for the done scrap moves.

        Cr the stock-valuation account of each product's category (grouped),
        Dr the scrap-loss account for the total. Real-time categories only;
        zero-value moves are skipped. Idempotent by ``ref`` + ``move_id``.
        """
        self.ensure_one()
        company = self.company_id
        currency = company.currency_id
        AccountMove = self.env["account.move"]
        ref = self._JOURNAL_REF % self.id
        if self.move_id or AccountMove.search_count([("ref", "=", ref), ("company_id", "=", company.id)]):
            return

        # Sum the scrapped value per stock-valuation account (real-time only).
        value_by_account = {}  # account -> amount (positive magnitude)
        journal = company.account_stock_journal_id
        for scrap in self.scrap_ids:
            categ = scrap.product_id.categ_id.with_company(company)
            if categ.property_valuation != "real_time":
                continue
            val_acc = categ.property_stock_valuation_account_id
            if not val_acc:
                continue
            journal = journal or categ.property_stock_journal
            for move in scrap.move_ids.filtered(lambda m: m.state == "done"):
                amount = abs(move.value)
                if not amount or currency.is_zero(amount):
                    continue
                value_by_account[val_acc] = value_by_account.get(val_acc, 0.0) + amount

        total = sum(value_by_account.values())
        if not total or currency.is_zero(total):
            return  # nothing valued to post (physical scrap still happened)
        if not journal:
            raise UserError(_("No stock journal is configured on the company to post the scrap."))

        scrap_loss_acc = self._scrap_loss_account(company)
        ou = self.warehouse_id.l10n_ou_analytic_id
        analytic = {str(ou.id): 100.0} if ou else False
        label = _("Scrap %s", self.name)

        line_ids = [
            # Dr Scrap Loss (aggregate)
            (
                0,
                0,
                {
                    "account_id": scrap_loss_acc.id,
                    "name": label,
                    "debit": total,
                    "credit": 0.0,
                    "analytic_distribution": analytic,
                },
            )
        ]
        # Cr Stock Valuation per category account
        for val_acc, amount in value_by_account.items():
            line_ids.append(
                (
                    0,
                    0,
                    {
                        "account_id": val_acc.id,
                        "name": label,
                        "debit": 0.0,
                        "credit": amount,
                        "analytic_distribution": analytic,
                    },
                )
            )

        move = AccountMove.create(
            {
                "move_type": "entry",
                "journal_id": journal.id,
                "company_id": company.id,
                "date": self.date,
                "ref": ref,
                "line_ids": line_ids,
            }
        )
        move.action_post()
        self.move_id = move.id

    def action_cancel(self):
        for rec in self:
            if rec.state == "done":
                raise UserError(
                    _(
                        "%s is validated — scrap moves and the journal are already "
                        "posted and cannot be cancelled here.",
                        rec.name,
                    )
                )
            rec.state = "cancel"
        return True

    def action_draft(self):
        for rec in self:
            if rec.state == "cancel":
                rec.state = "draft"
        return True

    # ------------------------------------------------------------------
    # Smart buttons
    # ------------------------------------------------------------------
    def action_view_scraps(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.scrap",
            "name": _("Scraps"),
            "view_mode": "list,form",
            "domain": [("id", "in", self.scrap_ids.ids)],
        }

    def action_view_move(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "target": "current",
        }


class CustomScrapBatchLine(models.Model):
    _name = "custom.scrap.batch.line"
    _description = "Scrap Batch Line"

    batch_id = fields.Many2one(
        comodel_name="custom.scrap.batch",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(related="batch_id.company_id", store=True)
    currency_id = fields.Many2one(related="batch_id.currency_id")
    product_id = fields.Many2one(
        comodel_name="product.product",
        string="Product",
        required=True,
        domain="[('is_storable', '=', True)]",
    )
    product_uom_id = fields.Many2one(
        comodel_name="uom.uom",
        string="Unit",
        compute="_compute_product_uom_id",
        store=True,
        readonly=False,
        precompute=True,
    )
    scrap_qty = fields.Float(
        string="Quantity",
        default=1.0,
        digits="Product Unit",
        required=True,
    )
    lot_id = fields.Many2one(
        comodel_name="stock.lot",
        string="Lot/Serial",
        domain="[('product_id', '=', product_id)]",
    )
    scrap_id = fields.Many2one(
        comodel_name="stock.scrap",
        string="Scrap",
        readonly=True,
        copy=False,
    )
    value = fields.Monetary(
        compute="_compute_value",
        currency_field="currency_id",
        help="Inventory value scrapped, read from the scrap move once validated.",
    )

    @api.depends("product_id")
    def _compute_product_uom_id(self):
        for line in self:
            line.product_uom_id = line.product_id.uom_id

    @api.depends("scrap_id.move_ids.value", "scrap_id.state")
    def _compute_value(self):
        for line in self:
            moves = line.scrap_id.move_ids.filtered(lambda m: m.state == "done")
            line.value = sum(abs(m.value) for m in moves)

    @api.constrains("scrap_qty")
    def _check_scrap_qty(self):
        for line in self:
            if line.scrap_qty <= 0:
                raise ValidationError(_("Scrap quantity must be positive."))

    def _prepare_scrap_vals(self):
        self.ensure_one()
        batch = self.batch_id
        return {
            "product_id": self.product_id.id,
            "product_uom_id": self.product_uom_id.id,
            "scrap_qty": self.scrap_qty,
            "lot_id": self.lot_id.id,
            "location_id": batch.location_id.id,
            "scrap_location_id": batch.scrap_location_id.id,
            "company_id": batch.company_id.id,
            "origin": batch.origin or batch.name,
            "scrap_reason_tag_ids": [(6, 0, batch.reason_tag_ids.ids)],
            "custom_scrap_batch_id": batch.id,
        }
