"""Live sync, end to end, with real timestamps -- create, update, and **delete**.

This is the test the whole architecture exists to pass. ADR 0001 rejected a ``write_date``
incremental tap specifically because it cannot see ``unlink()``; if the delete leg of this test does
not pass, the pipeline is a nightly dump with extra steps and the ADR's central claim is false.

What is asserted, in order:

1. The CDC loader is running and we know **which** warehouse it writes to (discovered, not assumed).
2. A partner created through the Odoo ORM appears in ``raw.res_partner`` with ``_op='I'``.
3. Its ``name`` lands as the PDP digest, not as cleartext -- masking happens *during* load
   (contract 05), so there is no window in which the warehouse holds the personal value.
4. A ``write()`` appears as a **new** row with ``_op='U'`` -- the landing zone is append-only, so
   the insert row must still be there afterwards.
5. An ``unlink()`` appears as a tombstone with ``_op='D'``.
6. The "latest non-deleted version per key" projection -- the rule every mart must apply per
   contract 05 -- returns **zero rows** for that partner. This is the step that distinguishes a
   delete that was recorded from a delete that took effect.
7. Every leg completes inside the landing-latency budget, and the measured seconds are printed.

Timestamps are real on both ends: the Odoo write timestamp comes from the OLTP database's clock and
``_ingested_at`` from the warehouse's, and both are printed so the numbers can be checked by hand.
"""

from __future__ import annotations

import uuid

import pytest

from conftest import wait_for
from helpers import db, env, pdp

pytestmark = [pytest.mark.live, pytest.mark.destructive]

#: The landing zone must be faster than the strictest mart SLA in ADR 0001, which is 60 s for
#: `mart_ppob_transaction`. Anything slower makes that SLA unreachable no matter how fast dbt is,
#: so 60 s is the derived budget for a change reaching `raw.*` -- not a number picked for comfort.
LANDING_SLA_SECONDS = 60

LATEST_NON_DELETED = """
SELECT id FROM (
    SELECT id, _op,
           row_number() OVER (PARTITION BY _tenant_id, id
                              ORDER BY _lsn DESC, _ingested_at DESC) AS rn
    FROM raw.res_partner
    WHERE _tenant_id = '{tenant}' AND id = {pk}
) latest
WHERE rn = 1 AND _op <> 'D';
"""


def _rows(target, pk, tenant):
    return db.query(
        target,
        "SELECT _op, name, email, phone, _lsn::text, _ingested_at "
        "FROM raw.res_partner WHERE _tenant_id = '{t}' AND id = {pk} "
        "ORDER BY _ingested_at, _lsn;".format(t=tenant, pk=pk),
    )


def _ops(target, pk, tenant):
    return [r[0] for r in _rows(target, pk, tenant)]


def _history_grid(target, pk, tenant):
    return db.grid(
        target,
        "SELECT id, _op, name, email, phone, _lsn::text, _ingested_at "
        "FROM raw.res_partner WHERE _tenant_id='{t}' AND id={pk} "
        "ORDER BY _ingested_at, _lsn;".format(t=tenant, pk=pk),
    )


