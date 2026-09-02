# -*- coding: utf-8 -*-
"""Idempotent seeding for the Trade/Non-Trade + Operating-Unit feature (#9).

Provisions, per company that has the apparel brand stores:

* an "Operating Unit" analytic plan + one analytic account per store
  (``stock.warehouse``), linked via ``warehouse.l10n_ou_analytic_id``;
* a dedicated purchase journal per store, linked via
  ``warehouse.l10n_purchase_journal_id``;
* the ``retail.purchase.account.map`` rows (trade / non-trade) resolved by
  account *code* (company-dependent, read with ``with_company``).

Safe to run repeatedly: everything is guarded by the link fields / existing
records. Called from the module ``post_init_hook`` and from
``scripts/tenants/retail/40_setup_trade_ou.py``.
"""

import logging

_logger = logging.getLogger(__name__)

OU_PLAN_NAME = "Operating Unit"

# --- Bank journals (feedback Finance-AP #4/#6/#7) ---------------------------
# Resolved by company-dependent EBR account code (with_company). MB = the bank's
# main GL account (journal default account). Bank-out posts DIRECT to the bank
# account (no outstanding suspense); bank-in receipts land in the per-bank IC
# clearing (suspense) account, cleared manually by Treasury.
BANK_OUT = {"bank": "BCA", "rekening": "2687778282", "mb": "1103019320"}
BANK_IN = [
    {"bank": "BCA", "rekening": "2685151268", "mb": "1103019310", "ic": "1103019311"},
    {"bank": "Mandiri", "rekening": "1680008812008", "mb": "1103019330", "ic": "1103019331"},
    {"bank": "BNI", "rekening": "7898989920", "mb": "1103019340", "ic": "1103019341"},
    {"bank": "BRI", "rekening": "001901888305302", "mb": "1103019350", "ic": "1103019351"},
]
# Methods offered per direction (CHECK = check_printing, only if that module is
# installed; missing methods are skipped silently).
OUT_METHODS = ("manual", "bank_transfer", "giro", "check_printing")
IN_METHODS = ("manual", "bank_transfer", "giro")

# Account codes in the EBR chart of accounts (company-dependent → resolve with
# ``with_company``). Trade GR/IR stays per product category, so no code here.
ACCOUNT_CODES = {
    "trade": {"payable": "2103100001"},
    "non_trade": {"payable": "2103300001", "grir": "2103300008", "expense": "6120010001"},
}


def _find_account(env, company, code):
    return env["account.account"].with_company(company).search([("code", "=", code)], limit=1)


def _ensure_payable(account):
    """Coerce an AP control account to payable type + reconcilable."""
    if not account:
        return
    fix = {}
    if account.account_type != "liability_payable":
        fix["account_type"] = "liability_payable"
    if not account.reconcile:
        fix["reconcile"] = True
    if fix:
        _logger.info("the apparel brand Trade/OU: coercing AP account %s -> %s", account.code, fix)
        account.write(fix)


def _ensure_grir_current(account):
    """Coerce a GR/IR clearing account to current-liability + reconcilable.

    GR/IR clearing is used on vendor-bill product lines (Dr GR/IR / Cr AP) to net
    the goods-receipt accrual. Core ``_check_payable_receivable`` forbids a
    ``liability_payable`` account on an invoice line that has no due date, so a
    clearing account imported as payable blocks bill posting. It must be a plain
    current liability (it carries no due date) but stay reconcilable so the
    receipt credit and the bill debit can be matched. Returns True if changed.
    """
    if not account:
        return False
    fix = {}
    if account.account_type != "liability_current":
        fix["account_type"] = "liability_current"
    if not account.reconcile:
        fix["reconcile"] = True
    if fix:
        _logger.info("the apparel brand Trade/OU: coercing GR/IR account %s -> %s", account.code, fix)
        account.write(fix)
        return True
    return False


def _ensure_ou_plan(env):
    Plan = env["account.analytic.plan"]
    plan = Plan.search([("name", "=", OU_PLAN_NAME)], limit=1)
    if not plan:
        plan = Plan.create({"name": OU_PLAN_NAME})
    return plan


def _unique_journal_code(env, company, base):
    """Return a <=5-char purchase-journal code unique within the company."""
    Journal = env["account.journal"]
    code = base[:5]
    n = 0
    while Journal.search_count([("code", "=", code), ("company_id", "=", company.id)]):
        n += 1
        code = ("%s%d" % (base[:4], n))[:5]
    return code


def _ensure_partner_bank(env, company, acc_number):
    Bank = env["res.partner.bank"]
    partner = company.partner_id
    pb = Bank.search([("acc_number", "=", acc_number), ("partner_id", "=", partner.id)], limit=1)
    if not pb:
        pb = Bank.create({"acc_number": acc_number, "partner_id": partner.id, "company_id": company.id})
    return pb


