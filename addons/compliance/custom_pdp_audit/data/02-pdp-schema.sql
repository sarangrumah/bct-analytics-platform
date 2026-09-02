-- ============================================================
-- PDP compliance schema (UU 27/2022)
-- - Append-only audit log with SHA-256 hash chain
-- - Trigger blocks UPDATE / DELETE / TRUNCATE
-- - Read-only view exposed to odoo app role
-- - Trigger function to compute hash from row + prev_hash
-- ============================================================

CREATE SCHEMA IF NOT EXISTS pdp;
COMMENT ON SCHEMA pdp IS 'Personal Data Protection (UU 27/2022) — audit, consent, retention';

-- ----- Idempotent migration: converge pre-existing tables to current schema -----
-- Older DBs bootstrapped audit_log with action VARCHAR(16) + a closed-enum CHECK.
-- CREATE TABLE IF NOT EXISTS below never migrates an existing table, so widen the
-- column and swap the stale enum CHECK for the current regex CHECK in place. This
-- block is a no-op on a fresh DB (table absent) and on an already-current DB.
DO $pdp_mig$
BEGIN
  IF to_regclass('pdp.audit_log') IS NOT NULL THEN
    -- Views reference action and would block the type change; drop and let the
    -- rest of this script (pdp.audit_log_v) and Odoo (public.pdp_audit_log,
    -- an _auto=False model view rebuilt on registry load) recreate them.
    DROP VIEW IF EXISTS public.pdp_audit_log;
    DROP VIEW IF EXISTS pdp.audit_log_v;
    IF EXISTS (
      SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'pdp' AND table_name = 'audit_log'
        AND column_name = 'action' AND character_maximum_length < 64
    ) THEN
      ALTER TABLE pdp.audit_log ALTER COLUMN action TYPE varchar(64);
    END IF;
    IF EXISTS (
      SELECT 1 FROM pg_constraint
      WHERE conrelid = 'pdp.audit_log'::regclass
        AND conname = 'audit_log_action_check'
        AND pg_get_constraintdef(oid) NOT LIKE '%~%'
    ) THEN
      ALTER TABLE pdp.audit_log DROP CONSTRAINT audit_log_action_check;
      ALTER TABLE pdp.audit_log
        ADD CONSTRAINT audit_log_action_check CHECK (action ~ '^[a-z][a-z0-9_]{1,63}$');
    END IF;
  END IF;
END
$pdp_mig$;

-- ----- Audit log table (append-only, hash-chained) -----
CREATE TABLE IF NOT EXISTS pdp.audit_log (
  id              BIGSERIAL    PRIMARY KEY,
  ts              TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp(),
  actor_user_id   INTEGER,                    -- res.users.id from Odoo (nullable for system)
  actor_login     VARCHAR(128),
  tenant_db       VARCHAR(64),
  model_name      VARCHAR(128) NOT NULL,
  res_id          BIGINT,
  -- Action codes are free-form domain identifiers (e.g. 'create', 'approval_submit',
  -- 'coretax_pajakku_submitted'). Constrained to lowercase snake_case to keep the
  -- audit grammar uniform without locking in a closed enum.
  action          VARCHAR(64)  NOT NULL CHECK (action ~ '^[a-z][a-z0-9_]{1,63}$'),
  field_changes   JSONB,
  classification  VARCHAR(32),                -- pii / sensitive_pii / financial / health / ...
  ip_address      INET,
  user_agent      TEXT,
  request_id      VARCHAR(64),
  reason          TEXT,                       -- e.g., unmask justification
  prev_hash       BYTEA,                      -- 32 bytes (sha256 of previous row)
  hash            BYTEA        NOT NULL       -- 32 bytes
);

CREATE INDEX IF NOT EXISTS audit_log_ts_idx       ON pdp.audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS audit_log_user_idx     ON pdp.audit_log (actor_user_id);
CREATE INDEX IF NOT EXISTS audit_log_model_idx    ON pdp.audit_log (model_name, res_id);
CREATE INDEX IF NOT EXISTS audit_log_action_idx   ON pdp.audit_log (action);
CREATE INDEX IF NOT EXISTS audit_log_class_idx    ON pdp.audit_log (classification);
CREATE INDEX IF NOT EXISTS audit_log_changes_idx  ON pdp.audit_log USING GIN (field_changes);

COMMENT ON TABLE pdp.audit_log IS 'Append-only audit log with chained sha256 hashes (tamper-evident)';

