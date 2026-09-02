"""Build the tenant/the tenant fixed-asset register from the client's begbal sheet.

Source data: the ``Aset Tetap`` sheet of ``TB & Detail the tenant the tenant 05 2026``
(4-Aug-2026), parsed by ``tools/parse_tenant_asset_sheet.py`` into
``data/asset_register_registered.csv`` and
``data/asset_register_unregistered.csv``. It supersedes the PO-derived register
(uniform 30-Jan-2025 acquisition date, one asset group), which is kept as
``data/aim_asset_register.csv`` for reference only.

Two populations:

* ``Registered Asset`` -- 3,180 the tenant units, per-unit acquisition date and cost,
  straight line over 48 months. They are what the tenant opening balance already
  carries: cost 27,110,131,391 (``1205104000``) and accumulated depreciation
  6,776,493,895 (``1205203000``). So this loader posts **no** acquisition
  journal; it seeds every depreciation line dated on/before ``POSTED_THROUGH``
  as ``posted=True, move_id=False`` -- counted in accumulated depreciation,
  never re-posted to the GL, never picked up by the monthly cron.
* ``Unregistered`` -- 144 the tenant spares (the units reclassed to Office Supplies on
  31-May-2026) and 266 the tenant support items. Neither appears in any GL balance, so
  they are register-only: ``depreciation_method='none'``, no schedule.

Idempotent: skips the load entirely if any ``custom.fixed.asset`` already
exists. Companies are resolved by NAME so the module stays portable across
trn_tenant_begbal / prd_tenant.

To rebuild a database that already has a register (which requires deleting the
old one), use ``scripts/tenants/tenant/rebuild_asset_register.py`` -- that is a
deliberate, backed-up operation, not something an install hook should do.
"""

import csv
import logging
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo.exceptions import UserError
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

MODULE = "custom_asset_register_seed"
REGISTERED_CSV = "data/asset_register_registered.csv"
UNREGISTERED_CSV = "data/asset_register_unregistered.csv"

TENANT_COMPANY = "PT Aero Inovasi Media"
TENANT_COMPANY = "PT Aero Reksa Kreasi Angkasa"
OWNER_TO_COMPANY = {"PT the tenant": TENANT_COMPANY, "the tenant": TENANT_COMPANY}

# the tenant chart codes (same numbering as the 10-digit chart); the tenant has its own
# accounts under the same codes.
COST_CODE = "1205104000"
ACCUM_CODE = "1205203000"
EXPENSE_CODE = "7204103000"
LOCATION_NAME = "RUKO GUDANG PALEM"

# One asset group per category on the sheet. All three share the same GL
# accounts -- the split exists so the register reports by category.
GROUP_CODES = {
    "Device": "FA-DEVICE",
    "Komponen Drone": "FA-KOMPONEN",
    "Alat Pendukung": "FA-PENDUKUNG",
}
DEFAULT_LIFE_MONTHS = 48

OPEN_DATE = date(2026, 5, 31)  # begbal cutover
POSTED_THROUGH = OPEN_DATE  # months already charged to the GL
TECHNICAL_JOURNALS = ("EXCH", "CABA", "STJ")
JOURNAL_PREFERENCE = ("MISC", "JM")

BATCH = 250


def _company(env, name):
    return env["res.company"].search([("name", "=", name)], limit=1)


def _acc(env, company, code):
    return (
        env["account.account"]
        .with_company(company)
        .search([("code", "=", code), ("company_ids", "in", company.id)], limit=1)
    )


def _journal(env, company):
    journals = env["account.journal"].search([("type", "=", "general"), ("company_id", "=", company.id)])
    candidates = journals.filtered(lambda journal: journal.code not in TECHNICAL_JOURNALS)
    for code in JOURNAL_PREFERENCE:
        match = candidates.filtered(lambda journal, code=code: journal.code == code)
        if match:
            return match[0]
    return candidates[:1]


def _read_csv(relpath):
    with file_open(f"{MODULE}/{relpath}", "r") as handle:
        return list(csv.DictReader(handle))


def _accounts_for(env, company):
    accounts = {
        "cost": _acc(env, company, COST_CODE),
        "accum": _acc(env, company, ACCUM_CODE),
        "expense": _acc(env, company, EXPENSE_CODE),
    }
    missing = [key for key, account in accounts.items() if not account]
    journal = _journal(env, company)
    if missing or not journal:
        raise UserError(
            f"{MODULE}: {company.name} is missing accounts {missing} / general journal -> cannot load register"
        )
    return accounts, journal


def _ensure_group(env, company, category, accounts, journal):
    """Upsert one asset group per category. Only empty fields are filled."""
    Group = env["custom.fixed.asset.group"].with_company(company)
    code = GROUP_CODES[category]
    vals = {
        "default_asset_account_id": accounts["cost"].id,
        "default_depreciation_account_id": accounts["accum"].id,
        "default_expense_account_id": accounts["expense"].id,
        "default_journal_id": journal.id,
        "default_useful_life_months": DEFAULT_LIFE_MONTHS,
    }
    group = Group.with_context(active_test=False).search(
        [("code", "=", code), ("company_id", "=", company.id)], limit=1
    )
    if group:
        group.write({field: value for field, value in vals.items() if value and not group[field]})
        return group
    return Group.create({"name": category, "code": code, "company_id": company.id, **vals})


