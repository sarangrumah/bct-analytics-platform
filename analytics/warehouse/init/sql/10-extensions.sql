-- ===========================================================================
-- 10-extensions.sql — extensions the warehouse depends on.
--
-- Idempotent. Applied at first boot by init/00-bootstrap.sh and on every
-- `make up-analytics` by bin/warehouse-apply.sh, from the same file, so the
-- two paths cannot drift.
-- ===========================================================================

-- pgcrypto: hmac(data bytea, key bytea, type text).
--
-- This is what makes the PDP transform executable INSIDE the warehouse's own
-- load path, rather than being a Python-only concept the SQL layer has to
-- take on trust. custom_pdp_masking pins the construction as
-- HMAC(key=salt, msg=value), SHA-256, lowercase hexdigest, no normalisation.
-- pgcrypto's hmac() is RFC 2104 with the key as the second argument, so
--
--     encode(hmac(value::bytea, salt::bytea, 'sha256'), 'hex')
--
-- is byte-identical to the reference Python. The known-answer vectors from
-- custom_pdp_masking/MODULE_KNOWLEDGE.md §2 are asserted against it in
-- 60-policy-functions.sql; if pgcrypto ever changed, that assertion fails at
-- apply time rather than producing a warehouse full of unjoinable digests.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- postgres_fdw: the reconciliation path.
--
-- Reconciliation has to compare the mart against ODOO, not against a control
-- total the warehouse computed for itself — otherwise the test is the model
-- marking its own homework. FDW makes the source totals a live read of the
-- Odoo database inside the same query.
--
-- Two containment rules, both enforced rather than documented:
--   1. The mapping connects as warehouse_reader, which holds SELECT +
--      REPLICATION and nothing else. There is no write path to Odoo because
--      the role cannot write (contract 04 §2), not because we chose not to.
--   2. The foreign tables are created with EXPLICIT column lists generated
--      from warehouse.column_policy, and every `secret`-class column is
--      absent from them. A `secret` column is therefore not merely unselected
--      — it does not exist as a name the warehouse can type.
CREATE EXTENSION IF NOT EXISTS postgres_fdw;

-- pg_stat_statements is preloaded in postgresql.conf; the extension itself
-- still has to be created for its view to exist.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
