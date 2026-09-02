#!/usr/bin/env python3
"""Regenerate ``data/pdp.field.classification.csv`` from a live Odoo 19 database.

This is a DEVELOPMENT tool. It is deliberately *not* imported by the module (no entry in any
``__init__.py``), so it never runs inside Odoo. Its output is committed to git; the module never
needs a database to install.

Why it exists
-------------
Contract 01 makes an unclassified column a hard failure in the CDC loader. Hand-maintaining a
classification for ~670 physical columns is exactly the sort of list that silently rots. So the
list of columns is machine-generated from ``information_schema.columns`` (the real Postgres
columns, not ``ir.model.fields`` - one2many and many2many "fields" have no column and never reach
the warehouse), and only the *decisions* are hand-written, in ``OVERRIDES`` below.

The target database MUST have all five custom modules installed, otherwise the generator refuses
to write rather than emit a seed that is silently missing a model.

Usage
-----
    python3 generate_classification_seed.py \
        --dsn "postgresql://odoo:changeme@127.0.0.1:35432/erp_dev" \
        --out ../data/pdp.field.classification.csv

Anything not named in ``OVERRIDES`` or matched by ``SUFFIX_RULES`` falls back to ``internal``.
``internal`` is the safe default here because it is *not* published and *not* dropped: it simply
means "business data, no personal content". A column that is actually personal and is left at the
default is a classification bug, which is what the module's coverage + spot-check tests exist to
catch.
"""

import argparse
import csv
import re
import sys

# --------------------------------------------------------------------------------------
# The models the warehouse reads. Extending the platform means extending this list AND
# re-running this generator.
# --------------------------------------------------------------------------------------
MODELS = [
    ("res.partner", "res_partner"),
    ("res.users", "res_users"),
    ("res.company", "res_company"),
    ("product.template", "product_template"),
    ("product.product", "product_product"),
    ("sale.order", "sale_order"),
    ("sale.order.line", "sale_order_line"),
    ("account.account", "account_account"),
    ("account.move", "account_move"),
    ("account.move.line", "account_move_line"),
    ("stock.move", "stock_move"),
    ("stock.picking", "stock_picking"),
    ("pos.order", "pos_order"),
    ("pos.order.line", "pos_order_line"),
    # Custom models. They are read from information_schema like every other model, which is why
    # this generator MUST be pointed at a database with all five custom modules installed - see
    # the module docstring. There is deliberately no hand-maintained fallback list: an earlier
    # revision had one, it drifted the moment a column was added to ppob.transaction, and the
    # coverage test caught it. One source of truth only.
    ("operating.unit", "operating_unit"),
    ("ppob.biller", "ppob_biller"),
    ("ppob.transaction", "ppob_transaction"),
]

ART_42 = "UU 27/2022 Art. 4(2) - data pribadi umum"
ART_43 = "UU 27/2022 Art. 4(3) - data pribadi spesifik"
ART_43_G = "UU 27/2022 Art. 4(3) huruf g - data pribadi lainnya"
SECRET_BASIS = "UU 27/2022 Art. 35 - kewajiban pelindungan; credential material"
INTERNAL_BASIS = "Not personal data - business record"
PUBLIC_BASIS = "Not personal data - published to counterparties"

# (pdp_class, drop_to_null, legal_basis, notes)
PERSONAL = ("personal", False, ART_42, "Direct identifier of a natural person; hashed at load so joins survive.")
SENSITIVE = ("sensitive", False, ART_43, "Specific personal data; hashed at load, never revealed.")
FREETEXT = ("sensitive", True, ART_43, "Free text - may contain any category of personal data; dropped to NULL at load.")
SECRET = ("secret", False, SECRET_BASIS, "Credential or key material; never named in the extraction SELECT list.")
INTERNAL = ("internal", False, INTERNAL_BASIS, "")
PUBLIC = ("public", False, PUBLIC_BASIS, "")

GEO = ("sensitive", True, ART_43_G, "Precise geolocation of a natural person; dropped to NULL - a digest of a coordinate has no analytic value.")

