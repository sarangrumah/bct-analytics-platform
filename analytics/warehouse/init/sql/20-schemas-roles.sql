-- ===========================================================================
-- 20-schemas-roles.sql — the four schemas of contract 05, and the three roles
-- that make the RLS story provable rather than decorative.
--
-- Idempotent. Requires psql variables:
--     :wh_user :wh_password :loader_user :loader_password
--     :rls_user :rls_password
-- injected with -v by init/00-bootstrap.sh and bin/warehouse-apply.sh, so no
-- credential is ever written into a tracked SQL file.
--
-- IMPLEMENTATION NOTE, learned the hard way: psql does NOT interpolate :vars
-- inside a dollar-quoted string, so `DO $$ ... :'wh_user' ... $$` is a syntax
-- error at the colon, not a substitution. Everything that needs a variable is
-- therefore built with format() at the top level and run through \gexec.
-- ===========================================================================

-- --- Roles -----------------------------------------------------------------
--
-- WHY THREE ROLES. Postgres row security has one rule that decides this
-- design: a SUPERUSER, and any role holding BYPASSRLS, bypasses row security
-- unconditionally and no policy can stop it. If dbt and the dashboard shared
-- one superuser identity — the obvious single-role design — then every RLS
-- test in this project would pass while proving nothing at all.
--
--   warehouse_admin  the container's POSTGRES_USER. Superuser. Runs this file
--                    and the backups. Nothing queries data as this role.
--   warehouse        NOSUPERUSER NOBYPASSRLS. Owns the schemas; dbt connects
--                    as it. Subject to RLS on its own tables (they are FORCE
--                    ROW LEVEL SECURITY), and given an explicitly scoped
--                    "unscoped only while app.tenant_id is unset" policy so a
--                    transformation can still see every tenant. The moment a
--                    caller sets app.tenant_id, this role is constrained like
--                    any other.
--   warehouse_loader NOSUPERUSER NOBYPASSRLS. Backend's CDC loader. It may
--                    INSERT into raw.*, read and write warehouse.pipeline_state
--                    and read warehouse.column_policy. It holds no CREATE and
--                    cannot reach marts at all: a loader that could create its
--                    own landing table could land a column with no policy row,
--                    which is the one thing contract 01 says must be
--                    structurally impossible.
--   warehouse_rls    NOSUPERUSER NOBYPASSRLS, SELECT only. The identity the
--                    semantic-api and the metrics exporter use. It has NO
--                    unscoped policy of any kind, so with app.tenant_id unset
--                    it reads zero rows. Fail closed, per master prompt §3.3.

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'wh_user', :'wh_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'wh_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'wh_user', :'wh_password')
\gexec

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'loader_user', :'loader_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'loader_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'loader_user', :'loader_password')
\gexec

SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'rls_user', :'rls_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'rls_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS PASSWORD %L',
              :'rls_user', :'rls_password')
\gexec

-- Statement ceilings per role, not globally.
--
-- A dashboard query that has not answered in 60 seconds is not going to
-- answer; it is going to hold a connection on a 1 GiB container while the
-- user reloads the page and issues another. dbt legitimately needs longer for
-- a full refresh, so the two get different ceilings.
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'wh_user', '600000')  \gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'rls_user', '60000')  \gexec
-- The loader runs long COPY/INSERT batches during a backfill; 10 min matches dbt.
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'loader_user', '600000') \gexec
SELECT format('ALTER ROLE %I SET idle_in_transaction_session_timeout = %L', :'rls_user', '30000') \gexec

-- ATTRIBUTABILITY (master prompt §3.2). Every statement issued by the
-- analytics read identity is logged with its user, database and
-- application_name (log_line_prefix carries %u@%d/%a). The container's stderr
-- goes to the json-file driver and on to Loki via promtail, so a warehouse
-- read is attributable even when the caller never invoked
-- warehouse.log_access(). This is deliberately set ONLY on the read role:
-- turning it on for dbt would log every model's SQL on every run and bury the
-- thing worth seeing.
SELECT format('ALTER ROLE %I SET log_statement = %L', :'rls_user', 'all') \gexec
SELECT format('ALTER ROLE %I SET log_min_duration_statement = %L', :'rls_user', '0') \gexec

-- --- Schemas ---------------------------------------------------------------
-- Names are frozen by contract 05 and are not negotiable here.
--   raw        append-only landing zone, one table per replicated source table
--   staging    dbt stg_ models
--   marts      dbt int_/dim_/fct_/mart_ models
--   warehouse  pipeline metadata and policy
--   snapshots  dbt snapshot storage for the SCD2 dimensions. Kept out of
--              marts on purpose: the SCD2 history table is an implementation
--              detail of dim_partner / dim_product, not a published surface.
--   src_<tenant> FDW foreign tables, created later by bin/gen_foreign_tables.py

SELECT format('CREATE SCHEMA IF NOT EXISTS %I AUTHORIZATION %I', s, :'wh_user')
FROM unnest(ARRAY['raw','staging','marts','warehouse','snapshots']) AS s
\gexec

-- Nobody creates objects in public. Postgres 16 already revokes this from
-- PUBLIC by default; making it explicit means a restore from an older dump
-- cannot silently reintroduce a shadowing-function surface.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
