"""PDP masking, asserted against the values actually stored in the warehouse.

The brief is explicit that this must compare the **actual stored value**, not a mock, so every
assertion here reads a row out of ``raw.*`` and compares it to a digest computed here, in the test,
from a specification transcribed by hand (``helpers/pdp.py``) rather than imported from the loader.
Importing the loader's own hash function would make the test assert that a function agrees with
itself.

Four separate properties, because they fail independently:

1. A ``personal`` column holds a 64-character lowercase digest and never the cleartext.
2. That digest is the *right* digest -- HMAC(key=salt, msg=value), not `sha256(salt||value)`, which
   also produces 64 lowercase hex characters and would pass every shape check.
3. A ``secret`` column **does not exist as a column at all**. Contract 05 maps `secret` to
   `transform='drop'`, and the point of dropping rather than nulling is that a column which is never
   selected cannot leak through a `SELECT *`, a `pg_dump`, or a future model that forgets.
4. Every column the loader lands is classified. "Unclassified is a hard failure" is only a
   structural fact if nothing unclassified is present.
"""

from __future__ import annotations

import re

import pytest

from helpers import db, env, pdp

pytestmark = [pytest.mark.live]

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _tenant():
    return env.env("CDC_TENANT_SLUG", env.env("ODOO_DB_NAME", "bct"))


def _salt(tenant):
    return env.env("WAREHOUSE_MASK_SALT_" + tenant.upper()) or env.env("WAREHOUSE_MASK_SALT_DEFAULT")


def test_known_answer_vectors_hold(evidence):
    """Before any warehouse value is compared, prove this file matches the specification."""
    lines = []
    for value, salt, expected in pdp.KNOWN_ANSWER_VECTORS:
        got = pdp.digest(value, salt)
        lines.append("%-32s %-18s %s %s" % (value[:32], salt, got, "OK" if got == expected else "FAIL"))
        assert got == expected, "vector %r/%r: got %s expected %s" % (value, salt, got, expected)
    evidence.add("MODULE_KNOWLEDGE.md §2 known-answer vectors", "\n".join(lines))

    value, salt, expected = pdp.KNOWN_ANSWER_VECTORS[0]
    wrong = pdp.salt_concat_sha256(value, salt)
    evidence.add(
        "negative control -- sha256(salt || value) must NOT match",
        "hmac   %s\nconcat %s" % (expected, wrong),
    )
    assert wrong != expected

    # Rows 1 and 2 of the vector table are the cross-tenant separation property.
    assert pdp.digest("budi.santoso@contoh.invalid", "bct-demo-salt") != pdp.digest(
        "budi.santoso@contoh.invalid", "other-tenant-salt"
    ), "the same value under two tenant salts collides; tenants would share join keys"

    assert pdp.digest(None, "x") is None
    assert pdp.digest("", "x") is None, "empty string must map to NULL, not to a shared digest"


def test_personal_columns_are_digests_in_the_warehouse(warehouse_up, cdc_warehouse, evidence):
    tenant = _tenant()
    personal = db.query(
        cdc_warehouse,
        "SELECT source_table, source_column FROM warehouse.column_policy "
        "WHERE transform = 'hmac_sha256' ORDER BY 1, 2;",
    )
    assert personal, "no column is classified `personal`; this test would prove nothing"
    evidence.add(
        "columns classified personal -> hmac_sha256 (%d)" % len(personal),
        "\n".join("%s.%s" % (t, c) for t, c in personal),
    )

    checked, problems = [], []
    for table, column in personal:
        rows = db.query(
            cdc_warehouse,
            'SELECT "%s" FROM raw.%s WHERE _tenant_id = \'%s\' AND "%s" IS NOT NULL '
            "AND _op <> 'D' LIMIT 25;" % (column, table, tenant, column),
        )
        if not rows:
            continue
        for (value,) in rows:
            if not HEX64.match(value):
                problems.append(
                    "raw.%s.%s holds %r, which is not a 64-char lowercase digest" % (table, column, value[:60])
                )
                break
        checked.append("%-32s %d sampled, all 64-hex" % ("%s.%s" % (table, column), len(rows)))
    evidence.add("sampled personal columns in the landing zone", "\n".join(checked) or "(none had data)")
    assert checked, "no personal column had any data to check; the assertion would be vacuous"
    assert not problems, problems


def test_a_personal_value_is_unreadable_and_matches_the_expected_digest(
    warehouse_up, oltp_up, cdc_warehouse, evidence
):
    """The strongest form: take a real cleartext out of Odoo, find its row in the warehouse.

    The cleartext must not appear anywhere in the column, and the stored value must equal the
    digest this test computes from the specification.
    """
    tenant = _tenant()
    salt = _salt(tenant)
    assert salt and salt != "changeme"

    rows = db.query(
        db.oltp_odoo(),
        "SELECT id, email FROM res_partner WHERE email IS NOT NULL AND email <> '' "
        "ORDER BY id LIMIT 5;",
    )
    assert rows, "no partner in Odoo has an email; nothing to prove"

    compared = []
    for pk, cleartext in rows:
        stored = db.query(
            cdc_warehouse,
            "SELECT email FROM raw.res_partner WHERE _tenant_id = '%s' AND id = %s "
            "AND _op <> 'D' ORDER BY _lsn DESC LIMIT 1;" % (tenant, pk),
        )
        if not stored:
            continue
        value = stored[0][0]
        expected = pdp.digest(cleartext, salt)
        compared.append("id=%-6s stored=%s expected=%s %s"
                        % (pk, value, expected, "OK" if value == expected else "MISMATCH"))
        assert value != cleartext, "raw.res_partner.email holds CLEARTEXT for id=%s" % pk
        assert value == expected, (
            "raw.res_partner.email for id=%s is %s but HMAC-SHA256(salt, %r) is %s"
            % (pk, value, cleartext, expected)
        )
        assert value != pdp.salt_concat_sha256(cleartext, salt), (
            "the stored value equals sha256(salt||value); the loader is using the WRONG "
            "construction and joins will not match custom_pdp_masking"
        )
    evidence.add("cleartext from Odoo vs stored value in the warehouse", "\n".join(compared))
    assert compared, "no partner with an email had landed in the warehouse yet"

    # And the cleartext appears nowhere in the whole column.
    sample = rows[0][1]
    hits = db.scalar(
        cdc_warehouse,
        "SELECT count(*) FROM raw.res_partner WHERE _tenant_id = '%s' AND email = %s;"
        % (tenant, "'" + sample.replace("'", "''") + "'"),
    )
    evidence.add("rows in raw.res_partner whose email equals the cleartext %r" % sample, hits)
    assert int(hits) == 0


