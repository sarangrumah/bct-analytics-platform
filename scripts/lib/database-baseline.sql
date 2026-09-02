-- ---------------------------------------------------------------------------
-- Per-database baseline. Applied by scripts/init-db.sh and
-- scripts/tenant-provision.sh to EVERY Odoo database, immediately after Odoo
-- has created it.
--
-- It cannot live in postgres/init/, because that runs once at cluster first
-- boot when no tenant database exists yet. It cannot ride on template1 either:
-- odoo.conf sets db_template = template0 on purpose, so that no privilege ever
-- arrives in a tenant database by accident.
--
-- Run with:
--     psql -v ON_ERROR_STOP=1 -v dbname=<db> -v reader=<role> -f this-file
--
-- Idempotent: safe to re-run after a module install adds tables.
-- ---------------------------------------------------------------------------

\echo '-- baseline: extensions'

-- pg_stat_statements is preloaded cluster-wide (postgresql.conf) but the view
-- is per-database.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
-- Used by Odoo's search and by mart-side fuzzy matching later.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;

\echo '-- baseline: revoking PUBLIC'

-- PUBLIC is an implicit member of every role, including warehouse_reader.
-- Leaving CONNECT/TEMP on PUBLIC would hand the reader a temp-table write path
-- and quietly break "read-only by construction".
REVOKE ALL ON DATABASE :"dbname" FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

\echo '-- baseline: warehouse_reader grants (SELECT only)'

-- CONNECT, and nothing else, at the database level. Notably NOT TEMPORARY.
GRANT CONNECT ON DATABASE :"dbname" TO :"reader";

-- USAGE lets the role resolve names in the schema; it does not permit CREATE.
GRANT USAGE ON SCHEMA public TO :"reader";

-- Existing objects.
GRANT SELECT ON ALL TABLES    IN SCHEMA public TO :"reader";
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO :"reader";

-- Future objects. Odoo creates a table every time a module is installed; a
-- one-shot GRANT would silently stop covering the schema on the next install
-- and CDC would fail on a table nobody thought about.
-- Scoped "FOR ROLE odoo" because default privileges apply per creating role,
-- and Odoo always connects as odoo.
ALTER DEFAULT PRIVILEGES FOR ROLE odoo IN SCHEMA public
    GRANT SELECT ON TABLES TO :"reader";
ALTER DEFAULT PRIVILEGES FOR ROLE odoo IN SCHEMA public
    GRANT SELECT ON SEQUENCES TO :"reader";

\echo '-- baseline: explicit write denial'

-- Belt and braces. None of these were granted, but revoking makes the intent
-- explicit and survives someone running a well-meaning "GRANT ALL" later.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON ALL TABLES IN SCHEMA public FROM :"reader";
REVOKE CREATE ON SCHEMA public FROM :"reader";
REVOKE USAGE, UPDATE ON ALL SEQUENCES IN SCHEMA public FROM :"reader";
REVOKE TEMPORARY ON DATABASE :"dbname" FROM :"reader";

-- ---------------------------------------------------------------------------
-- PHASE 3 CDC HOOK — deliberately NOT implemented here.
--
-- docs/adr/0001-analytics-warehouse.md specifies one publication and one
-- replication slot per tenant database. Both are created by
-- scripts/tenant-provision.sh (see the marked block in that file), not here,
-- because:
--   * a publication names specific tables, which do not all exist until the
--     tenant's modules are installed;
--   * creating a slot has a side effect on the cluster (WAL retention starts
--     immediately), so it must be an explicit provisioning step and not a
--     side effect of applying grants.
--
-- What Phase 3 will add, for reference:
--     CREATE PUBLICATION bct_cdc_<slug> FOR TABLE res_partner, ...;
--     SELECT pg_create_logical_replication_slot('bct_slot_<slug>', 'pgoutput');
-- warehouse_reader already holds REPLICATION, so no privilege change is
-- needed at that point.
-- ---------------------------------------------------------------------------

\echo '-- baseline: done'