# --------------------------------------------------------------------------------------
# Hand-written decisions. Keyed "model.column".
# --------------------------------------------------------------------------------------
OVERRIDES = {
    # ---------------- res.partner : the canonical personal-data table ----------------
    "res.partner.name": PERSONAL,
    "res.partner.complete_name": PERSONAL,
    "res.partner.commercial_company_name": PERSONAL,
    "res.partner.company_name": PERSONAL,
    "res.partner.email": PERSONAL,
    "res.partner.email_normalized": PERSONAL,
    "res.partner.phone": PERSONAL,
    "res.partner.phone_sanitized": PERSONAL,
    "res.partner.street": PERSONAL,
    "res.partner.street2": PERSONAL,
    "res.partner.city": PERSONAL,
    "res.partner.zip": PERSONAL,
    "res.partner.function": PERSONAL,
    "res.partner.ref": PERSONAL,
    # company_dependent + jsonb: see COMPANY_DEPENDENT_NOTE. Hashing the whole map would
    # conflate several companies' values for one person into a single digest that identifies
    # nobody and joins to nothing, while its presence still leaks how many companies hold a
    # value for that partner. No metric in contract 03 references it.
    "res.partner.barcode": ("sensitive", True, ART_43,
        "Per-company barcode map (company_dependent, stored as jsonb). Dropped to NULL at load: "
        "a digest of the map is a pseudonym of nothing, and its presence leaks how many companies "
        "hold a value for this person."),
    "res.partner.website": PERSONAL,
    "res.partner.company_registry": PERSONAL,
    "res.partner.global_location_number": PERSONAL,
    "res.partner.peppol_endpoint": PERSONAL,
    "res.partner.signup_type": PERSONAL,
    # vat carries NPWP for a badan and NIK for a perorangan under Coretax - Art. 4(3).
    "res.partner.vat": ("sensitive", False, ART_43, "Indonesian NPWP / NIK. Art. 4(3) specific personal data. Hashed at load."),
    "res.partner.comment": FREETEXT,
    "res.partner.picking_warn_msg": FREETEXT,
    "res.partner.sale_warn_msg": FREETEXT,
    "res.partner.properties": ("sensitive", True, ART_43, "User-defined JSON properties - arbitrary content; dropped to NULL at load."),
    "res.partner.partner_latitude": GEO,
    "res.partner.partner_longitude": GEO,
    # Financial standing of an individual partner. Contract 01 classifies transaction amounts as
    # `internal` (its own example: sale.order.amount_total), so partner-level credit config stays
    # internal for consistency; only the free-text and identifier columns move up.
    "res.partner.credit_limit": INTERNAL,
    # lang and tz reveal locale and rough timezone, which is weakly identifying, but they are
    # dimension keys the warehouse groups by and they are not Art. 4(2) identifiers on their own.
    "res.partner.lang": INTERNAL,
    "res.partner.tz": INTERNAL,

    # ---------------- res.users ----------------
    "res.users.login": PERSONAL,
    "res.users.password": SECRET,
    "res.users.totp_secret": SECRET,
    "res.users.signature": FREETEXT,
    "res.users.out_of_office_message": FREETEXT,

    # ---------------- res.company : a legal entity, not a natural person ----------------
    "res.company.name": PUBLIC,
    "res.company.email": PUBLIC,
    "res.company.phone": PUBLIC,
    "res.company.company_details": PUBLIC,
    "res.company.report_header": PUBLIC,
    "res.company.report_footer": PUBLIC,
    "res.company.invoice_terms": PUBLIC,
    "res.company.invoice_terms_html": PUBLIC,
    "res.company.bank_account_code_prefix": INTERNAL,
    "res.company.cash_account_code_prefix": INTERNAL,
    "res.company.transfer_account_code_prefix": INTERNAL,

    # ---------------- product catalogue : publishable ----------------
    "product.template.name": PUBLIC,
    "product.template.default_code": PUBLIC,
    "product.template.barcode": PUBLIC,
    "product.template.description": PUBLIC,
    "product.template.description_sale": PUBLIC,
    "product.template.public_description": PUBLIC,
    "product.template.list_price": PUBLIC,
    "product.template.uom_name": PUBLIC,
    "product.product.default_code": PUBLIC,
    "product.product.barcode": PUBLIC,
    "product.product.name": PUBLIC,
    # Cost is not published.
    "product.template.standard_price": INTERNAL,

    # ---------------- sale ----------------
    "sale.order.access_token": SECRET,
    "sale.order.signed_by": PERSONAL,
    "sale.order.note": FREETEXT,
    "sale.order.client_order_ref": PERSONAL,
    "sale.order.reference": INTERNAL,
    # NOTE: sale.order.signature and stock.picking.signature are fields.Image with
    # attachment=True, so they have NO column on their own table - the bytes live in
    # ir_attachment. They are therefore not classifiable here. The CDC loader does not
    # extract ir_attachment at all; see MODULE_KNOWLEDGE.md, "Scope boundaries".

    # ---------------- account.account : the chart of accounts ----------------
    # Added on request of Data Warehouse, which needs `account_type` on
    # fct_account_move_line. account.account is a CONFIGURATION table - a catalogue of ledger
    # accounts authored by an accountant - not a record about a natural person. Every column is
    # therefore `internal` (business record, no transform, lands verbatim) with exactly one
    # exception, `note`.
    #
    # Two of the sixteen -- `code_store` and `note` -- required Security sign-off before the
    # replicated set could change. Security ruled on 2026-08-31 and APPROVED both as written; the
    # ruling is quoted in each row's notes. Security's boundary, worth keeping in front of the next
    # reader: contract 01's barcode ruling has two limbs, and only the first reaches a chart of
    # accounts. Limb A ("a map keyed by anything other than the data subject is never HMAC'd as a
    # whole") is a TRANSFORM rule and it does govern code_store, satisfied here by `internal`
    # carrying no transform. Limb B (the barcode REMEDY: sensitive + drop_to_null) does not follow,
    # because that remedy protects a natural person and a GL account code has no data subject.
    # It is not a type rule and not a storage-shape rule: company_dependent jsonb does NOT imply
    # drop.
    "account.account.account_type": ("internal", False, INTERNAL_BASIS,
        "Chart-of-accounts taxonomy (asset_receivable, income, expense, ...). Not personal data. "
        "`internal` carries no transform, so the value lands readable - which is what "
        "fct_account_move_line needs it for."),
    "account.account.code_store": ("internal", False, INTERNAL_BASIS,
        "Security ruling 2026-08-31: company_dependent, so stored as a per-company jsonb map "
        '({"1": "110100"}) - the res.partner.barcode shape. Contract 01 forbids HMAC-ing such a '
        "map as a whole; `internal` satisfies that BY CONSTRUCTION, because the internal "
        "transform is `none` and nothing is hashed. The ruling's remedy (drop_to_null) does not "
        "follow, because that remedy protects a DATA SUBJECT and a ledger account code has none: "
        "it identifies an account, not a person. The map lands as-is."),
    "account.account.note": ("sensitive", True, ART_43,
        "Security ruling 2026-08-31: Odoo labels this field 'Internal Notes'. It is free text on a "
        "financial account: an accountant may type anything there, including a beneficiary name or "
        "a bank account number, and contract 01 lists bank account under Art. 4(3). Treated like "
        "every other free-text column in this registry (res.partner.comment, account.move."
        "narration, stock.picking.note, ...) and dropped to NULL. No metric in contract 03 "
        "references it."),
    "account.account.name": ("internal", False, INTERNAL_BASIS,
        "Translated column, so stored as a jsonb map keyed by language code - a map keyed by "
        "something other than a data subject, like code_store. `internal` means no transform, so "
        "contract 01's no-HMAC-over-a-map ruling is satisfied by construction. Chart-of-accounts "
        "labels are internal configuration; they are not published to counterparties, so `public` "
        "would overstate it."),
    "account.account.description": ("internal", False, INTERNAL_BASIS,
        "Translated jsonb map keyed by language code. Accountant-facing guidance shown against the "
        "account in the chart-of-accounts UI; account configuration, not personal data."),

    # ---------------- account ----------------
    "account.move.access_token": SECRET,
    "account.move.inalterable_hash": SECRET,
    "account.move.narration": FREETEXT,
    "account.move.invoice_source_email": PERSONAL,
    "account.move.invoice_partner_display_name": PERSONAL,
    "account.move.partner_credit_warning": FREETEXT,
    # account.move.line.name / sale.order.line.name are document *labels*, defaulted from the
    # product name and edited to things like "Paket Data 10GB - Sept 2026". Contract 01 puts
    # document business data at `internal` (its own example: sale.order.amount_total), and the
    # warehouse needs the label for line-level reporting. Kept internal deliberately; the
    # genuinely narrative columns (narration, note, comment, *_customer_note) stay sensitive.

    # ---------------- stock ----------------
    "stock.picking.note": FREETEXT,
    "stock.move.description_picking_manual": FREETEXT,

    # ---------------- PPOB ----------------
    "ppob.transaction.customer_ref": ("sensitive", False, ART_43,
        "Subscriber / meter number. Identifies a household and, joined to amount over time, its "
        "consumption pattern. Hashed at load - the digest still supports repeat-customer counts."),
    "ppob.transaction.customer_name": PERSONAL,
    "ppob.transaction.failure_reason": ("sensitive", True, ART_43,
        "Free text from the biller; may echo subscriber details. Dropped to NULL at load."),
    "ppob.biller.name": PUBLIC,
    "ppob.biller.code": PUBLIC,
    "ppob.biller.category": PUBLIC,

    # ---------------- point of sale ----------------
    "pos.order.access_token": SECRET,
    "pos.order.ticket_code": SECRET,
    "pos.order.email": PERSONAL,
    "pos.order.mobile": PERSONAL,
    "pos.order.floating_order_name": PERSONAL,
    "pos.order.tracking_number": INTERNAL,
    "pos.order.general_customer_note": FREETEXT,
    "pos.order.internal_note": FREETEXT,
    "pos.order.last_order_preparation_change": INTERNAL,
    "pos.order.line.customer_note": FREETEXT,
    "pos.order.line.note": FREETEXT,
    "pos.order.line.notice": FREETEXT,
}

