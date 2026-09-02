# -*- coding: utf-8 -*-
from odoo import _, fields, models
from odoo.exceptions import UserError


class PpobCommissionSettlementWizard(models.TransientModel):
    _name = "custom.ppob.commission.settlement.wizard"
    _description = "Close a period of commission accruals into invoices / payouts"

    date_from = fields.Date(required=True)
    date_to = fields.Date(required=True)
    scope = fields.Selection(
        selection=[
            ("provider_to_us", "Provider -> Us"),
            ("us_to_mitra", "Us -> Mitra"),
        ],
        required=True,
    )
    partner_id = fields.Many2one("res.partner", help="Leave empty to settle every partner.")
    pph_rate = fields.Float(
        default=2.0,
        help="Fallback PPh 23 rate (%) for us_to_mitra payouts when no "
        "custom.witholding.rate (pph_type 23) matches. 2% for partners "
        "with NPWP, 4% without. When a rate rule exists, the platform "
        "withholding engine decides instead.",
    )

    def action_run(self):
        self.ensure_one()
        Accrual = self.env["custom.ppob.commission.accrual"]
        domain = [
            ("scope", "=", self.scope),
            ("state", "=", "accrued"),
            ("date", ">=", self.date_from),
            ("date", "<=", self.date_to),
        ]
        if self.partner_id:
            domain.append(("partner_id", "=", self.partner_id.id))
        accruals = Accrual.search(domain)
        if not accruals:
            raise UserError(_("No matching accruals found."))

        grouped = {}
        for a in accruals:
            grouped.setdefault(a.partner_id.id, self.env["custom.ppob.commission.accrual"])
            grouped[a.partner_id.id] |= a

        created_docs = self.env["account.move"]
        for _partner_id, records in grouped.items():
            partner = records[0].partner_id
            total = sum(records.mapped("amount"))
            if self.scope == "provider_to_us":
                doc = self._create_vendor_bill(partner, records, total)
            else:
                doc = self._create_mitra_payout(partner, records, total)
            records.write({"state": "invoiced", "settlement_move_id": doc.id})
            created_docs |= doc
        return {
            "type": "ir.actions.act_window",
            "name": _("Commission Settlement Moves"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", created_docs.ids)],
        }

    def _acc(self, role):
        return self.env["custom.ppob.account.mapping"]._get_account(role, self.env.company)

    def _create_vendor_bill(self, partner, accruals, total):
        """Provider->us: create a vendor refund so the provider pays us."""
        journal = self.env["account.journal"].search(
            [("type", "=", "purchase"), ("company_id", "=", self.env.company.id)],
            limit=1,
        )
        if not journal:
            raise UserError(_("No purchase journal found."))
        income_acc = self._acc("commission_income")
        return self.env["account.move"].create(
            {
                "move_type": "in_refund",
                "journal_id": journal.id,
                "partner_id": partner.id,
                "invoice_date": self.date_to,
                "ref": f"Commission {self.date_from} - {self.date_to}",
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": f"PPOB Commission {self.date_from}..{self.date_to}",
                            "quantity": 1.0,
                            "price_unit": total,
                            "account_id": income_acc.id if income_acc else False,
                        },
                    ),
                ],
            }
        )

    def _create_mitra_payout(self, partner, accruals, total):
        """Us->mitra: create a payout JE that settles CommPayable-Mitra,
        withholds PPh 23 (via the platform engine), and nets to bank/wallet."""
        journal = self.env.ref("custom_ppob_wallet.journal_ppob_sale", raise_if_not_found=False)
        if not journal:
            raise UserError(_("PPOB Sale journal not found."))
        comm_payable = self._acc("commission_payable_mitra")
        pph_payable = self._acc("pph23_payable")
        if not (comm_payable and pph_payable):
            raise UserError(_("Commission payable / PPh 23 payable accounts not mapped."))

        # Resolve PPh 23 through the platform withholding engine; fall back to
        # the statutory 2%/4%-by-NPWP default when no rate rule is configured.
        engine = self.env["custom.witholding.engine"]
        result = engine.compute(partner, total, "23", date=self.date_to)
        if result.get("applicable_rule_id"):
            pph_amount = result["withheld"]
            rate = result["rate"]
        else:
            rate = self.pph_rate if result.get("has_npwp") else 4.0
            pph_amount = round(total * (rate / 100.0), 2)
        net = total - pph_amount

        ref = f"Commission payout {partner.display_name} {self.date_from}..{self.date_to}"
        move = self.env["account.move"].create(
            {
                "journal_id": journal.id,
                "move_type": "entry",
                "ref": ref,
                "partner_id": partner.id,
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "account_id": comm_payable.id,
                            "partner_id": partner.id,
                            "name": ref,
                            "debit": total,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": pph_payable.id,
                            "partner_id": partner.id,
                            "name": f"{ref} PPh23 {rate}%",
                            "debit": 0.0,
                            "credit": pph_amount,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "account_id": comm_payable.id,
                            "partner_id": partner.id,
                            "name": f"{ref} Net",
                            "debit": 0.0,
                            "credit": net,
                        },
                    ),
                ],
            }
        )
        move.action_post()
        return move
