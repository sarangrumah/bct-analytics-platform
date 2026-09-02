"""Backfill resumability: kill the snapshot mid-run, restart it, and lose nothing.

A backfill that cannot be resumed is one nobody dares start, and on a table of any size it *will* be
interrupted -- by an OOM kill, a deploy, a laptop lid. The loader's design note says the resume
point is ``max(id)`` already landed, read back out of ``raw.<table>`` itself rather than from a
progress table, precisely so the progress marker cannot get ahead of the rows it describes. This
test exercises that claim under ``SIGKILL``, not a graceful stop: a clean shutdown can flush and
checkpoint, which is the case that works even when the design is wrong.

**How the gap is created, and why not with a separate tenant.** The obvious isolation -- run the
probe under a throwaway tenant slug -- does not work here, and finding out why is itself worth
recording: ``CDC_TENANT_SLUG`` names the *publication and slot*, while the value written to
``_tenant_id`` is ``settings.tenant``, i.e. ``CDC_TENANT_DB``. A probe run under a new slug
therefore resumes from the **live** tenant's high-water mark and lands nothing at all, which looks
exactly like a passing test. (Contract 05 defines ``_tenant_id`` as "source database / tenant slug";
the loader consistently uses the database, so this is a documentation ambiguity rather than a bug.)

So the gap is made where the loader will actually see it: a slice of the live tenant's landed rows
is removed, which is precisely the state an interrupted backfill leaves behind. The test restores
the table to a byte-identical live projection before it finishes, and asserts that it did.
"""

from __future__ import annotations

import re
import time

import pytest

from conftest import run
from helpers import db, env, loader

pytestmark = [pytest.mark.live, pytest.mark.destructive, pytest.mark.slow]

PROBE_NAME = "odoo19-bct-cdc-qa-resume"
#: Candidates for "the biggest replicated table". Which one that is changes as seed data and test
#: scaffolding come and go, so the choice is made at runtime from what the dataset holds.
CANDIDATES = ("ppob_transaction", "account_move_line", "sale_order_line", "stock_move",
              "pos_order_line", "res_partner")
TABLE = CANDIDATES[0]
#: Fraction of the table left in place, i.e. the simulated interruption point.
KEEP_FRACTION = 0.4
#: Below this the backfill completes faster than any poll loop can interrupt it, and the test would
#: fail for a reason that says nothing about resumability. Measured on this hardware, not guessed:
#: a 9,610-row table took long enough to SIGKILL mid-run reliably, while a 258-row gap finished in
#: about 30 ms -- the whole backfill was over before the first poll came round.
MIN_ROWS = 3000

#: Business columns only. `_lsn` and `_ingested_at` legitimately differ between the original load
#: and the re-load -- the row is re-read at a new snapshot LSN -- so including them would make a
#: correct restore look like a difference.
CHECKSUM = """
SELECT md5(string_agg(sig, '|' ORDER BY sig)), count(*)::text
FROM (
  SELECT md5(ROW(id, name, amount, fee, state, biller_id, partner_id, company_id,
                 operating_unit_id, transaction_date)::text) AS sig
  FROM (
    SELECT *, row_number() OVER (PARTITION BY _tenant_id, id
                                 ORDER BY _lsn DESC, _ingested_at DESC) AS _rn
    FROM raw.{table} WHERE _tenant_id = '{tenant}'
  ) t WHERE t._rn = 1 AND t._op <> 'D'
) s;
"""


def _tenant():
    return env.env("ODOO_DB_NAME", "bct")


def _columns(admin):
    return [
        r[0] for r in db.query(
            admin,
            "SELECT column_name FROM information_schema.columns WHERE table_schema='raw' "
            "AND table_name='%s' AND left(column_name,1) <> '_' ORDER BY ordinal_position;" % TABLE,
        )
    ]


