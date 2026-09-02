-- ============================================================================
-- tenant_registry — the ATHERA control plane.
--
-- Ported from the platform repo's postgres/init/04-tenant-registry-schema.sql
-- on 2026-09-01, with three deliberate changes recorded below.
--
-- WHAT THIS IS. The single source of truth for "which clients exist, what did
-- we do to them, and are they paid up". The diagram draws one Postgres under
-- Super Admin CMS; this is that Postgres. tenant-orchestrator is the sole
-- writer. custom_super_admin reads it through a least-privilege role and
-- mirrors it into an Odoo model for the UI.
--
-- CHANGE 1 — IT LIVES IN THE ADMIN ODOO DATABASE, NOT A SEPARATE MASTER DB.
-- The source put this schema in the cluster's maintenance database while
-- custom_super_admin ran in a different one, and that module reads the audit
-- view with a plain cr.execute on its own cursor. Postgres has no cross-
-- database SELECT: same cluster is necessary but nowhere near sufficient, and
-- the query would have needed a postgres_fdw or dblink that nothing sets up.
-- So the schema is created inside the ATHERA admin database — the same one
-- custom_super_admin is installed in — and the read works natively. This is
-- also the reason the control plane cannot be moved to its own VPS away from
-- the admin Odoo; that constraint is recorded in .env.example.
--
-- CHANGE 2 — THE SLUG RULE IS THIS REPO'S, NOT THE SOURCE'S.
-- The source allowed 63 characters. Here a slug becomes a database name AND a
-- replication slot name, and Postgres slot names forbid dashes and are
-- shorter, so scripts/lib/common.sh validate_slug enforces
-- ^[a-z][a-z0-9_]{1,30}$ and this CHECK matches it exactly. Two different
-- rules for one identifier is how a tenant gets provisioned that CDC can
-- never follow.
--
-- CHANGE 3 — SUBSCRIPTION IS MODELLED, NOT A BARE STRING.
-- The source carried plan_tier VARCHAR(32) and nothing else, which cannot
-- answer the diagram's "Active?" decision. There is a real plans table and a
-- real validity window here, because that decision gates every client login.
-- ============================================================================

-- digest() for the action-log hash chain.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS tenant_registry;
COMMENT ON SCHEMA tenant_registry IS 'ATHERA control plane: client lifecycle, subscription and audit';

-- ============================================================================
-- Lifecycle state
-- ============================================================================
DO $do$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace
                 WHERE t.typname = 'tenant_state' AND n.nspname = 'tenant_registry') THEN
    CREATE TYPE tenant_registry.tenant_state AS ENUM (
      'provisioning',
      'active',
      'suspended',
      'archived',
      'failed'
    );
  END IF;
END$do$;

-- ============================================================================
-- Plans — what a client can buy.
--
-- products is the entitlement, and it is an array rather than three booleans
-- so that adding a fourth ATHERA product is a data change, not a migration.
-- The values are constrained to a fixed set: a typo in an entitlement is a
-- silent grant or a silent denial, which is the worst failure this table has.
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registry.plans (
  code          VARCHAR(32) PRIMARY KEY CHECK (code ~ '^[a-z][a-z0-9_]{1,31}$'),
  display_name  VARCHAR(128) NOT NULL,
  products      TEXT[] NOT NULL DEFAULT '{}',
  price_month   NUMERIC(14,2),
  currency      VARCHAR(3) NOT NULL DEFAULT 'IDR',
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CONSTRAINT plans_products_known CHECK (
    products <@ ARRAY['insight','odoo','agent']::text[]
  )
);

COMMENT ON COLUMN tenant_registry.plans.products IS
  'Entitlement: which ATHERA products this plan grants. Subset of insight/odoo/agent.';

INSERT INTO tenant_registry.plans (code, display_name, products, price_month) VALUES
  ('trial',     'Trial',            ARRAY['insight'],                0),
  ('insight',   'ATHERA Insight',   ARRAY['insight'],                NULL),
  ('odoo_care', 'Odoo + Odoo Care', ARRAY['odoo'],                   NULL),
  ('suite',     'ATHERA Suite',     ARRAY['insight','odoo','agent'], NULL)
ON CONFLICT (code) DO NOTHING;