# --------------------------------------------------------------------------------------
# Pattern rules applied when no explicit override matched. Ordered; first match wins.
# --------------------------------------------------------------------------------------
SUFFIX_RULES = [
    (re.compile(r"(^|_)(access_token|secret|token|api_key|private_key|passwd|password)$"), SECRET),
]

#: A company_dependent field is stored as a jsonb map keyed by company id
#: (``{"1": "BC123", "2": "BC456"}``), not as a scalar. Hashing that map produces a digest of a
#: composite value: it identifies no one, joins to nothing, and still discloses how many companies
#: hold a value. So any company_dependent column that would otherwise be `personal` or `sensitive`
#: is forced to `sensitive` + drop_to_null. custom_pdp_core's test suite asserts this invariant
#: against the live database, so a future Odoo release that makes another column company_dependent
#: fails the build rather than silently shipping a meaningless digest.
COMPANY_DEPENDENT_NOTE = (
    "Stored as a per-company jsonb map, not a scalar. Dropped to NULL at load: a digest of the "
    "map identifies nobody and joins to nothing."
)


#: Physical types over which contract 01 permits a deterministic digest. Deliberately the exact
#: set the CDC loader enforces at startup (``bct_cdc.policy.TEXT_TYPES`` / ``warehouse_ctl.
#: TEXTUAL_TYPES``), expressed here in ``udt_name`` spelling. Contract 01: "Text types only - not
#: anything castable to text." A bigint IS hashable if you cast it; the choice of canonical
#: rendering is precisely the ambiguity that produced the res.partner.barcode defect.
TEXTUAL_UDT = frozenset({"text", "varchar", "bpchar", "char", "name", "citext"})