def _checksum(admin, tenant, columns):
    projection = ", ".join('"%s"' % c for c in columns)
    sql = (
        "SELECT md5(string_agg(sig, '|' ORDER BY sig)), count(*)::text FROM ("
        "  SELECT md5(ROW(%s)::text) AS sig FROM ("
        "    SELECT *, row_number() OVER (PARTITION BY _tenant_id, id"
        "                                 ORDER BY _lsn DESC, _ingested_at DESC) AS _rn"
        "    FROM raw.%s WHERE _tenant_id = '%s'"
        "  ) t WHERE t._rn = 1 AND t._op <> 'D'"
        ") s;" % (projection, TABLE, tenant)
    )
    return db.query(admin, sql)[0]


def test_an_interrupted_backfill_resumes_where_it_stopped(oltp_up, warehouse_up, evidence):
    global TABLE
    admin = db.warehouse_admin()
    tenant = _tenant()

    # Pick the biggest replicated table available *now*, rather than hardcoding one. A table with
    # fewer rows than a couple of pages has no middle, so a "kill it mid-run" test against one
    # either lands nothing or finishes first -- and then passes or fails for reasons that have
    # nothing to do with resumability. The biggest table changes as seed data and test scaffolding
    # come and go, so this is discovered, not assumed.
    sizes = []
    for candidate in CANDIDATES:
        try:
            sizes.append(
                (int(db.scalar(db.oltp_odoo(), "SELECT count(*) FROM %s;" % candidate)), candidate)
            )
        except db.PsqlError:
            continue
    sizes.sort(reverse=True)
    evidence.add(
        "candidate tables, by row count in Odoo",
        "\n".join("%-20s %d" % (t, n) for n, t in sizes) or "(none readable)",
    )
    if not sizes or sizes[0][0] < MIN_ROWS:
        pytest.skip(
            "the largest replicated table has %s rows; at least %d are needed for the backfill to "
            "have a middle to interrupt. That is a property of the dataset, not of the loader, so "
            "it is reported as NOT RUN rather than as a failure."
            % (sizes[0][0] if sizes else "no", MIN_ROWS)
        )
    TABLE = sizes[0][1]
    columns = _columns(admin)
    source_rows = sizes[0][0]
    overrides = {
        "CDC_SOURCE_TABLES": TABLE,
        # Pages sized so the run takes long enough to interrupt: roughly twenty of them across the
        # gap, whatever the table's size turns out to be.
        "CDC_BATCH_SIZE": str(max(10, int(source_rows * (1 - KEEP_FRACTION)) // 20)),
    }
    before_checksum, before_live = _checksum(admin, tenant, columns)
    max_id = int(db.scalar(db.oltp_odoo(), "SELECT max(id) FROM %s;" % TABLE))
    cut = int(db.scalar(
        db.oltp_odoo(),
        "SELECT id FROM %s ORDER BY id OFFSET %d LIMIT 1;"
        % (TABLE, int(source_rows * KEEP_FRACTION)),
    ))
    evidence.add(
        "before",
        "rows in Odoo.%s          %d\nlive projection in raw     %s\nchecksum                   %s\n"
        "simulated interruption at  id > %d (of max id %d)"
        % (TABLE, source_rows, before_live, before_checksum, cut, max_id),
    )
    assert int(before_live) == source_rows, (
        "the landing zone is not in sync with Odoo before the test starts (%s vs %d); fix that "
        "first, otherwise a restore cannot be told apart from a pre-existing gap"
        % (before_live, source_rows)
    )

    loader.kill(PROBE_NAME)
    try:
        # ---- create the gap an interrupted backfill would have left ------------------
        rc, out, err = db.execute(
            admin, "DELETE FROM raw.%s WHERE _tenant_id = '%s' AND id > %d;" % (TABLE, tenant, cut)
        )
        assert rc == 0, err
        gap_start_live = int(_checksum(admin, tenant, columns)[1])
        missing = source_rows - gap_start_live
        evidence.add(
            "gap created", "%s\nlive rows now %d, missing %d" % (out, gap_start_live, missing)
        )
        assert missing > 50, "the simulated gap is only %d rows; too small to interrupt" % missing

        # ---- run 1: resume, then SIGKILL mid-flight ----------------------------------
        process = loader.popen_loader_direct(PROBE_NAME, ["--backfill-only"], overrides)
        partial = gap_start_live
        deadline = time.time() + 90
        while time.time() < deadline:
            partial = int(_checksum(admin, tenant, columns)[1])
            if partial >= gap_start_live + max(20, missing // 4):
                break
            time.sleep(0.3)
        killed = run(["docker", "kill", "-s", "KILL", PROBE_NAME], timeout=60)
        try:
            run1_log = process.communicate(timeout=90)[0] or ""
        except Exception:
            process.kill()
            run1_log = ""
        after_kill = int(_checksum(admin, tenant, columns)[1])
        evidence.add(
            "run 1: SIGKILL mid-backfill",
            "live rows at kill        %d (started from %d, target %d)\n"
            "docker kill rc=%d %s\n%s"
            % (after_kill, gap_start_live, source_rows, killed.returncode,
               (killed.stdout or killed.stderr).strip(),
               "\n".join(line for line in run1_log.splitlines() if TABLE in line)[-600:]),
        )
        assert killed.returncode == 0, (
            "the loader was already gone when the kill was issued, so nothing was interrupted: %s"
            % (killed.stdout or killed.stderr).strip()
        )
        assert gap_start_live < after_kill < source_rows, (
            "run 1 landed %d of the %d missing rows -- it either did nothing or finished, so this "
            "test says nothing about resuming" % (after_kill - gap_start_live, missing)
        )

        # ---- run 2: resume from where run 1 died -------------------------------------
        loader.kill(PROBE_NAME)
        second = loader.run_loader_direct(PROBE_NAME, ["--backfill-only"], overrides, timeout=900)
        log = second.stdout + second.stderr
        evidence.add(
            "run 2: resume (rc=%d)" % second.returncode,
            "\n".join(line for line in log.splitlines() if TABLE in line)[-800:],
        )
        assert second.returncode == 0, log[-2000:]

        resume = re.search(r"resuming backfill of \S+\.%s from id > (\d+)" % TABLE, log)
        assert resume, "run 2 logged no resume point for %s:\n%s" % (TABLE, log[-1200:])
        resume_point = int(resume.group(1))
        landed = re.search(r"backfill \S+\.%s complete: (\d+) rows landed this run" % TABLE, log)
        assert landed, log[-1200:]

        after_checksum, after_live = _checksum(admin, tenant, columns)
        evidence.add(
            "run 2 result",
            "resumed from             id > %d\nrows landed this run     %s\n"
            "expected                 %d\nlive rows now            %s (target %d)"
            % (resume_point, landed.group(1), source_rows - after_kill, after_live, source_rows),
        )
        assert resume_point > cut, (
            "run 2 resumed from id > %d, at or below the interruption point %d -- it re-read rows "
            "it already had" % (resume_point, cut)
        )
        assert int(landed.group(1)) == source_rows - after_kill, (
            "run 2 landed %s rows; resuming from %d of %d should have landed exactly %d"
            % (landed.group(1), after_kill, source_rows, source_rows - after_kill)
        )
        assert int(after_live) == source_rows

        # ---- the restored table is byte-identical to the original --------------------
        evidence.add(
            "restored?",
            "checksum before  %s\nchecksum after   %s\nidentical: %s"
            % (before_checksum, after_checksum, before_checksum == after_checksum),
        )
        assert after_checksum == before_checksum, (
            "the resumed backfill restored the row count but not the content: the live projection "
            "checksum changed from %s to %s" % (before_checksum, after_checksum)
        )
    finally:
        loader.kill(PROBE_NAME)
        final_checksum, final_live = _checksum(admin, tenant, columns)
        if final_checksum != before_checksum:
            # Never leave the warehouse short: re-run to completion before reporting.
            repair = loader.run_loader_direct(PROBE_NAME, ["--backfill-only"], overrides, timeout=900)
            final_checksum, final_live = _checksum(admin, tenant, columns)
            evidence.add(
                "repair pass (the test failed part-way and the table was left incomplete)",
                "rc=%d  live rows now %s  checksum matches original: %s"
                % (repair.returncode, final_live, final_checksum == before_checksum),
            )
            loader.kill(PROBE_NAME)
