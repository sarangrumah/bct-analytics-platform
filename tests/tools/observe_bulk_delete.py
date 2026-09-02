"""Watch a bulk delete travel from Odoo to the mart, and record the numbers.

Written for Backend's 9,250-row `unlink` of the throughput-test PPOB transactions. A single-row
delete has already been measured at 0.20 s end to end; a delete four orders of magnitude larger is
the closest thing this build has to a real test of the tombstone path under load, and of the 2 GiB
`max_slot_wal_keep_size` cap that ADR 0001 makes load-bearing.

Sampled every ~2 s, all from the same clock so the columns are comparable:

* ``odoo``        rows still in Odoo's ``ppob_transaction``
* ``live``        rows surviving the contract-05 latest-non-deleted projection of ``raw``
* ``tomb``        ``_op='D'`` rows landed in ``raw``
* ``mart``        rows in ``marts.fct_ppob_transaction`` (the projection one layer up)
* ``retained``    WAL bytes the replication slot is holding back -- the number the 512 MiB warning
                  and 2 GiB cap are measured against
* ``active``      whether the slot still has a consumer
* ``fresh_age``   seconds since ``max(pipeline_state.last_success_at)``; this must NOT climb during
                  the burst, or `meta.is_stale` would flip while the pipeline is healthy and busy

Usage:  python3 tests/tools/observe_bulk_delete.py [--seconds 900] [--out FILE]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from helpers import db, env  # noqa: E402

TABLE = "ppob_transaction"


def sample(tenant):
    admin = db.warehouse_admin()
    odoo = db.oltp_odoo()
    row = {}
    row["odoo"] = db.scalar(odoo, "SELECT count(*) FROM %s;" % TABLE)
    slot = db.query(
        odoo,
        "SELECT active, wal_status, "
        "pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)::bigint, "
        "pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) "
        "FROM pg_replication_slots WHERE plugin='pgoutput' ORDER BY slot_name LIMIT 1;",
    )
    row["active"], row["wal_status"], row["retained"], row["retained_pretty"] = (
        slot[0] if slot else ("-", "-", "0", "0")
    )
    counts = db.query(
        admin,
        "SELECT (SELECT count(*) FROM (SELECT id, _op, row_number() OVER "
        "(PARTITION BY _tenant_id, id ORDER BY _lsn DESC, _ingested_at DESC) rn "
        "FROM raw.%s WHERE _tenant_id='%s') t WHERE rn=1 AND _op<>'D'), "
        "(SELECT count(*) FROM raw.%s WHERE _tenant_id='%s' AND _op='D'), "
        "(SELECT count(*) FROM raw.%s WHERE _tenant_id='%s'), "
        "(SELECT count(*) FROM marts.fct_%s WHERE tenant_id='%s'), "
        "(SELECT round(extract(epoch FROM now() - max(last_success_at))) "
        " FROM warehouse.pipeline_state WHERE tenant_id='%s');"
        % (TABLE, tenant, TABLE, tenant, TABLE, tenant, TABLE, tenant, tenant),
    )[0]
    row["live"], row["tomb"], row["raw"], row["mart"], row["fresh_age"] = counts
    return row


HEADER = ("    elapsed      odoo      live      tomb       raw      mart   "
          "retained  act  wal_status  fresh_age")


def line(elapsed, row):
    return "%11.1f %9s %9s %9s %9s %9s %10s  %-3s %-11s %6s" % (
        elapsed, row["odoo"], row["live"], row["tomb"], row["raw"], row["mart"],
        row["retained_pretty"], row["active"], row["wal_status"], row["fresh_age"],
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=900.0)
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    tenant = env.env("ODOO_DB_NAME", "bct")
    handle = open(args.out, "w", encoding="utf-8") if args.out else None

    def emit(text):
        print(text, flush=True)
        if handle:
            handle.write(text + "\n")
            handle.flush()

    emit("# observing a bulk delete of %s, tenant=%s" % (TABLE, tenant))
    emit("# started %s" % time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()))
    baseline = sample(tenant)
    emit("# BASELINE " + str(baseline))
    emit(HEADER)
    started = time.time()
    emit(line(0.0, baseline))

    first_change = None
    last = baseline
    stable_since = None
    while time.time() - started < args.seconds:
        time.sleep(args.interval)
        now = sample(tenant)
        elapsed = time.time() - started
        emit(line(elapsed, now))
        if first_change is None and now["odoo"] != baseline["odoo"]:
            first_change = elapsed
            emit("# ODOO SIDE CHANGED at %.1fs: %s -> %s"
                 % (elapsed, baseline["odoo"], now["odoo"]))
        if first_change is not None:
            if now == last:
                if stable_since is None:
                    stable_since = elapsed
                elif elapsed - stable_since > 20:
                    emit("# STABLE for 20s; stopping")
                    break
            else:
                stable_since = None
        last = now

    final = sample(tenant)
    emit("# FINAL " + str(final))
    emit("# deleted in odoo      : %s -> %s (%d rows)"
         % (baseline["odoo"], final["odoo"], int(baseline["odoo"]) - int(final["odoo"])))
    emit("# tombstones landed    : %s -> %s (%d rows)"
         % (baseline["tomb"], final["tomb"], int(final["tomb"]) - int(baseline["tomb"])))
    emit("# live projection      : %s -> %s" % (baseline["live"], final["live"]))
    emit("# mart rows            : %s -> %s" % (baseline["mart"], final["mart"]))
    emit("# peak retained WAL    : see the retained column; cap is 2 GiB, warn at 512 MiB")
    if handle:
        handle.close()


if __name__ == "__main__":
    main()
