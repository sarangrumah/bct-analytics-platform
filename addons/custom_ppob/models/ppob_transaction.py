# Part of custom_ppob. Licence: LGPL-3.
"""The PPOB transaction fact.

One row is one bill payment or top-up: a customer hands over money at a counter or in an app, the
platform forwards it to a biller, and the biller either settles it or does not. The row is the
source of ``fct_ppob_transaction`` in the warehouse, so its state machine and its timestamps are
the parts that matter most - dbt asserts ``accepted_values`` on ``state``.

State machine
-------------
::

    draft ──submit──> pending ──succeed──> success ──reverse──> reversed
                          │
                          └────fail─────> failed

Only those five transitions exist. They are enforced in ``write()`` rather than only in the action
methods, because a warehouse that trusts ``state`` has to be able to trust that nothing wrote a
value straight into the column.
"""

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.custom_operating_unit.models.operating_unit_mixin import operating_unit_field

PPOB_STATES = [
    ("draft", "Draft"),
    ("pending", "Pending"),
    ("success", "Success"),
    ("failed", "Failed"),
    ("reversed", "Reversed"),
]

#: The only legal state transitions. Anything else raises.
PPOB_TRANSITIONS = {
    "draft": {"pending"},
    "pending": {"success", "failed"},
    "success": {"reversed"},
    "failed": set(),
    "reversed": set(),
}

#: States in which the transaction is finished and its financial figures are frozen.
PPOB_TERMINAL_STATES = ("success", "failed", "reversed")