def test_live_sync_create_update_delete(
    odoo_up, oltp_up, cdc_running, cdc_warehouse, cdc_target, evidence
):
    from helpers import odoo as odoo_helper

    tenant = env.env("CDC_TENANT_SLUG", env.env("ODOO_DB_NAME", "bct"))
    salt = env.env("WAREHOUSE_MASK_SALT_" + tenant.upper()) or env.env("WAREHOUSE_MASK_SALT_DEFAULT")
    assert salt and salt != "changeme", "no usable PDP salt in .env; the digest check would be vacuous"

    evidence.add("warehouse under test", "container=%s  tenant=%s" % (cdc_target, tenant))
    evidence.add("identity used to read the landing zone", db.grid(cdc_warehouse, db.IDENTITY_SQL))

    tag = uuid.uuid4().hex[:12]
    name_v1 = "QA Live Sync " + tag
    name_v2 = "QA Live Sync " + tag + " UPDATED"
    email = "qa.livesync.%s@contoh.invalid" % tag
    phone = "+62-800-" + tag[:6]

    # ---------------------------------------------------------------- CREATE
    pk = odoo_helper.create_partner(name_v1, email, phone)
    odoo_side = odoo_helper.shell(
        # The clock is read and consumed BEFORE any ORM attribute access. `p.create_date` issues its
        # own query on the same cursor, which discards a pending result set -- reading the clock
        # inline in the dict literal returns None from fetchone() for that reason alone.
        "env.cr.execute(\"SELECT now() AT TIME ZONE 'UTC'\")\n"
        "oltp_now = str(env.cr.fetchone()[0])\n"
        "p = env['res.partner'].browse(%d)\n"
        "result = {'id': p.id, 'create_date': str(p.create_date), "
        "'write_date': str(p.write_date), 'oltp_now': oltp_now}" % pk
    )
    evidence.add(
        "Odoo CREATE (ORM, committed)",
        "id=%s  create_date=%s  write_date=%s  OLTP clock now=%s"
        % (pk, odoo_side["create_date"], odoo_side["write_date"], odoo_side["oltp_now"]),
    )

    got, insert_latency = wait_for(
        lambda: "I" in _ops(cdc_warehouse, pk, tenant), LANDING_SLA_SECONDS, 0.5
    )
    evidence.add("raw.res_partner after CREATE", _history_grid(cdc_warehouse, pk, tenant))
    evidence.add("CREATE landing latency", "%.2fs (budget %ds)" % (insert_latency, LANDING_SLA_SECONDS))
    assert got, (
        "CREATE never landed: no _op='I' row for res_partner id=%s in %s after %.1fs"
        % (pk, cdc_target, insert_latency)
    )

    landed = _rows(cdc_warehouse, pk, tenant)
    insert_row = [r for r in landed if r[0] == "I"][0]
    landed_name, landed_email, landed_phone = insert_row[1], insert_row[2], insert_row[3]

    # ------------------------------------------------- masking, applied at load time
    assert landed_name != name_v1, "res_partner.name landed as CLEARTEXT; contract 01 classes it personal"
    assert landed_name == pdp.digest(name_v1, salt), (
        "name landed, but not as the contract-01 digest of the value we wrote.\n"
        "  landed   %s\n  expected %s" % (landed_name, pdp.digest(name_v1, salt))
    )
    assert landed_email == pdp.digest(email, salt), "email digest mismatch"
    assert landed_phone == pdp.digest(phone, salt), "phone digest mismatch"
    evidence.add(
        "masking verified against the ACTUAL stored value",
        "name cleartext = %r\n"
        "name stored    = %s\n"
        "HMAC-SHA256(key=salt, msg=name) = %s" % (name_v1, landed_name, pdp.digest(name_v1, salt)),
    )

    # ---------------------------------------------------------------- UPDATE
    odoo_helper.write_partner(pk, {"name": name_v2})
    expected_v2 = pdp.digest(name_v2, salt)
    got, update_latency = wait_for(
        lambda: any(r[0] == "U" and r[1] == expected_v2 for r in _rows(cdc_warehouse, pk, tenant)),
        LANDING_SLA_SECONDS, 0.5,
    )
    evidence.add("raw.res_partner after UPDATE", _history_grid(cdc_warehouse, pk, tenant))
    evidence.add("UPDATE landing latency", "%.2fs (budget %ds)" % (update_latency, LANDING_SLA_SECONDS))
    assert got, (
        "UPDATE never landed as a new _op='U' row carrying the new digest after %.1fs" % update_latency
    )

    after_update = _rows(cdc_warehouse, pk, tenant)
    assert any(r[0] == "I" and r[1] == pdp.digest(name_v1, salt) for r in after_update), (
        "the original _op='I' row is gone after the update -- the landing zone is NOT append-only. "
        "Contract 05 forbids UPDATE on raw.*; a change must be a new row."
    )

    # ---------------------------------------------------------------- DELETE
    gone_in_odoo = odoo_helper.unlink_partner(pk)
    assert gone_in_odoo, "unlink() did not remove res_partner id=%s from Odoo" % pk

    got, delete_latency = wait_for(
        lambda: "D" in _ops(cdc_warehouse, pk, tenant), LANDING_SLA_SECONDS, 0.5
    )
    evidence.add(
        "raw.res_partner after DELETE (tombstone; nothing is physically removed)",
        _history_grid(cdc_warehouse, pk, tenant),
    )
    evidence.add("DELETE landing latency", "%.2fs (budget %ds)" % (delete_latency, LANDING_SLA_SECONDS))
    assert got, (
        "DELETE never landed: no _op='D' tombstone for res_partner id=%s after %.1fs. This is the "
        "exact failure mode ADR 0001 chose logical decoding to prevent." % (pk, delete_latency)
    )

    # ---------------- the delete must TAKE EFFECT, not merely be recorded
    survivors = db.query(cdc_warehouse, LATEST_NON_DELETED.format(tenant=tenant, pk=pk))
    evidence.add(
        "latest non-deleted version per key (the projection every mart must apply)",
        "rows returned: %d  -> %r" % (len(survivors), survivors),
    )
    assert survivors == [], (
        "the tombstone landed but the row still survives the latest-non-deleted projection, so a "
        "mart built on it would keep showing the deleted partner."
    )

    ops = _ops(cdc_warehouse, pk, tenant)
    evidence.add("full operation history for the key", " -> ".join(ops))
    assert ops.count("I") >= 1 and ops.count("U") >= 1 and ops.count("D") >= 1, ops
    assert len(ops) >= 3, "fewer than three landed rows: history was overwritten, not appended"

    evidence.add(
        "SUMMARY",
        "create %.2fs | update %.2fs | delete %.2fs | budget %ds each | warehouse %s"
        % (insert_latency, update_latency, delete_latency, LANDING_SLA_SECONDS, cdc_target),
    )
    assert insert_latency < LANDING_SLA_SECONDS
    assert update_latency < LANDING_SLA_SECONDS
    assert delete_latency < LANDING_SLA_SECONDS