-- ----- Hash computation function -----
-- The hash is sha256 of: prev_hash || canonical_row_bytes
CREATE OR REPLACE FUNCTION pdp._compute_audit_hash(
  p_prev_hash BYTEA,
  p_ts TIMESTAMPTZ,
  p_actor_user_id INTEGER,
  p_actor_login TEXT,
  p_tenant_db TEXT,
  p_model_name TEXT,
  p_res_id BIGINT,
  p_action TEXT,
  p_field_changes JSONB,
  p_classification TEXT,
  p_ip INET,
  p_user_agent TEXT,
  p_request_id TEXT,
  p_reason TEXT
) RETURNS BYTEA
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  payload BYTEA;
BEGIN
  payload := COALESCE(p_prev_hash, '\x'::bytea)
          || convert_to(
              COALESCE(to_char(p_ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'), '')
              || '|' || COALESCE(p_actor_user_id::text, '')
              || '|' || COALESCE(p_actor_login, '')
              || '|' || COALESCE(p_tenant_db, '')
              || '|' || COALESCE(p_model_name, '')
              || '|' || COALESCE(p_res_id::text, '')
              || '|' || COALESCE(p_action, '')
              || '|' || COALESCE(p_field_changes::text, '')
              || '|' || COALESCE(p_classification, '')
              || '|' || COALESCE(p_ip::text, '')
              || '|' || COALESCE(p_user_agent, '')
              || '|' || COALESCE(p_request_id, '')
              || '|' || COALESCE(p_reason, ''),
            'UTF8');
  RETURN digest(payload, 'sha256');
END$$;

-- ----- BEFORE INSERT trigger: chain hash + populate prev_hash -----
CREATE OR REPLACE FUNCTION pdp._audit_log_before_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  v_prev BYTEA;
BEGIN
  SELECT hash INTO v_prev FROM pdp.audit_log ORDER BY id DESC LIMIT 1;
  NEW.prev_hash := v_prev;
  NEW.hash := pdp._compute_audit_hash(
    NEW.prev_hash, NEW.ts, NEW.actor_user_id, NEW.actor_login,
    NEW.tenant_db, NEW.model_name, NEW.res_id, NEW.action,
    NEW.field_changes, NEW.classification, NEW.ip_address,
    NEW.user_agent, NEW.request_id, NEW.reason
  );
  RETURN NEW;
END$$;

DROP TRIGGER IF EXISTS audit_log_before_insert ON pdp.audit_log;
CREATE TRIGGER audit_log_before_insert
  BEFORE INSERT ON pdp.audit_log
  FOR EACH ROW EXECUTE FUNCTION pdp._audit_log_before_insert();

-- ----- BEFORE UPDATE / DELETE trigger: REJECT (append-only) -----
CREATE OR REPLACE FUNCTION pdp._audit_log_block_modify()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'pdp.audit_log is append-only (PDP compliance) — % rejected', TG_OP
    USING ERRCODE = 'insufficient_privilege';
END$$;

DROP TRIGGER IF EXISTS audit_log_block_update ON pdp.audit_log;
CREATE TRIGGER audit_log_block_update
  BEFORE UPDATE ON pdp.audit_log
  FOR EACH ROW EXECUTE FUNCTION pdp._audit_log_block_modify();

DROP TRIGGER IF EXISTS audit_log_block_delete ON pdp.audit_log;
CREATE TRIGGER audit_log_block_delete
  BEFORE DELETE ON pdp.audit_log
  FOR EACH ROW EXECUTE FUNCTION pdp._audit_log_block_modify();

DROP TRIGGER IF EXISTS audit_log_block_truncate ON pdp.audit_log;
CREATE TRIGGER audit_log_block_truncate
  BEFORE TRUNCATE ON pdp.audit_log
  FOR EACH STATEMENT EXECUTE FUNCTION pdp._audit_log_block_modify();

-- ----- Read-only view exposed to Odoo -----
CREATE OR REPLACE VIEW pdp.audit_log_v AS
SELECT
  id, ts, actor_user_id, actor_login, tenant_db,
  model_name, res_id, action, field_changes, classification,
  host(ip_address) AS ip_address,
  user_agent, request_id, reason,
  encode(prev_hash, 'hex') AS prev_hash_hex,
  encode(hash, 'hex')      AS hash_hex
FROM pdp.audit_log;

COMMENT ON VIEW pdp.audit_log_v IS 'Read-only view of audit_log with hex-encoded hashes for Odoo consumption';

-- ----- Chain verification function -----
CREATE OR REPLACE FUNCTION pdp.verify_audit_chain(p_limit INTEGER DEFAULT NULL)
RETURNS TABLE(broken_id BIGINT, expected_hash TEXT, actual_hash TEXT)
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r RECORD;
  v_expected BYTEA;
  v_prev BYTEA := NULL;
  v_count INTEGER := 0;
BEGIN
  FOR r IN
    SELECT * FROM pdp.audit_log ORDER BY id ASC
    LIMIT COALESCE(p_limit, 2147483647)
  LOOP
    v_expected := pdp._compute_audit_hash(
      v_prev, r.ts, r.actor_user_id, r.actor_login, r.tenant_db,
      r.model_name, r.res_id, r.action, r.field_changes, r.classification,
      r.ip_address, r.user_agent, r.request_id, r.reason
    );
    IF v_expected <> r.hash THEN
      broken_id := r.id;
      expected_hash := encode(v_expected, 'hex');
      actual_hash := encode(r.hash, 'hex');
      RETURN NEXT;
    END IF;
    v_prev := r.hash;
    v_count := v_count + 1;
  END LOOP;
  RETURN;
END$$;

COMMENT ON FUNCTION pdp.verify_audit_chain(INTEGER) IS 'Walk audit_log and report rows whose stored hash != recomputed hash';
