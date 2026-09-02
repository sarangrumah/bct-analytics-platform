-- ---------------------------------------------------------------------------
-- Cluster roles.
--
-- Three roles, three jobs, no overlap:
--
--   odoo              cluster owner, created by initdb from POSTGRES_USER.
--                     Superuser: Odoo creates databases, installs extensions
--                     and runs schema migrations. Not narrowed here.
--
--   warehouse_reader  the CDC / analytics identity. SELECT + REPLICATION and
--                     nothing else. Master prompt section 2, "read-only by
--                     construction": there must be no write path from the
--                     warehouse back into Odoo -- not by policy, but because
--                     the role is structurally incapable of it. Proven by
--                     scripts/warehouse-reader-check.sh.
--
--   metrics_exporter  postgres_exporter identity. pg_monitor gives read access
--                     to pg_stat_* and pg_replication_slots (which is how slot
--                     lag alerting works) with no table data access at all.
--
-- psql does not interpolate :'var' inside dollar-quoted strings, so a DO block
-- cannot carry the password. format() + \gexec is used instead: it builds the
-- statement as text and executes it, with %L doing the literal quoting and %I
-- the identifier quoting.
-- ---------------------------------------------------------------------------

\echo '-- roles: ensuring warehouse_reader'

-- Create only if absent...
SELECT format(
    'CREATE ROLE %I LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
    :'reader_user', :'reader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader_user')
\gexec

-- ...then converge the attributes unconditionally, so re-running this file can
-- never leave the role holding a privilege it picked up by hand.
SELECT format(
    'ALTER ROLE %I WITH LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
    :'reader_user', :'reader_password')
\gexec

SELECT format(
    'COMMENT ON ROLE %I IS %L',
    :'reader_user',
    'CDC/analytics identity. SELECT + REPLICATION only. Never grant INSERT, UPDATE, DELETE, TRUNCATE or CREATE to this role - see docs/agents/contracts/04-platform.md and docs/adr/0001-analytics-warehouse.md.')
\gexec

\echo '-- roles: ensuring metrics_exporter'

SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD %L',
    :'exporter_user', :'exporter_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'exporter_user')
\gexec

SELECT format(
    'ALTER ROLE %I WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT PASSWORD %L',
    :'exporter_user', :'exporter_password')
\gexec

-- pg_monitor = pg_read_all_settings + pg_read_all_stats + pg_stat_scan_tables.
-- Statistics and replication-slot state; no table data.
SELECT format('GRANT pg_monitor TO %I', :'exporter_user')
\gexec

SELECT format(
    'COMMENT ON ROLE %I IS %L',
    :'exporter_user',
    'postgres_exporter identity. pg_monitor only - statistics and replication slot state, no table data.')
\gexec
