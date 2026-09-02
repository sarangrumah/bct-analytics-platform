"""Startup checks, then backfill, then stream. The order below is load-bearing.

Every check that can fail runs *before* a replication slot exists. A slot begins retaining WAL the
instant it is created and the 2 GB cap starts counting immediately (contract 04), so a loader that
creates its slot and then discovers it cannot mask a column has already started a clock it is not
consuming. Fail first, then take the slot.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

import psycopg2.extras

from . import backfill as bf
from . import metrics as m
from . import pdp_hash
from . import source as src
from . import warehouse as wh
from .config import settings_from_env
from .odoo_rpc import OdooClient, verify_digest_agreement
from .policy import Policy, UnclassifiedColumn, UnhashableColumn, publication_column_list
from .stream import StreamConsumer

_logger = logging.getLogger("bct_cdc")

#: Exit codes. Distinct so an operator (or a test) can tell a policy refusal from a crash.
EXIT_OK = 0
EXIT_UNCLASSIFIED = 3
EXIT_SLOT_INVALIDATED = 4
EXIT_DIGEST_MISMATCH = 5
EXIT_POLICY_MISSING = 6
EXIT_SCHEMA_DRIFT = 7

_stop = threading.Event()

#: Set by the slot monitor when Postgres reports ``wal_status='lost'``. Read by the stream loop,
#: which then stops rather than continuing to consume a slot whose history has been discarded.
_slot_invalidated = threading.Event()


def _configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )


def build_plans(source_conn, policy: Policy, tables, salt: str) -> dict:
    """Resolve every table's masking plan, or refuse to start.

    This is where "hard-fail on an unclassified column" actually happens, and it happens for *all*
    tables before any of them is used -- reporting one missing classification, fixing it, and then
    discovering the next one is a slow way to find out the policy was never seeded.
    """
    plans = {}
    problems = []
    for table in tables:
        columns = src.source_columns(source_conn, table)
        names = [c for c, _ in columns]
        types = {c: t for c, t in columns}
        try:
            plans[table] = policy.plan(table, names, salt, column_types=types)
        except (UnclassifiedColumn, UnhashableColumn) as exc:
            problems.append(str(exc))
    if problems:
        raise UnclassifiedColumn("\n\n".join(problems))
    return plans


def assert_publication_excludes_secrets(source_conn, publication: str, policy: Policy, plans) -> None:
    """The structural ``secret`` control: Postgres must not put the column on the wire at all.

    Checked rather than assumed. A publication created without a column list replicates every
    column, which would mean ``res_users.password`` travels to this process and is only kept out of
    the warehouse by this loader's own filtering -- a policy control wearing a structural control's
    clothes.
    """
    declared = src.publication_tables(source_conn, publication)
    failures = []
    for table in plans:
        secrets = policy.secret_columns(table)
        if not secrets:
            continue
        columns = declared.get(table)
        if columns is None:
            failures.append(
                "%s is not in publication %s at all" % (table, publication)
            )
        elif not columns:
            failures.append(
                "%s is published without a column list, so its secret columns (%s) would be sent "
                "on the wire" % (table, ", ".join(secrets))
            )
        else:
            leaked = sorted(set(columns) & set(secrets))
            if leaked:
                failures.append(
                    "%s publishes secret columns: %s" % (table, ", ".join(leaked))
                )
    if failures:
        raise RuntimeError(
            "Publication %s does not structurally exclude secret-class columns:\n  - %s\n"
            "Re-run scripts/analytics/cdc-provision.sh, which builds the column list from "
            "warehouse.column_policy." % (publication, "\n  - ".join(failures))
        )


def assert_publication_covers_plans(source_conn, publication: str, plans) -> None:
    """Every table the loader plans to replicate must actually be IN the publication.

    This exists because :func:`assert_publication_excludes_secrets` cannot catch a missing table.
    That check starts with ``if not secrets: continue`` -- a table with no ``secret``-class column
    is skipped outright, so its total absence from the publication produces no failure at all. It
    is the empty-result tell in its purest form: the check asks "did any secret leak?", gets
    "none", and has no way to distinguish "none leaked" from "this table is not published".

    Observed for real: ``warehouse.column_policy`` gained ``account_account`` (16 columns, zero
    of them ``secret``) after the publication was created. The loader planned the table, the
    backfill landed 104 rows over a plain ``SELECT`` -- and Postgres never put a single change on
    the wire, because the publication still carried the other 15 tables. ``raw.account_account``
    looked populated and was frozen at the moment of the backfill; every row sat at ``_lsn 0/0``.

    The backfill is exactly what makes this invisible: it does not go through the publication, so
    a table can be fully populated and permanently stale at the same time. Nothing downstream can
    tell those apart -- a stale row and a fresh row are the same row.

    Refusing to start is the right response and the loader cannot repair it itself: ``CREATE`` /
    ``ALTER PUBLICATION`` requires table ownership, which ``warehouse_reader`` deliberately does
    not have (contract 04). The remedy is named in the error.
    """
    declared = src.publication_tables(source_conn, publication)
    # Empty-result rule: assert the population searched was non-empty. A publication that carries
    # no tables at all would otherwise make the set-difference below trivially satisfiable for
    # zero plans, and would make this check pass on a publication that replicates nothing.
    if not declared:
        raise RuntimeError(
            "Publication %s declares no tables at all. Nothing would ever stream. Run "
            "scripts/analytics/cdc-provision.sh." % publication
        )
    missing = sorted(set(plans) - set(declared))
    if missing:
        raise RuntimeError(
            "Publication %s does not carry %d of the %d tables this loader plans to replicate: "
            "%s.\n"
            "Those tables would be backfilled once by a plain SELECT and then never receive "
            "another change -- populated and permanently stale, which is indistinguishable "
            "downstream from up to date.\n"
            "warehouse.column_policy has grown since the publication "
            "was created. Re-run scripts/analytics/cdc-provision.sh, which rebuilds the "
            "publication from the policy as the `odoo` role (this loader holds no ownership and "
            "cannot ALTER PUBLICATION itself)."
            % (publication, len(missing), len(plans), ", ".join(missing))
        )
    _logger.info(
        "publication %s carries all %d planned tables (%d declared in total)",
        publication, len(plans), len(declared),
    )


def _slot_monitor(settings, stop_event) -> None:
    """Poll the server's view of the slot.

    Deliberately the *corroborating* signal, not the paging one. When this consumer is dead or
    wedged -- the exact failure the 2 GB cap exists to bound -- this series goes **absent, not
    high**, so an alert on it either never fires or fires as a generic scrape-down that never
    mentions WAL retention. The alert that pages comes from postgres_exporter on the OLTP side,
    which keeps reporting after this process dies. The value of publishing both is that they can
    *disagree*: a consumer that believes it is caught up while Postgres says it is 2 GB behind.
    """
    conn = wh.connect(settings.source_dsn, autocommit=True)
    try:
        while not stop_event.is_set():
            try:
                status = src.slot_status(conn, settings.slot)
                m.SLOT_LAG_BYTES.labels(tenant=settings.tenant, slot=settings.slot).set(
                    status["lag_bytes"]
                )
                m.SLOT_INVALIDATED.labels(tenant=settings.tenant, slot=settings.slot).set(
                    1 if status["wal_status"] == "lost" else 0
                )
                if status["wal_status"] == "lost":
                    # Logging alone would leave the consumer running against a slot whose WAL
                    # Postgres has already discarded, quietly producing a mart with a hole. Stop.
                    _logger.error(
                        "replication slot %s is INVALIDATED (wal_status=lost). The 2 GB cap fired; "
                        "the mart has a hole and a re-snapshot is required. Stopping the consumer.",
                        settings.slot,
                    )
                    _slot_invalidated.set()
                    stop_event.set()
                    return
            except Exception as exc:  # pragma: no cover - monitoring must not kill the loader
                _logger.warning("slot monitor: %s", exc)
            stop_event.wait(10.0)
    finally:
        conn.close()


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="bct-cdc", description="Odoo -> warehouse CDC loader")
    parser.add_argument("--check-only", action="store_true",
                        help="Run every startup check and exit. Creates no slot.")
    parser.add_argument("--backfill-only", action="store_true")
    parser.add_argument("--stream-only", action="store_true")
    parser.add_argument("--print-publication-sql", action="store_true",
                        help="Emit the CREATE PUBLICATION statement built from warehouse.column_policy.")
    parser.add_argument("--drop-slot", action="store_true",
                        help="Drop the replication slot and exit. Teardown only.")
    parser.add_argument("--max-seconds", type=float, default=0.0,
                        help="Stop streaming after N seconds. Test harness only.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)

    # 1. The digest is verified before anything is connected, so a broken build cannot reach a
    #    database at all.
    pdp_hash.self_test()

    settings = settings_from_env()
    _logger.info("starting: %r", settings)

    warehouse_conn = wh.connect(settings.warehouse_dsn)
    try:
        try:
            wh.assert_pipeline_tables(warehouse_conn)
            policy_rows = wh.load_column_policy(warehouse_conn)
        except wh.ColumnPolicyMissing as exc:
            _logger.error("%s", exc)
            return EXIT_POLICY_MISSING
        policy = Policy(policy_rows)
        _logger.info("loaded %d column policy rows covering %d tables",
                     len(policy_rows), len(policy.tables))

        source_conn = wh.connect(settings.source_dsn, autocommit=True)
        try:
            if args.drop_slot:
                src.drop_slot(source_conn, settings.slot)
                _logger.info("dropped replication slot %s; no WAL is retained for it", settings.slot)
                return EXIT_OK

            # 2. Masking plans for every table, or a hard failure naming every offending column.
            # The table list comes from the policy, not from a constant in this repository: DWH
            # decides what is replicated by classifying it, and the loader follows.
            tables = settings.source_tables or wh.policy_tables(warehouse_conn)
            _logger.info("replicating %d tables declared in warehouse.column_policy", len(tables))
            try:
                plans = build_plans(source_conn, policy, tables, settings.salt)
            except (UnclassifiedColumn, UnhashableColumn) as exc:
                _logger.error("refusing to start:\n%s", exc)
                return EXIT_UNCLASSIFIED

            if args.print_publication_sql:
                print(publication_sql(settings.publication, plans))
                return EXIT_OK

            # 3. Cross-language digest agreement, if an Odoo login is configured.
            if settings.verify_digest_spec and settings.odoo_login:
                from .odoo_rpc import DigestSpecMismatch
                client = OdooClient(settings.odoo_url, settings.odoo_db,
                                    settings.odoo_login, settings.odoo_password)
                try:
                    verify_digest_agreement(client)
                except DigestSpecMismatch as exc:
                    _logger.error("%s", exc)
                    return EXIT_DIGEST_MISMATCH

            # 4. The publication must exist and must structurally exclude secrets.
            if not src.publication_exists(source_conn, settings.publication):
                _logger.error(
                    "publication %s does not exist. Run scripts/analytics/cdc-provision.sh "
                    "(it runs as the odoo role, because CREATE PUBLICATION needs ownership and "
                    "warehouse_reader correctly does not have it).", settings.publication
                )
                return EXIT_UNCLASSIFIED
            assert_publication_excludes_secrets(source_conn, settings.publication, policy, plans)
            # ...and must actually carry every planned table. The check above cannot see a table
            # that is simply absent when that table has no secret columns; this one can.
            assert_publication_covers_plans(source_conn, settings.publication, plans)

            # 5. Landing tables must already exist. The loader holds no CREATE on schema raw --
            #    a loader that could create its own landing table could land an unclassified
            #    column, which would demote "unclassified is a hard failure" from a structural
            #    fact to a convention. A missing table is schema drift, reported not repaired.
            try:
                for table, plan in plans.items():
                    wh.assert_landing_table(warehouse_conn, table, plan.select_columns)
            except wh.SchemaDrift as exc:
                _logger.error("%s", exc)
                return EXIT_SCHEMA_DRIFT

            if args.check_only:
                total_hashed = sum(len(p.hashed_columns()) for p in plans.values())
                total_nulled = sum(len(p.nulled_columns()) for p in plans.values())
                total_secret = sum(len(policy.secret_columns(t)) for t in plans)
                _logger.info(
                    "checks passed: %d tables, %d hashed columns, %d nulled columns, "
                    "%d secret columns excluded from extraction",
                    len(plans), total_hashed, total_nulled, total_secret,
                )
                return EXIT_OK

            m.serve(settings.metrics_port)
            m.UP.labels(tenant=settings.tenant).set(0)

            # 6. The slot. Created only now, after every check that could fail has passed.
            replication_conn = psycopg2.connect(
                settings.source_replication_dsn,
                connection_factory=psycopg2.extras.LogicalReplicationConnection,
            )
            try:
                # assert_slot_healthy RAISES SlotInvalidated on wal_status='lost' and returns the
                # status dict otherwise; the return value is logged rather than discarded so an
                # operator can see the retained-WAL figure at startup.
                status = src.assert_slot_healthy(source_conn, settings.slot)
                if status["exists"]:
                    _logger.info(
                        "slot %s: active=%s wal_status=%s retained=%d bytes",
                        settings.slot, status["active"], status["wal_status"], status["lag_bytes"],
                    )
                snapshot_lsn = src.ensure_slot(replication_conn, settings.slot)

                monitor = threading.Thread(
                    target=_slot_monitor, args=(settings, _stop), daemon=True
                )
                monitor.start()

                # 7. Backfill. Always resumable, and re-running it is ALWAYS safe: the resume
                #    point is the highest id already landed, so a repeat run reads nothing. There
                #    is deliberately no --reload flag -- it belonged to an earlier design that
                #    tracked completion in a side table, and once the resume point moved into the
                #    landing zone itself there was nothing left for it to clear. Re-running
                #    `--backfill-only` IS the safe recovery path.
                if not args.stream_only:
                    for table, plan in plans.items():
                        bf.backfill_table(
                            source_conn, warehouse_conn, settings.tenant, table, plan,
                            snapshot_lsn, settings.slot, batch_size=settings.batch_size,
                        )
                if args.backfill_only:
                    return EXIT_OK

                # 8. Steady state.
                return _stream(settings, plans, warehouse_conn, replication_conn, args)
            finally:
                replication_conn.close()
        finally:
            source_conn.close()
    finally:
        warehouse_conn.close()


def _publish_amplification(conn, tenant, tables) -> None:
    """Publish landing-zone growth so epoch duplication is visible without an investigation."""
    for table in tables:
        try:
            rows, distinct_ids, duplicates, unordered = wh.landing_amplification(conn, tenant, table)
        except Exception as exc:  # pragma: no cover - a metric must not kill the loader
            _logger.debug("amplification probe failed for %s: %s", table, exc)
            conn.rollback()
            continue
        if distinct_ids:
            m.LANDING_AMPLIFICATION.labels(tenant=tenant, source_table=table).set(
                rows / float(distinct_ids)
            )
        m.LANDING_DUPLICATE_CHANGES.labels(tenant=tenant, source_table=table).set(duplicates)
        m.LANDING_UNORDERED.labels(tenant=tenant, source_table=table).set(unordered)
        if unordered:
            _logger.warning(
                "raw.%s holds %d row(s) with a NULL _lsn for tenant %s. They still reach the marts "
                "(raw_latest coalesces a NULL to '0/0', so real CDC rows supersede them), but "
                "(_tenant_id, pk, _lsn) is no longer a total order while they exist. This loader "
                "never writes one.",
                table, unordered, tenant,
            )
        if duplicates:
            _logger.error(
                "raw.%s holds %d row(s) sharing (id, _op, _lsn) for tenant %s: the same change "
                "landed twice. There IS a known mechanism and it is not corruption: "
                "at-least-once redelivery after a restart that resumed from a confirmed_flush_lsn "
                "which had not advanced past those changes. Because it is the same WAL record the "
                "payloads are identical and the marts are unaffected, DWH's raw_latest "
                "partitioning by (_tenant_id, id) and taking rank 1. The resume floor now prevents "
                "NEW duplicates; rows already landed stay, so this figure does not self-clear and "
                "a CONSTANT value is history, not an active fault. GROWTH after a stable restart "
                "would be the real fault.",
                table, duplicates, tenant,
            )


def heartbeat_loop(settings, tables, stop_event, interval=15.0) -> None:
    """Advance ``pipeline_state.last_success_at`` on a timer, independent of message arrival.

    Deliberately a thread rather than a call inside the stream callback. psycopg2 invokes that
    callback once per decoded message, so a heartbeat living there stops exactly when the pipeline
    goes quiet -- which is the one moment it has to keep running. ``consume_stream``'s
    ``keepalive_interval`` does not help: it sends WAL keepalives to the server and never calls back
    into Python.

    It writes a HEARTBEAT, not an event: an idle pipeline and a dead one must not look alike.
    ``pipeline_state.last_success_at`` is the sole source of ``meta.is_stale`` (contract 05) and
    PPOB's SLA is 60 s, so without this a single quiet minute makes every PPOB mart report itself
    stale to the dashboard.

    Its own connection, because psycopg2 connections are not safe to share across threads.
    """
    conn = wh.connect(settings.warehouse_dsn)
    ticks = 0
    try:
        while not stop_event.is_set():
            try:
                wh.heartbeat(conn, settings.tenant, tables)
                now = time.time()
                for table in tables:
                    m.LAST_SUCCESS.labels(tenant=settings.tenant, source_table=table).set(now)
                # Amplification is a full scan per table, so it rides the heartbeat at 1-in-20
                # (~5 minutes) rather than every tick. Cheap enough to be always-on, and the point
                # is a trend, which a 5-minute sample resolves perfectly well.
                if ticks % 20 == 0:
                    _publish_amplification(conn, settings.tenant, tables)
                ticks += 1
            except Exception as exc:  # pragma: no cover - a heartbeat must not kill the loader
                _logger.warning("heartbeat: %s", exc)
                try:
                    conn.rollback()
                except Exception:
                    # The connection is unusable. Log it and keep looping: the next iteration
                    # reconnects implicitly through psycopg2's error path, and a heartbeat thread
                    # that dies takes meta.is_stale with it -- which is the failure this whole
                    # loop exists to prevent.
                    _logger.warning("heartbeat rollback failed; connection is unusable")
            stop_event.wait(interval)
    finally:
        conn.close()


def _stream(settings, plans, warehouse_conn, replication_conn, args) -> int:
    status_conn = wh.connect(settings.warehouse_dsn)
    cur = replication_conn.cursor()
    options = {"proto_version": "1", "publication_names": settings.publication}
    # The resume floor, read back out of the landing zone rather than from pipeline_state. This is
    # what makes a restart idempotent for the STREAM: feedback follows durability by design, so
    # Postgres redelivers anything committed to the warehouse but not yet confirmed. Without this
    # the redelivered changes land a second time -- observed by DWH on res_partner, two changes at
    # real LSNs with identical payloads, landed 101 seconds apart.
    resume_floor = wh.landed_max_lsn(warehouse_conn, settings.tenant, list(plans))
    _logger.info(
        "resume floor %s: changes at or below this LSN are already landed and will be dropped "
        "if Postgres redelivers them", resume_floor,
    )
    consumer = StreamConsumer(
        settings.tenant, settings.slot, plans, warehouse_conn, status_conn,
        resume_floor_lsn=resume_floor,
    )
    cur.start_replication(slot_name=settings.slot, decode=False, options=options)
    m.UP.labels(tenant=settings.tenant).set(1)
    _logger.info("streaming from slot %s on publication %s", settings.slot, settings.publication)

    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=heartbeat_loop,
        args=(settings, list(plans), heartbeat_stop),
        daemon=True,
        name="cdc-heartbeat",
    )
    heartbeat.start()

    deadline = time.time() + args.max_seconds if args.max_seconds else None

    class _Wrapped:
        def __call__(self, msg):
            consumer(msg)
            if _slot_invalidated.is_set():
                raise src.SlotInvalidated(
                    "Replication slot %s was invalidated while streaming (wal_status=lost). "
                    "Changes this consumer had not read are gone from the WAL, so continuing "
                    "would silently leave a hole in the mart. A re-snapshot is required."
                    % settings.slot
                )
            if deadline is not None and time.time() > deadline:
                raise KeyboardInterrupt
            if _stop.is_set():
                raise KeyboardInterrupt

    try:
        cur.consume_stream(_Wrapped(), keepalive_interval=5)
    except KeyboardInterrupt:
        _logger.info("stopping on request")
    except src.SlotInvalidated as exc:
        _logger.error("%s", exc)
        return EXIT_SLOT_INVALIDATED
    finally:
        heartbeat_stop.set()
        m.UP.labels(tenant=settings.tenant).set(0)
        try:
            consumer.flush(None)
        except Exception:  # pragma: no cover
            _logger.exception("final flush failed")
        status_conn.close()
        _log_slot_hygiene(settings)
    return EXIT_OK


def _log_slot_hygiene(settings) -> None:
    """Say, out loud, what the shutdown just left behind.

    The slot is deliberately **not** dropped on a clean stop: dropping it discards the resume point
    and the next start would need a full re-snapshot. The cost is that WAL now accumulates with
    nobody reading it, bounded only by the 2 GB cap. That is a backstop, not normal operation, so a
    shutdown that is meant to last must run ``--drop-slot`` and accept the re-snapshot.
    """
    try:
        conn = wh.connect(settings.source_dsn, autocommit=True)
        try:
            status = src.slot_status(conn, settings.slot)
        finally:
            conn.close()
    except Exception:  # pragma: no cover
        return
    if status["exists"]:
        _logger.warning(
            "slot %s is left in place with %d bytes of WAL retained. It keeps the resume point, "
            "but Postgres will now accumulate WAL until this consumer returns or the 2 GB cap "
            "invalidates the slot. For a long shutdown run --drop-slot and accept a re-snapshot.",
            settings.slot, status["lag_bytes"],
        )


def publication_sql(publication: str, plans) -> str:
    """Build ``CREATE PUBLICATION`` with a per-table column list from the resolved plans."""
    parts = []
    for table in sorted(plans):
        columns = publication_column_list(plans[table])
        parts.append(
            'public.%s (%s)' % (table, ", ".join('"%s"' % c for c in sorted(columns)))
        )
    return (
        "-- Generated from warehouse.column_policy by `bct-cdc --print-publication-sql`.\n"
        "-- The column list is the structural control behind contract 01's 'secret is dropped at\n"
        "-- extraction': a column absent here is never put on the wire by Postgres.\n"
        "CREATE PUBLICATION %s FOR TABLE\n  %s\n  WITH (publish = 'insert, update, delete');\n"
        % (publication, ",\n  ".join(parts))
    )


def _handle_signal(signum, frame):  # pragma: no cover
    _stop.set()


def main() -> int:
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    try:
        return run()
    except Exception:
        _logger.exception("CDC loader failed")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
