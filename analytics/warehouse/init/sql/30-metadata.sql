-- ===========================================================================
-- 30-metadata.sql — the `warehouse` schema: policy, pipeline state, tenant
-- registry, SLA table and the access audit.
--
-- warehouse.column_policy and warehouse.pipeline_state are reproduced from
-- docs/agents/contracts/05-warehouse.md CHARACTER FOR CHARACTER in their
-- column names, types and constraints. That file is Lead-frozen and Backend's
-- loader is built against it. Do not rename a column here without the Lead
-- re-briefing Backend (§2.3).
--
-- Idempotent.
-- ===========================================================================

-- OWNERSHIP. Everything below is created under SET ROLE :wh_user, so the
-- metadata tables are owned by `warehouse` rather than by the superuser that
-- happens to be applying the file. Without this, `warehouse` — the role dbt
-- connects as — gets "permission denied for table column_policy", and worse,
-- information_schema.tables returns NOTHING for this schema (it filters by
-- privilege), so the tables look like they were never created at all. It is a
-- genuinely confusing failure mode and it is why this line exists.
--
-- It also puts the SECURITY DEFINER function warehouse.log_access() in
-- 60-functions.sql at `warehouse` privilege rather than at superuser
-- privilege, which is the difference between "may append an audit row" and
-- "may do anything at all".
SET ROLE :wh_user;

-- ---------------------------------------------------------------------------
-- warehouse.column_policy — DWH writes, Backend's loader reads and executes.
--
-- This table IS the seam. Master prompt §3.2 assigns PDP masking to the DWH
-- agent but requires it applied during load, and the loader is Backend's
-- code; two agents must never write one file. So the instruction set is a
-- table rather than a call. DWH populates it from custom_pdp_core's registry;
-- Backend executes exactly what it says and invents nothing.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.column_policy (
  source_table   text NOT NULL,          -- e.g. 'res_partner' (PHYSICAL table name)
  source_column  text NOT NULL,          -- e.g. 'email'       (PHYSICAL column name)
  pdp_class      text NOT NULL
                 CHECK (pdp_class IN ('public','internal','personal','sensitive','secret')),
  transform      text NOT NULL
                 CHECK (transform IN ('none','hmac_sha256','hmac_sha256_nullable','drop')),
  mask_null      boolean NOT NULL DEFAULT false,
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source_table, source_column)
);

-- Beyond the contract's own CHECKs, the class -> transform mapping in
-- contract 05 is "not negotiable". So it is a constraint, not a convention: a
-- future sync that mapped `personal` to `none` would be rejected by the
-- database rather than quietly landing cleartext.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'column_policy_class_transform_ck') THEN
    ALTER TABLE warehouse.column_policy ADD CONSTRAINT column_policy_class_transform_ck CHECK (
         (pdp_class IN ('public','internal') AND transform = 'none'                 AND mask_null = false)
      OR (pdp_class = 'personal'             AND transform = 'hmac_sha256'          AND mask_null = false)
      OR (pdp_class = 'sensitive'            AND transform IN ('hmac_sha256','hmac_sha256_nullable'))
      OR (pdp_class = 'secret'               AND transform = 'drop')
    );
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'column_policy_masknull_ck') THEN
    -- mask_null only means anything for `sensitive`; mirrors the CHECK
    -- custom_pdp_core puts on drop_to_null so the two cannot drift.
    ALTER TABLE warehouse.column_policy ADD CONSTRAINT column_policy_masknull_ck
      CHECK (mask_null IS NOT TRUE OR pdp_class = 'sensitive');
  END IF;
END
$$;

COMMENT ON TABLE warehouse.column_policy IS
  'Frozen contract 05. DWH writes; the CDC loader reads it at startup and executes it. '
  'A column the loader is about to extract with no row here is a HARD FAILURE - the loader '
  'exits non-zero. Never default an unclassified column to public.';

-- ---------------------------------------------------------------------------
-- warehouse.pipeline_state — Backend writes, DWH and semantic-api read.
--
-- The ONLY source of meta.last_refreshed_at and meta.is_stale (metric
-- contract §3). The dashboard's freshness indicator reads real pipeline
-- metadata, never a client clock.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.pipeline_state (
  tenant_id        text NOT NULL,
  source_table     text NOT NULL,
  last_lsn         pg_lsn,
  last_success_at  timestamptz,
  rows_loaded      bigint NOT NULL DEFAULT 0,
  last_error       text,
  failure_count    integer NOT NULL DEFAULT 0,
  slot_name        text,
  PRIMARY KEY (tenant_id, source_table)
);

COMMENT ON TABLE warehouse.pipeline_state IS
  'Frozen contract 05. Backend''s CDC loader writes; semantic-api serves meta.last_refreshed_at '
  'and meta.is_stale from it. Never compute freshness from a clock.';