class PpobTransaction(models.Model):
    _name = "ppob.transaction"
    _description = "PPOB Transaction"
    _inherit = ["pdp.masked.mixin"]
    _order = "requested_at desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Reference",
        required=True,
        copy=False,
        readonly=True,
        index=True,
        default=lambda self: _("New"),
    )
    partner_id = fields.Many2one("res.partner", string="Customer", index=True)
    biller_id = fields.Many2one("ppob.biller", required=True, index=True, ondelete="restrict")
    product_id = fields.Many2one(
        "product.product",
        string="Product",
        index=True,
        ondelete="restrict",
        help="The saleable denomination or bill type, so PPOB joins dim_product like every other "
        "revenue line.",
    )
    operating_unit_id = operating_unit_field()
    company_id = fields.Many2one(
        "res.company", required=True, index=True, default=lambda self: self.env.company
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )

    amount = fields.Monetary(
        string="Bill Amount",
        currency_field="currency_id",
        help="What the biller is owed. Excludes the admin fee.",
    )
    admin_fee = fields.Monetary(
        currency_field="currency_id", help="Charged to the customer on top of the bill amount."
    )
    commission = fields.Monetary(
        currency_field="currency_id",
        help="The share of the admin fee retained as revenue. The rest is the biller's or the "
        "channel's.",
    )
    total_amount = fields.Monetary(
        compute="_compute_total_amount",
        store=True,
        currency_field="currency_id",
        help="amount + admin_fee: what the customer actually paid.",
    )

    customer_ref = fields.Char(
        string="Customer Reference",
        index=True,
        help="Subscriber, meter or policy number. Classified `sensitive` in custom_pdp_core: "
        "hashed before it reaches the warehouse, masked in the UI for non-viewers.",
    )
    customer_name = fields.Char(
        help="Name returned by the biller inquiry. Classified `personal`.",
    )

    state = fields.Selection(
        PPOB_STATES, required=True, default="draft", index=True, copy=False
    )
    requested_at = fields.Datetime(
        required=True, default=fields.Datetime.now, index=True, copy=False
    )
    settled_at = fields.Datetime(readonly=True, copy=False, index=True)
    sla_seconds = fields.Integer(
        string="SLA (s)",
        compute="_compute_sla_seconds",
        store=True,
        index=True,
        help="Whole seconds between requested_at and settled_at. Null while the transaction is "
        "still open.",
    )
    sla_breached = fields.Boolean(compute="_compute_sla_seconds", store=True)
    failure_reason = fields.Text(copy=False)
    biller_reference = fields.Char(
        string="Biller Reference",
        copy=False,
        help="The biller's own settlement identifier, for reconciliation.",
    )

    #: `name` is a system-generated sequence, not a person's name; masking it would only make the
    #: UI unusable. `customer_name` and `customer_ref` are the ones that get masked.
    _pdp_ui_mask_exclude = ("name",)
    _pdp_ui_mask_companions = {}

    _amounts_non_negative = models.Constraint(
        "CHECK (amount >= 0 AND admin_fee >= 0 AND commission >= 0)",
        "PPOB amounts cannot be negative. Use the 'reversed' state to undo a settlement.",
    )
    _commission_within_admin_fee = models.Constraint(
        "CHECK (commission <= admin_fee)",
        "Commission cannot exceed the admin fee it is taken from.",
    )
    _name_uniq = models.Constraint(
        "UNIQUE (name)",
        "A PPOB transaction reference must be unique.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------

    @api.depends("amount", "admin_fee")
    def _compute_total_amount(self):
        for txn in self:
            txn.total_amount = (txn.amount or 0.0) + (txn.admin_fee or 0.0)

    @api.depends("requested_at", "settled_at", "biller_id.sla_target_seconds")
    def _compute_sla_seconds(self):
        for txn in self:
            if txn.requested_at and txn.settled_at:
                delta = txn.settled_at - txn.requested_at
                txn.sla_seconds = int(delta.total_seconds())
                target = txn.biller_id.sla_target_seconds or 0
                txn.sla_breached = bool(target) and txn.sla_seconds > target
            else:
                txn.sla_seconds = 0
                txn.sla_breached = False

    @api.constrains("settled_at", "requested_at")
    def _check_settlement_order(self):
        for txn in self:
            if txn.settled_at and txn.requested_at and txn.settled_at < txn.requested_at:
                raise ValidationError(
                    _("A PPOB transaction cannot settle before it was requested.")
                )

    @api.constrains("operating_unit_id", "company_id")
    def _check_operating_unit_company(self):
        for txn in self:
            unit = txn.operating_unit_id
            if unit and unit.company_id != txn.company_id:
                raise ValidationError(
                    _(
                        "Operating Unit %(unit)s belongs to another company than this "
                        "transaction.",
                        unit=unit.display_name,
                    )
                )

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    @api.model
    def _assert_transition(self, source, target):
        if source == target:
            return
        if target not in PPOB_TRANSITIONS.get(source, set()):
            allowed = sorted(PPOB_TRANSITIONS.get(source, set())) or ["(none)"]
            raise UserError(
                _(
                    "Illegal PPOB state transition %(source)s -> %(target)s. "
                    "From %(source)s the only legal next states are: %(allowed)s.",
                    source=source,
                    target=target,
                    allowed=", ".join(allowed),
                )
            )

    def write(self, vals):
        if "state" in vals:
            target = vals["state"]
            for txn in self:
                self._assert_transition(txn.state, target)
        if any(key in vals for key in ("amount", "admin_fee", "commission")):
            frozen = self.filtered(lambda t: t.state in PPOB_TERMINAL_STATES)
            if frozen:
                raise UserError(
                    _(
                        "The financial figures of a %(state)s transaction are frozen (%(names)s).",
                        state=frozen[0].state,
                        names=", ".join(frozen.mapped("name")),
                    )
                )
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("state") and vals["state"] != "draft":
                # Creating straight into a later state would skip the machine entirely.
                self._assert_transition("draft", vals["state"])
            if not vals.get("name") or vals["name"] == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "ppob.transaction"
                ) or _("New")
        return super().create(vals_list)

    # -- actions --------------------------------------------------------

    def action_submit(self):
        """draft -> pending."""
        for txn in self:
            self._assert_transition(txn.state, "pending")
            if not txn.customer_ref:
                raise UserError(
                    _("%s cannot be submitted without a customer reference.", txn.name)
                )
        return self.write({"state": "pending"})

    def action_succeed(self, biller_reference=None, settled_at=None):
        """pending -> success."""
        for txn in self:
            self._assert_transition(txn.state, "success")
        values = {
            "state": "success",
            "settled_at": settled_at or fields.Datetime.now(),
            "failure_reason": False,
        }
        if biller_reference:
            values["biller_reference"] = biller_reference
        return self.write(values)

    def action_fail(self, reason=None, settled_at=None):
        """pending -> failed."""
        for txn in self:
            self._assert_transition(txn.state, "failed")
        return self.write({
            "state": "failed",
            "settled_at": settled_at or fields.Datetime.now(),
            "failure_reason": reason or _("Unspecified biller failure"),
        })

    def action_reverse(self, reason=None):
        """success -> reversed."""
        for txn in self:
            self._assert_transition(txn.state, "reversed")
        return self.write({
            "state": "reversed",
            "failure_reason": reason or _("Reversed"),
        })