def test_live_sync_delete_reaches_the_mart(marts_exist, cdc_running, cdc_warehouse, evidence):
    """The same delete, one layer up: it must also disappear from ``marts.dim_partner``.

    Separated from the landing-zone test deliberately. That one proves the CDC contract; this one
    proves dbt's model applies the tombstone. They fail for entirely different reasons -- a missing
    tombstone versus a model that reads ``raw`` without filtering ``_op='D'`` -- and conflating them
    would make a failure ambiguous.

    This one runs ``dbt build``, because the mart is only as current as the last build. That makes
    it slow and it is worth it: "the row is gone from the landing zone's projection" is a claim
    about a SQL expression I wrote in the test, while "the row is gone from the mart" is a claim
    about the model that actually serves the dashboard.
    """
    from conftest import run
    from helpers import odoo as odoo_helper

    assert "dim_partner" in marts_exist, (
        "marts exist but dim_partner is not among them: %r" % (marts_exist,)
    )
    tenant = env.env("CDC_TENANT_SLUG", env.env("ODOO_DB_NAME", "bct"))
    salt = env.env("WAREHOUSE_MASK_SALT_" + tenant.upper()) or env.env("WAREHOUSE_MASK_SALT_DEFAULT")

    tag = uuid.uuid4().hex[:12]
    name = "QA Mart Delete " + tag
    pk = odoo_helper.create_partner(name, "qa.mart.%s@contoh.invalid" % tag, "+62-800-" + tag[:6])
    digest = pdp.digest(name, salt)

    landed, seconds = wait_for(
        lambda: db.query(
            cdc_warehouse,
            "SELECT _op FROM raw.res_partner WHERE _tenant_id='%s' AND id=%d;" % (tenant, pk),
        ),
        LANDING_SLA_SECONDS, 0.5,
    )
    assert landed, "the partner never reached raw.res_partner (%.1fs)" % seconds

    build = run(["make", "dbt-run"], timeout=1800)
    assert build.returncode == 0, (build.stdout + build.stderr)[-1500:]
    present = db.query(
        db.warehouse_admin(),
        "SELECT partner_id, name, is_current FROM marts.dim_partner "
        "WHERE tenant_id='%s' AND partner_id=%d;" % (tenant, pk),
    )
    evidence.add(
        "marts.dim_partner after CREATE (id=%d, digest %s...)" % (pk, digest[:16]),
        "%r" % (present,),
    )
    assert present, (
        "the partner landed in raw but never appeared in marts.dim_partner after a dbt build"
    )

    # ---- now delete it, and require the mart to lose it -------------------------------
    assert odoo_helper.unlink_partner(pk), "unlink() did not remove res_partner id=%d" % pk
    tombstoned, seconds = wait_for(
        lambda: any(r[0] == "D" for r in db.query(
            cdc_warehouse,
            "SELECT _op FROM raw.res_partner WHERE _tenant_id='%s' AND id=%d;" % (tenant, pk),
        )),
        LANDING_SLA_SECONDS, 0.5,
    )
    assert tombstoned, "no tombstone landed for id=%d after %.1fs" % (pk, seconds)

    build = run(["make", "dbt-run"], timeout=1800)
    assert build.returncode == 0, (build.stdout + build.stderr)[-1500:]

    remaining = db.query(
        db.warehouse_admin(),
        "SELECT partner_id, name, is_current FROM marts.dim_partner "
        "WHERE tenant_id='%s' AND partner_id=%d;" % (tenant, pk),
    )
    current = [r for r in remaining if r[2] == "t"]
    evidence.add(
        "marts.dim_partner after DELETE",
        "rows for the key: %r\ncurrent rows: %r" % (remaining, current),
    )
    assert not current, (
        "the partner was deleted in Odoo and tombstoned in raw, but marts.dim_partner still carries "
        "a CURRENT row for it: %r. A dashboard would still show this person." % current
    )

    # And the digest is gone from the mart entirely as a current value.
    leaked = db.query(
        db.warehouse_admin(),
        "SELECT count(*) FROM marts.dim_partner WHERE tenant_id='%s' AND name='%s' "
        "AND is_current;" % (tenant, digest),
    )
    evidence.add("current rows still carrying that partner's name digest", leaked[0][0])
    assert int(leaked[0][0]) == 0