def _ensure_method_line(env, journal, code, payment_type):
    """Ensure ``journal`` offers the payment method ``code`` for ``payment_type``.
    No-op if the method is not installed."""
    method = env["account.payment.method"].search([("code", "=", code), ("payment_type", "=", payment_type)], limit=1)
    if not method:
        return
    Line = env["account.payment.method.line"]
    line = Line.search(
        [("journal_id", "=", journal.id), ("payment_method_id", "=", method.id)],
        limit=1,
    )
    if not line:
        Line.create({"journal_id": journal.id, "payment_method_id": method.id})


def _set_all_outstanding(journal, account):
    """Point EVERY payment method line (both directions) of ``journal`` at
    ``account`` so no line silently falls back to the company default outstanding
    account. Keeps each bank journal internally consistent: the bank-out journal
    stays direct-to-bank on any line, a bank-in journal stays on its clearing
    account on any line."""
    lines = journal.inbound_payment_method_line_ids | journal.outbound_payment_method_line_ids
    for line in lines:
        if line.payment_account_id != account:
            line.payment_account_id = account.id


def _ensure_bank_journal(env, company, name, mb_account, partner_bank, base_code):
    Journal = env["account.journal"]
    journal = Journal.search(
        [
            ("type", "=", "bank"),
            ("company_id", "=", company.id),
            ("bank_account_id", "=", partner_bank.id),
            ("name", "=", name),
        ],
        limit=1,
    )
    if not journal:
        journal = Journal.create(
            {
                "name": name,
                "type": "bank",
                "code": _unique_journal_code(env, company, base_code),
                "company_id": company.id,
                "default_account_id": mb_account.id,
                "bank_account_id": partner_bank.id,
            }
        )
    return journal


def seed_bank_journals(env):
    """Create/configure the apparel brand bank journals from the EBR chart.

    Idempotent (guarded by journal name + bank account). One bank-out journal
    (direct-to-bank) and one bank-in journal per rekening (IC suspense). Runs
    from the post_init hook and ``scripts/tenants/retail/50_setup_bank_journals.py``.
    """
    companies = env["stock.warehouse"].search([]).mapped("company_id") or env.company
    made = 0
    for company in companies:
        # Bank OUT — vendor payments post DIRECT to the bank account (no suspense)
        mb = _find_account(env, company, BANK_OUT["mb"])
        if mb:
            pb = _ensure_partner_bank(env, company, BANK_OUT["rekening"])
            name = "Bank %s - %s (Out)" % (BANK_OUT["bank"], BANK_OUT["rekening"])
            journal = _ensure_bank_journal(env, company, name, mb, pb, ("O" + BANK_OUT["bank"])[:5])
            for code in OUT_METHODS:
                _ensure_method_line(env, journal, code, "outbound")
            # Direct-to-bank on EVERY line (both directions): no outstanding suspense.
            _set_all_outstanding(journal, mb)
            made += 1

        # Bank IN — receipts land in the per-bank IC clearing (suspense) account
        for entry in BANK_IN:
            mb = _find_account(env, company, entry["mb"])
            ic = _find_account(env, company, entry["ic"])
            if not mb:
                continue
            pb = _ensure_partner_bank(env, company, entry["rekening"])
            name = "Bank %s - %s" % (entry["bank"], entry["rekening"])
            journal = _ensure_bank_journal(env, company, name, mb, pb, ("I" + entry["bank"])[:5])
            for code in IN_METHODS:
                _ensure_method_line(env, journal, code, "inbound")
            # Route EVERY line (both directions) to the per-bank IC clearing
            # (suspense) account so a stray outbound line can't leak to a default.
            _set_all_outstanding(journal, ic)
            made += 1

    _logger.info("the apparel brand bank journals seeded/verified: %d", made)
    return {"bank_journals": made}


def _ensure_ho_analytic(env, company, plan):
    """Ensure a Head-Office Operating-Unit analytic for ``company``.

    Head Office is the company itself (no warehouse), so unlike the store OUs it
    is keyed on the company. Idempotent: reuses the pointer or an existing
    same-named analytic in the OU plan before creating one. Returns True if a new
    analytic was created/linked.
    """
    if company.l10n_ho_analytic_id:
        return False
    Analytic = env["account.analytic.account"]
    name = "%s - Head Office" % (company.name or "Head Office")
    analytic = Analytic.search(
        [("name", "=", name), ("plan_id", "=", plan.id), ("company_id", "=", company.id)],
        limit=1,
    ) or Analytic.create(
        {
            "name": name,
            "plan_id": plan.id,
            "company_id": company.id,
        }
    )
    company.l10n_ho_analytic_id = analytic.id
    return True