class UnhashableColumn(SystemExit):
    """The seed would ask the loader to digest a non-text column."""


def assert_hashable(model, column, udt, decision):
    """Refuse to WRITE a seed that the loader would refuse to START on.

    The loader already fails closed on this (contract 01's startup validation, added after
    res.partner.barcode). Repeating the check in the producer is not redundancy for its own sake:
    the loader's version fires in the warehouse, hours later, in another agent's terminal, while
    this one fires in the hand of the person making the classification decision. A `personal` or
    hashed-`sensitive` class on a jsonb column is a CONTRACT question - company_dependent map,
    translated map, or a type that simply has no canonical text rendering - and it must be answered
    deliberately, never by an implicit cast here.
    """
    pdp_class, drop, _basis, _notes = decision
    hashed = pdp_class == "personal" or (pdp_class == "sensitive" and not drop)
    if hashed and udt not in TEXTUAL_UDT:
        raise UnhashableColumn(
            "refusing to write the seed: %s.%s is classified %r (which contract 01 maps to "
            "hmac_sha256) but its physical type is %r, not text. Contract 01 requires an explicit "
            "decision here - the usual answer for a jsonb map (company_dependent or translated) is "
            "sensitive + drop_to_null, because a digest of a map keyed by anything other than the "
            "data subject is a pseudonym of nothing. Do not cast it to text."
            % (model, column, pdp_class, udt)
        )