-- ============================================================================
-- Tenants — one row per client.
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registry.tenants (
  id                BIGSERIAL PRIMARY KEY,
  slug              VARCHAR(31)  NOT NULL UNIQUE
                    CHECK (slug ~ '^[a-z][a-z0-9_]{1,30}$'),
  display_name      VARCHAR(128) NOT NULL,
  db_name           VARCHAR(63)  NOT NULL UNIQUE,
  state             tenant_registry.tenant_state NOT NULL DEFAULT 'provisioning',

  -- Subscription. plan_code is the entitlement; valid_until is the clock.
  -- Both are consulted by the gateway on every login and every refresh, which
  -- is what makes the diagram's "Active?" decision real rather than advisory.
  plan_code         VARCHAR(32) REFERENCES tenant_registry.plans(code),
  valid_until       TIMESTAMPTZ,
  trial_ends_at     TIMESTAMPTZ,

  -- A client that subscribes only to Insight has no Odoo database of its own;
  -- it brings its own application. This column is what tells the provisioner
  -- and the CDC onboarding which of the two shapes it is dealing with.
  insight_source_kind VARCHAR(16) NOT NULL DEFAULT 'odoo'
                      CHECK (insight_source_kind IN ('odoo','external_postgres')),

  csm_user_id       INTEGER,
  contact_email     VARCHAR(254),
  contact_phone     VARCHAR(32),

  backup_schedule_cron   VARCHAR(64) NOT NULL DEFAULT '0 2 * * *',
  backup_retention_daily INTEGER NOT NULL DEFAULT 30,
  last_backup_at         TIMESTAMPTZ,
  last_backup_size_bytes BIGINT,
  last_backup_id         VARCHAR(128),

  max_db_connections INTEGER NOT NULL DEFAULT 20,
  features           JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at        TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  activated_at      TIMESTAMPTZ,
  suspended_at      TIMESTAMPTZ,
  archived_at       TIMESTAMPTZ,
  purge_after       TIMESTAMPTZ,
  last_seen_at      TIMESTAMPTZ,
  notes             TEXT
);

CREATE INDEX IF NOT EXISTS tenants_state_idx ON tenant_registry.tenants(state);
CREATE INDEX IF NOT EXISTS tenants_csm_idx   ON tenant_registry.tenants(csm_user_id);
CREATE INDEX IF NOT EXISTS tenants_purge_idx ON tenant_registry.tenants(purge_after)
  WHERE state = 'archived';

COMMENT ON TABLE tenant_registry.tenants IS
  'Client lifecycle and entitlement. Sole writer is tenant-orchestrator.';

-- ============================================================================
-- is_active(slug) — the "Active?" decision, in exactly one place.
--
-- The gateway, the orchestrator and the super-admin UI all need this answer,
-- and three implementations of it would eventually disagree. Disagreement here
-- means a suspended client keeps a working dashboard, so it is a function and
-- not a convention.
--
-- Fail-closed: an unknown slug is NOT active. A tenant with no valid_until is
-- open-ended, which is what a manually managed enterprise account looks like;
-- a tenant whose valid_until has passed is not active whatever its state says.
-- ============================================================================
CREATE OR REPLACE FUNCTION tenant_registry.is_active(p_slug TEXT)
RETURNS BOOLEAN
LANGUAGE sql STABLE AS $fn$
  SELECT COALESCE(
    (SELECT t.state = 'active'
        AND (t.valid_until IS NULL OR t.valid_until > now())
       FROM tenant_registry.tenants t
      WHERE t.slug = p_slug),
    false);
$fn$;

-- entitlements(slug) — which products this client may open. Empty for an
-- unknown or unplanned tenant, again fail-closed.
CREATE OR REPLACE FUNCTION tenant_registry.entitlements(p_slug TEXT)
RETURNS TEXT[]
LANGUAGE sql STABLE AS $fn$
  SELECT COALESCE(
    (SELECT p.products
       FROM tenant_registry.tenants t
       JOIN tenant_registry.plans p ON p.code = t.plan_code
      WHERE t.slug = p_slug AND p.is_active),
    ARRAY[]::text[]);
$fn$;

-- ============================================================================
-- Action log — append-only, hash-chained.
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registry.action_log (
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
  -- NO FOREIGN KEY, deliberately. It had ON DELETE SET NULL, which needs an
  -- UPDATE on this table -- and the append-only trigger below refuses every
  -- UPDATE. The two cancelled out: deleting a tenant that had ever been acted
  -- on failed with "UPDATE ONLY tenant_registry.action_log SET tenant_id =
  -- NULL", and the only ways out were to drop the audit guarantee or to leak
  -- rows forever. An append-only log should not hold a mutable reference into
  -- a table that can be deleted; tenant_slug is the durable identifier and
  -- survives the tenant it names, which is what an audit trail is for.
  tenant_id   BIGINT,
  tenant_slug VARCHAR(63),
  action      VARCHAR(64)  NOT NULL,
  actor       VARCHAR(128) NOT NULL,
  detail      JSONB,
  outcome     VARCHAR(16)  NOT NULL CHECK (outcome IN ('success','failure','partial')),
  error       TEXT,
  prev_hash   BYTEA,
  hash        BYTEA NOT NULL
);

CREATE INDEX IF NOT EXISTS action_log_ts_idx     ON tenant_registry.action_log(ts DESC);
CREATE INDEX IF NOT EXISTS action_log_tenant_idx ON tenant_registry.action_log(tenant_id);
CREATE INDEX IF NOT EXISTS action_log_action_idx ON tenant_registry.action_log(action);