def _location(env):
    Location = env["custom.fixed.asset.location"]
    location = Location.with_context(active_test=False).search([("name", "=", LOCATION_NAME)], limit=1)
    return location or Location.create({"name": LOCATION_NAME})


def seed_group_and_location(env):
    """Seed the asset groups + location for both companies (called on upgrade).

    Idempotent and non-destructive: creates what is missing and fills only empty
    account/journal fields, so the wiring self-heals without touching data.
    """
    seeded = []
    for company_name, categories in (
        (TENANT_COMPANY, ("Device", "Komponen Drone")),
        (TENANT_COMPANY, ("Alat Pendukung",)),
    ):
        company = _company(env, company_name)
        if not company:
            _logger.warning("%s: company %r not found -> skip group seed", MODULE, company_name)
            continue
        accounts, journal = _accounts_for(env, company)
        for category in categories:
            seeded.append(_ensure_group(env, company, category, accounts, journal).code)
    location = _location(env)
    _logger.info("%s: seeded groups %s + location %s", MODULE, seeded, location.name)
    return seeded, location


def _elapsed_months(start, until=POSTED_THROUGH):
    """Number of monthly depreciation lines dated on/before `until`."""
    if start > until:
        return 0
    return (until.year - start.year) * 12 + (until.month - start.month) + 1


def _asset_vals(row, company, group, location, accounts, journal):
    """Map one CSV row to ``custom.fixed.asset`` values."""
    acquisition = date.fromisoformat(row["acq_date"])
    cost = float(row["cost"] or 0)
    vals = {
        "name": row["name"],
        "code": row["code"],
        "company_id": company.id,
        "group_id": group.id,
        "location_id": location.id if location else False,
        "acquisition_date": acquisition,
        "acquisition_value": cost,
        "asset_account_id": accounts["cost"].id,
        "depreciation_account_id": accounts["accum"].id,
        "expense_account_id": accounts["expense"].id,
        "journal_id": journal.id,
        "depreciation_method": "none",
        "useful_life_months": 0,
    }
    if row["status"] != "Registered Asset" or cost <= 0:
        return vals

    # Depreciation starts in the month of acquisition -- the GL shows the first
    # charge in Jun-2025 for the 18-Jun-2025 fleet. Total life = months already
    # elapsed at the cutover + the remaining life the client computed (48 for
    # every unit on the sheet).
    start = acquisition + relativedelta(day=31)
    vals.update(
        {
            "posting_date": start,
            "depreciation_date_mode": "specific",
            "depreciation_method": "straight_line",
            "useful_life_months": _elapsed_months(start) + int(row["remaining_life"] or 0),
        }
    )
    return vals


def load_register(env, rows, posted_through=POSTED_THROUGH):
    """Create the register records and seed their already-charged history.

    Returns ``(created, seeded_lines)``.
    """
    by_company = {}
    for row in rows:
        by_company.setdefault(OWNER_TO_COMPANY[row["owner"]], []).append(row)

    created = seeded_lines = 0
    for company_name, company_rows in by_company.items():
        company = _company(env, company_name)
        if not company:
            _logger.warning("%s: company %r not found -> skip %s rows", MODULE, company_name, len(company_rows))
            continue
        accounts, journal = _accounts_for(env, company)
        location = _location(env) if company_name == TENANT_COMPANY else False

        groups, vals_list = {}, []
        for row in company_rows:
            category = row["category"]
            if category not in groups:
                groups[category] = _ensure_group(env, company, category, accounts, journal)
            vals_list.append(_asset_vals(row, company, groups[category], location, accounts, journal))

        Asset = env["custom.fixed.asset"].with_company(company)
        for start in range(0, len(vals_list), BATCH):
            assets = Asset.create(vals_list[start : start + BATCH])
            depreciable = assets.filtered(lambda asset: asset.depreciation_method != "none")
            if depreciable:
                depreciable.action_confirm()
                history = depreciable.depreciation_line_ids.filtered(lambda line: line.date <= posted_through)
                if history:
                    history.write({"posted": True})
                    seeded_lines += len(history)
            created += len(assets)
            env.invalidate_all()
        _logger.info("%s: %s -> %s assets", MODULE, company_name, len(vals_list))

    return created, seeded_lines


def read_register_rows():
    """The full register population: registered units first, then register-only."""
    return _read_csv(REGISTERED_CSV) + _read_csv(UNREGISTERED_CSV)


def post_init_hook(env):
    seed_group_and_location(env)
    if env["custom.fixed.asset"].with_context(active_test=False).search_count([]):
        _logger.info("%s: fixed assets already exist -> skip register load (idempotent)", MODULE)
        return
    created, seeded_lines = load_register(env, read_register_rows())
    _logger.info(
        "%s: created %s assets, seeded %s already-charged depreciation lines",
        MODULE,
        created,
        seeded_lines,
    )