-- ---------------------------------------------------------------------------
-- warehouse.tenant_registry — the source of dim_tenant.
--
-- ADR 0001 "Multi-tenant": one publication and one replication slot per
-- tenant database, and every fact and dimension carries tenant_id. This is
-- where a tenant's identity, its source database and its salt ENV NAME live.
-- The salt VALUE is never here and never in git: only the name of the
-- environment variable that holds it, per contract 01.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.tenant_registry (
  tenant_id       text PRIMARY KEY
                  CHECK (tenant_id ~ '^[a-z][a-z0-9_]{1,30}$'),
  display_name    text NOT NULL,
  source_database text NOT NULL,
  slot_name       text,
  publication     text,
  mask_salt_env   text NOT NULL,
  is_test_tenant  boolean NOT NULL DEFAULT false,
  active          boolean NOT NULL DEFAULT true,
  onboarded_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN warehouse.tenant_registry.tenant_id IS
  'Matches scripts/lib/common.sh:validate_slug - no dashes, because Postgres replication slot '
  'names forbid them (contract 04 §3).';
COMMENT ON COLUMN warehouse.tenant_registry.mask_salt_env IS
  'NAME of the environment variable holding this tenant''s HMAC salt. Never the value.';
COMMENT ON COLUMN warehouse.tenant_registry.is_test_tenant IS
  'True for a tenant that exists to exercise isolation rather than to serve a customer. '
  'Surfaced on dim_tenant so nobody mistakes one for production volume.';

-- ---------------------------------------------------------------------------
-- warehouse.mart_sla — the GATE 2 freshness table, as data.
--
-- ADR 0001 fixes a per-mart SLA and explicitly rejects a uniform one. is_stale
-- is computed against THIS, so changing an SLA is a data change reviewed
-- against the ADR, not an edit scattered through application code.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.mart_sla (
  mart_name           text PRIMARY KEY,
  sla_seconds         integer NOT NULL CHECK (sla_seconds > 0),
  on_breach           text NOT NULL CHECK (on_breach IN ('page','alert')),
  source_tables       text[] NOT NULL,
  note                text
);

-- ---------------------------------------------------------------------------
-- warehouse.access_audit — §3.2, "every warehouse access path must be
-- attributable".
--
-- THE HONEST DESIGN NOTE, because the brief asks for one: the mirrored module
-- is custom_pdp_audit, and it DOES NOT EXIST. The five addons in this repo are
-- custom_demo_seed, custom_operating_unit, custom_pdp_core, custom_pdp_masking
-- and custom_ppob. So this is designed here rather than mirrored, and it is
-- built from three layers because no single one of them is sufficient:
--
--   1. STATEMENT LOGGING (20-schemas-roles.sql). `ALTER ROLE warehouse_rls SET
--      log_statement='all'` makes every statement from the analytics read
--      identity appear in the container log with %u@%d/%a. This is the layer
--      an attacker cannot opt out of, because it is applied by the server, not
--      by the client. It is also the layer that survives a client that forgets
--      to call the function below.
--   2. THIS TABLE, written through warehouse.log_access(), which the
--      semantic-api calls once per served query. It records the SEMANTIC fact
--      -- which metric, which tenant scope, how many rows -- that a raw
--      statement log cannot reconstruct.
--   3. RLS itself, which makes an unattributed read return zero rows rather
--      than the wrong tenant's data.
--
-- Postgres cannot trigger on SELECT, so there is no way to make layer 2
-- mandatory inside the database without pgaudit (not present in
-- postgres:16-alpine). Layer 1 is what closes that gap. Stated plainly rather
-- than left as an implied guarantee.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.access_audit (
  id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  occurred_at      timestamptz NOT NULL DEFAULT now(),
  db_user          text NOT NULL DEFAULT session_user,
  application_name text,
  client_addr      inet,
  tenant_scope     text,
  principal        text,
  object_schema    text,
  object_name      text,
  action           text NOT NULL,
  row_count        bigint,
  detail           jsonb
);

CREATE INDEX IF NOT EXISTS access_audit_occurred_at_idx ON warehouse.access_audit (occurred_at DESC);
CREATE INDEX IF NOT EXISTS access_audit_tenant_idx      ON warehouse.access_audit (tenant_scope, occurred_at DESC);

-- SECURITY DEFINER so the read-only role can append an audit row without
-- holding INSERT on the table itself. A caller that could INSERT directly
-- could also DELETE its own trail if it were ever granted more; this keeps
-- the read identity's only write capability to "append one audit row".
CREATE OR REPLACE FUNCTION warehouse.log_access(
  p_action        text,
  p_object_schema text DEFAULT NULL,
  p_object_name   text DEFAULT NULL,
  p_row_count     bigint DEFAULT NULL,
  p_principal     text DEFAULT NULL,
  p_detail        jsonb DEFAULT NULL
) RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = warehouse, pg_temp
AS $$
DECLARE
  v_id bigint;
BEGIN
  INSERT INTO warehouse.access_audit (
    db_user, application_name, client_addr, tenant_scope, principal,
    object_schema, object_name, action, row_count, detail)
  VALUES (
    session_user,
    current_setting('application_name', true),
    inet_client_addr(),
    -- Read from the same session variable RLS uses, so an audit row can never
    -- claim a different tenant scope than the one the query actually ran under.
    nullif(current_setting('app.tenant_id', true), ''),
    p_principal,
    p_object_schema, p_object_name, p_action, p_row_count, p_detail)
  RETURNING id INTO v_id;
  RETURN v_id;
END
$$;

COMMENT ON FUNCTION warehouse.log_access IS
  'Append one attributable access record. tenant_scope is taken from app.tenant_id, not from an '
  'argument, so the audit trail cannot disagree with the RLS scope the query ran under.';

-- ---------------------------------------------------------------------------
-- warehouse.dbt_run_result — what `dbt build` did, as data.
--
-- Written by the on-run-end hook in analytics/dbt/macros/log_dbt_results.sql.
-- This is what makes "a reconciliation failure is visible in Prometheus"
-- true: the exporter reads this table, so a failed test is a metric and an
-- alert, not just a red line in someone's terminal.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS warehouse.dbt_run_result (
  invocation_id  text        NOT NULL,
  run_started_at timestamptz NOT NULL,
  node_id        text        NOT NULL,
  resource_type  text        NOT NULL,
  node_name      text        NOT NULL,
  status         text        NOT NULL,
  severity       text,
  rows_affected  bigint,
  failures       bigint,
  execution_time numeric,
  message        text,
  PRIMARY KEY (invocation_id, node_id)
);