def _ensure_bill_sequence_prefix(env):
    """Force the Trade vendor-bill sequence prefix to BILL/T/EBR/... on
    already-installed DBs.

    The sequence record is ``noupdate=1`` so the XML prefix change (BILL/EBR ->
    BILL/T/EBR, symmetric with BILL/NT/EBR) does not reach existing retail DBs on
    upgrade. Fix it here idempotently; the running counter is untouched.
    """
    seq = env["ir.sequence"].sudo().search([("code", "=", "account.move.retail.bill.trade")])
    fixed = 0
    for s in seq:
        if s.prefix != "BILL/T/EBR/%(year)s/%(month)s/":
            s.prefix = "BILL/T/EBR/%(year)s/%(month)s/"
            fixed += 1
    return fixed


def seed_trade_ou(env):
    warehouses = env["stock.warehouse"].search([])
    if not warehouses:
        return
    plan = _ensure_ou_plan(env)
    Analytic = env["account.analytic.account"]
    Journal = env["account.journal"]

    _ensure_bill_sequence_prefix(env)

    made_analytic = made_journal = 0
    for idx, wh in enumerate(warehouses, start=1):
        company = wh.company_id or env.company
        wh_name = wh.display_name

        # Operating-Unit analytic account
        if not wh.l10n_ou_analytic_id:
            analytic = Analytic.search(
                [("name", "=", wh_name), ("plan_id", "=", plan.id), ("company_id", "=", company.id)],
                limit=1,
            ) or Analytic.create(
                {
                    "name": wh_name,
                    "plan_id": plan.id,
                    "company_id": company.id,
                }
            )
            wh.l10n_ou_analytic_id = analytic.id
            made_analytic += 1

        # Per-store purchase journal
        if not wh.l10n_purchase_journal_id:
            code = _unique_journal_code(env, company, wh.code or ("P%03d" % idx))
            journal = Journal.search(
                [("type", "=", "purchase"), ("company_id", "=", company.id), ("name", "=", "Pembelian - %s" % wh_name)],
                limit=1,
            ) or Journal.create(
                {
                    "name": "Pembelian - %s" % wh_name,
                    "type": "purchase",
                    "code": code,
                    "company_id": company.id,
                }
            )
            wh.l10n_purchase_journal_id = journal.id
            made_journal += 1

    # Trade / Non-Trade account mapping, one pair per company that has stores
    AccountMap = env["retail.purchase.account.map"]
    companies = warehouses.mapped("company_id") or env.company

    # Head-Office Operating Unit — the company itself (no warehouse).
    made_ho = 0
    for company in companies:
        if _ensure_ho_analytic(env, company, plan):
            made_ho += 1

    made_map = 0
    for company in companies:
        for ptype, codes in ACCOUNT_CODES.items():
            mapping = AccountMap._get_map(company, ptype)
            if not mapping:
                mapping = AccountMap.create({"company_id": company.id, "purchase_type": ptype})
                made_map += 1
            vals = {}
            if not mapping.payable_account_id:
                acc = _find_account(env, company, codes.get("payable"))
                if acc:
                    vals["payable_account_id"] = acc.id
            if not mapping.grir_account_id and codes.get("grir"):
                acc = _find_account(env, company, codes["grir"])
                if acc:
                    vals["grir_account_id"] = acc.id
            if not mapping.expense_account_id and codes.get("expense"):
                acc = _find_account(env, company, codes["expense"])
                if acc:
                    vals["expense_account_id"] = acc.id
            if vals:
                mapping.write(vals)
            # An AP control account must be payable-typed and reconcilable for
            # Odoo to use it on the vendor-bill payment-term line
            # (account.move.line._check_payable_receivable). The EBR CoA
            # designates these accounts as payable; correct the type here if the
            # import stored one as a plain current liability. Runs every time so
            # already-mapped accounts are also normalised.
            _ensure_payable(mapping.payable_account_id)

    # GR/IR clearing accounts used on vendor-bill product lines must be current
    # liabilities, not payable, or core's due-date rule blocks bill posting.
    # Non-trade uses the mapping's grir account; trade uses each real-time
    # product category's stock-variation account. The EBR CoA imported the
    # per-category trade GR/IR accounts as payable — normalise them here. Runs
    # every time so already-configured accounts are also corrected.
    made_grir = 0
    Category = env["product.category"]
    for company in companies:
        for mapping in AccountMap.search([("company_id", "=", company.id)]):
            if _ensure_grir_current(mapping.grir_account_id):
                made_grir += 1
        seen = set()
        cats = Category.with_company(company).search([("property_valuation", "=", "real_time")])
        for categ in cats:
            acc = categ.account_stock_variation_id
            if acc and acc.id not in seen:
                seen.add(acc.id)
                if _ensure_grir_current(acc):
                    made_grir += 1

    _logger.info(
        "the apparel brand Trade/OU seeding: %d analytic, %d HO, %d journals, %d mappings, %d GR/IR normalised",
        made_analytic,
        made_ho,
        made_journal,
        made_map,
        made_grir,
    )
    return {
        "analytic": made_analytic,
        "head_office": made_ho,
        "journals": made_journal,
        "mappings": made_map,
        "grir_normalised": made_grir,
    }