def enforce_company_dependent(model, column, decision, is_company_dependent):
    """Force a company_dependent personal/sensitive column to the NULL-drop transform."""
    pdp_class, drop, basis, notes = decision
    if not is_company_dependent or pdp_class not in ("personal", "sensitive"):
        return decision
    if pdp_class == "sensitive" and drop:
        return decision
    return ("sensitive", True, ART_43, COMPANY_DEPENDENT_NOTE)


def xmlid(model, field):
    return "pdp_%s__%s" % (model.replace(".", "_"), field)


def classify(model, column):
    key = "%s.%s" % (model, column)
    if key in OVERRIDES:
        return OVERRIDES[key]
    for pattern, decision in SUFFIX_RULES:
        if pattern.search(column):
            return decision
    return INTERNAL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", required=True, help="libpq connection string to a live Odoo DB")
    parser.add_argument("--out", required=True, help="path of the CSV to write")
    args = parser.parse_args()

    import psycopg2  # noqa: PLC0415 - dev-only dependency

    rows = []
    seen = set()
    missing_models = {model for model, _table in MODELS}
    with psycopg2.connect(args.dsn) as conn, conn.cursor() as cur:
        for model, table in MODELS:
            cur.execute(
                "SELECT c.column_name, COALESCE(f.company_dependent, false), c.udt_name "
                "FROM information_schema.columns c "
                "LEFT JOIN ir_model_fields f "
                "       ON f.model = %s AND f.name = c.column_name "
                "WHERE c.table_schema = 'public' AND c.table_name = %s "
                "ORDER BY c.column_name",
                (model, table),
            )
            columns = list(cur.fetchall())
            if not columns:
                print("ERROR: table %s not found - model %s" % (table, model), file=sys.stderr)
                continue
            for column, is_company_dependent, udt in columns:
                decision = enforce_company_dependent(
                    model, column, classify(model, column), is_company_dependent
                )
                assert_hashable(model, column, udt, decision)
                pdp_class, drop, basis, notes = decision
                rows.append((xmlid(model, column), model, column, pdp_class, basis, notes,
                             "True" if drop else "False"))
                seen.add((model, column))
            missing_models.discard(model)

    if missing_models:
        raise SystemExit(
            "refusing to write a partial seed: these models have no table in the target "
            "database, so their columns would silently vanish from the registry: %s. Point --dsn "
            "at a database with every custom module installed." % ", ".join(sorted(missing_models))
        )
    rows.sort(key=lambda r: (r[1], r[2]))
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(["id", "model_name", "field_name", "pdp_class", "legal_basis", "notes",
                         "drop_to_null"])
        writer.writerows(rows)
    print("wrote %d rows to %s" % (len(rows), args.out))


if __name__ == "__main__":
    main()