CREATE OR REPLACE FUNCTION tenant_registry._compute_action_hash(
  p_prev_hash BYTEA, p_ts TIMESTAMPTZ, p_tenant_slug TEXT, p_action TEXT,
  p_actor TEXT, p_detail JSONB, p_outcome TEXT, p_error TEXT
) RETURNS BYTEA LANGUAGE plpgsql IMMUTABLE AS $fn$
DECLARE payload BYTEA;
BEGIN
  payload := COALESCE(p_prev_hash, '\x'::bytea)
          || convert_to(
              COALESCE(to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '')
              || '|' || COALESCE(p_tenant_slug, '')
              || '|' || COALESCE(p_action, '')
              || '|' || COALESCE(p_actor, '')
              || '|' || COALESCE(p_detail::text, '')
              || '|' || COALESCE(p_outcome, '')
              || '|' || COALESCE(p_error, ''),
            'UTF8');
  RETURN digest(payload, 'sha256');
END$fn$;

CREATE OR REPLACE FUNCTION tenant_registry._action_log_before_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $fn$
DECLARE v_prev BYTEA;
BEGIN
  SELECT hash INTO v_prev FROM tenant_registry.action_log ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := v_prev;
  NEW.hash := tenant_registry._compute_action_hash(
    NEW.prev_hash, NEW.ts, NEW.tenant_slug, NEW.action,
    NEW.actor, NEW.detail, NEW.outcome, NEW.error);
  RETURN NEW;
END$fn$;

DROP TRIGGER IF EXISTS action_log_before_insert ON tenant_registry.action_log;
CREATE TRIGGER action_log_before_insert
  BEFORE INSERT ON tenant_registry.action_log
  FOR EACH ROW EXECUTE FUNCTION tenant_registry._action_log_before_insert();

-- Append-only is enforced by the database, not by the orchestrator being
-- careful. An audit log its own writer can rewrite is not an audit log.
CREATE OR REPLACE FUNCTION tenant_registry._action_log_block_modify()
RETURNS TRIGGER LANGUAGE plpgsql AS $fn$
BEGIN
  RAISE EXCEPTION 'tenant_registry.action_log is append-only — % rejected', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END$fn$;

DROP TRIGGER IF EXISTS action_log_block_update ON tenant_registry.action_log;
CREATE TRIGGER action_log_block_update BEFORE UPDATE ON tenant_registry.action_log
  FOR EACH ROW EXECUTE FUNCTION tenant_registry._action_log_block_modify();
DROP TRIGGER IF EXISTS action_log_block_delete ON tenant_registry.action_log;
CREATE TRIGGER action_log_block_delete BEFORE DELETE ON tenant_registry.action_log
  FOR EACH ROW EXECUTE FUNCTION tenant_registry._action_log_block_modify();
DROP TRIGGER IF EXISTS action_log_block_truncate ON tenant_registry.action_log;
CREATE TRIGGER action_log_block_truncate BEFORE TRUNCATE ON tenant_registry.action_log
  FOR EACH STATEMENT EXECUTE FUNCTION tenant_registry._action_log_block_modify();

-- Hex-encoded view: this is what custom_super_admin's cron reads.
CREATE OR REPLACE VIEW tenant_registry.action_log_v AS
SELECT id, ts, tenant_id, tenant_slug, action, actor, detail, outcome, error,
       encode(prev_hash, 'hex') AS prev_hash_hex,
       encode(hash, 'hex')      AS hash_hex
  FROM tenant_registry.action_log;

CREATE OR REPLACE FUNCTION tenant_registry.verify_action_chain(p_limit INTEGER DEFAULT NULL)
RETURNS TABLE(broken_id BIGINT, expected_hash TEXT, actual_hash TEXT)
LANGUAGE plpgsql STABLE AS $fn$
DECLARE r RECORD; v_expected BYTEA; v_prev BYTEA := NULL;
BEGIN
  FOR r IN SELECT * FROM tenant_registry.action_log ORDER BY id ASC
           LIMIT COALESCE(p_limit, 2147483647)
  LOOP
    v_expected := tenant_registry._compute_action_hash(
      v_prev, r.ts, r.tenant_slug, r.action, r.actor, r.detail, r.outcome, r.error);
    IF v_expected <> r.hash THEN
      broken_id := r.id;
      expected_hash := encode(v_expected, 'hex');
      actual_hash := encode(r.hash, 'hex');
      RETURN NEXT;
    END IF;
    v_prev := r.hash;
  END LOOP;
  RETURN;
END$fn$;

-- ============================================================================
-- Backup ledger
-- ============================================================================
CREATE TABLE IF NOT EXISTS tenant_registry.backups (
  id              BIGSERIAL PRIMARY KEY,
  tenant_id       BIGINT NOT NULL REFERENCES tenant_registry.tenants(id) ON DELETE CASCADE,
  tenant_slug     VARCHAR(63) NOT NULL,
  kind            VARCHAR(16) NOT NULL CHECK (kind IN ('daily','monthly','yearly','manual')),
  started_at      TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  finished_at     TIMESTAMPTZ,
  size_bytes      BIGINT,
  path            VARCHAR(512),
  checksum_sha256 VARCHAR(64),
  outcome         VARCHAR(16) NOT NULL DEFAULT 'pending'
                  CHECK (outcome IN ('pending','success','failure')),
  error           TEXT,
  expires_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS backups_tenant_idx ON tenant_registry.backups(tenant_id, started_at DESC);
CREATE INDEX IF NOT EXISTS backups_expire_idx ON tenant_registry.backups(expires_at)
  WHERE outcome = 'success';