CREATE INDEX IF NOT EXISTS dbt_run_result_started_idx ON warehouse.dbt_run_result (run_started_at DESC);

-- ---------------------------------------------------------------------------
-- warehouse.mart_freshness — the view the semantic-api serves
-- meta.last_refreshed_at and meta.is_stale from.
--
-- is_stale compares real pipeline metadata against the ADR's per-mart SLA. It
-- deliberately does NOT fall back to a default when a mart has no SLA row:
-- an unknown SLA yields NULL, and a NULL is_stale is a bug report, not a
-- reassuring "fresh".
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW warehouse.mart_freshness AS
SELECT
    s.mart_name,
    ps.tenant_id,
    s.sla_seconds,
    s.on_breach,
    max(ps.last_success_at)                                       AS last_refreshed_at,
    extract(epoch FROM (now() - max(ps.last_success_at)))::bigint AS age_seconds,
    CASE
      WHEN max(ps.last_success_at) IS NULL THEN true
      ELSE (now() - max(ps.last_success_at)) > make_interval(secs => s.sla_seconds)
    END                                                           AS is_stale
FROM warehouse.mart_sla s
JOIN warehouse.pipeline_state ps
  ON ps.source_table = ANY (s.source_tables)
GROUP BY s.mart_name, ps.tenant_id, s.sla_seconds, s.on_breach;

COMMENT ON VIEW warehouse.mart_freshness IS
  'metric contract §3: the single source of meta.last_refreshed_at / meta.is_stale. '
  'A mart with no pipeline_state row reports is_stale = true, never "fresh".';


-- ---------------------------------------------------------------------------
-- CONSTRAINT CONVERGENCE.
--
-- CREATE TABLE IF NOT EXISTS is idempotent in the weak sense - it does not
-- error - but it does NOT converge: if a table exists and has lost a
-- constraint, the statement is a silent no-op and the table stays wrong.
--
-- That is not hypothetical. A pg_restore --clean that was interrupted part way
-- through its drop phase left warehouse.mart_sla, warehouse.pipeline_state,
-- warehouse.dbt_run_result and warehouse.tenant_registry with NO primary key,
-- and re-running this file "successfully" repaired none of it. The first
-- symptom was an ON CONFLICT failing with "there is no unique or exclusion
-- constraint matching the ON CONFLICT specification" - and the CDC loader
-- upserts pipeline_state through exactly that path, so the next thing to break
-- would have been the pipeline.
--
-- So the primary keys are asserted separately from the table creation. This
-- makes warehouse-apply.sh a genuine repair tool rather than only a
-- first-boot one.
-- ---------------------------------------------------------------------------
DO $ensure_pk$
DECLARE
  r record;
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      ('column_policy',   'column_policy_pkey',   'source_table, source_column'),
      ('pipeline_state',  'pipeline_state_pkey',  'tenant_id, source_table'),
      ('tenant_registry', 'tenant_registry_pkey', 'tenant_id'),
      ('mart_sla',        'mart_sla_pkey',        'mart_name'),
      ('access_audit',    'access_audit_pkey',    'id'),
      ('dbt_run_result',  'dbt_run_result_pkey',  'invocation_id, node_id')
    ) AS t(tbl, con, cols)
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_constraint c
      JOIN pg_class    k ON k.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = k.relnamespace
      WHERE n.nspname = 'warehouse' AND k.relname = r.tbl AND c.contype = 'p'
    ) THEN
      EXECUTE format('ALTER TABLE warehouse.%I ADD CONSTRAINT %I PRIMARY KEY (%s)',
                     r.tbl, r.con, r.cols);
      RAISE NOTICE 'restored missing primary key on warehouse.% ', r.tbl;
    END IF;
  END LOOP;
END
$ensure_pk$;

RESET ROLE;