def test_secret_columns_do_not_exist_as_columns(warehouse_up, cdc_warehouse, evidence):
    """`secret` -> `drop`. Not NULL, not masked: absent."""
    secrets = db.query(
        cdc_warehouse,
        "SELECT source_table, source_column FROM warehouse.column_policy "
        "WHERE pdp_class = 'secret' ORDER BY 1, 2;",
    )
    assert secrets, "no column is classified `secret`; this test would prove nothing"
    evidence.add(
        "columns classified secret -> drop (%d)" % len(secrets),
        "\n".join("%s.%s" % (t, c) for t, c in secrets),
    )
    assert all(
        r[0] == "drop" for r in db.query(
            cdc_warehouse,
            "SELECT transform FROM warehouse.column_policy WHERE pdp_class = 'secret';",
        )
    ), "a `secret` column is mapped to something other than `drop`"

    present = []
    for table, column in secrets:
        exists = db.scalar(
            cdc_warehouse,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'raw' "
            "AND table_name = '%s' AND column_name = '%s';" % (table, column),
        )
        if int(exists):
            present.append("raw.%s.%s EXISTS" % (table, column))
    evidence.add(
        "secret columns present in raw.*",
        "\n".join(present) or "none -- every secret-class column is absent from the landing zone",
    )
    assert not present, (
        "a secret-class column exists in the landing zone: %r. Contract 05 maps secret -> drop "
        "precisely so it cannot leak through SELECT *, pg_dump, or a future model." % present
    )


def test_every_landed_column_is_classified(warehouse_up, cdc_warehouse, evidence):
    """"Unclassified is a hard failure" is only structural if nothing unclassified is landed."""
    unclassified = db.query(
        cdc_warehouse,
        "SELECT c.table_name, c.column_name FROM information_schema.columns c "
        "WHERE c.table_schema = 'raw' AND left(c.column_name, 1) <> '_' "
        "AND NOT EXISTS (SELECT 1 FROM warehouse.column_policy p "
        "                WHERE p.source_table = c.table_name AND p.source_column = c.column_name) "
        "ORDER BY 1, 2;",
    )
    # The subject set must be non-empty, or "nothing unclassified" is just "nothing". DWH found this
    # exact shape in one of their own tests: an assertion whose subject could be empty, passing
    # because there was nothing to find rather than because nothing was wrong.
    landed = db.scalar(
        cdc_warehouse,
        "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'raw' "
        "AND left(column_name, 1) <> '_';",
    )
    evidence.add("business columns present across raw.*", landed)
    assert int(landed) > 100, (
        "only %s business columns exist in raw.*; an empty landing zone would make this assertion "
        "pass by having nothing to classify" % landed
    )
    evidence.add(
        "landed columns with no classification row",
        "\n".join("%s.%s" % (t, c) for t, c in unclassified) or "none",
    )
    assert not unclassified, (
        "%d landed column(s) have no row in warehouse.column_policy: %r"
        % (len(unclassified), unclassified[:20])
    )


def test_sensitive_free_text_is_nulled_not_hashed(warehouse_up, cdc_warehouse, evidence):
    """`sensitive` + `mask_null` -> NULL. A digest of free text is a re-identification risk."""
    rows = db.query(
        cdc_warehouse,
        "SELECT source_table, source_column, mask_null FROM warehouse.column_policy "
        "WHERE pdp_class = 'sensitive' ORDER BY 1, 2;",
    )
    evidence.add(
        "columns classified sensitive (%d)" % len(rows),
        "\n".join("%s.%s mask_null=%s" % r for r in rows) or "(none)",
    )
    if not rows:
        pytest.skip("nothing is classified `sensitive` in this build (NOT RUN)")
    tenant = _tenant()
    nulled = [(t, c) for t, c, m in rows if m == "t"]
    report = []
    for table, column in nulled:
        non_null = db.scalar(
            cdc_warehouse,
            'SELECT count(*) FROM raw.%s WHERE _tenant_id = \'%s\' AND "%s" IS NOT NULL;'
            % (table, tenant, column),
        )
        report.append("%s.%s non-null rows=%s" % (table, column, non_null))
        assert int(non_null) == 0, (
            "raw.%s.%s is mask_null but holds %s non-null values" % (table, column, non_null)
        )
    evidence.add("mask_null columns", "\n".join(report) or "(no mask_null columns in this build)")
