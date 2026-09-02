-- ===========================================================================
-- 60-functions.sql — the PDP transform in SQL, the RLS applicator, and a
-- self-test that runs every time this file is applied.
--
-- Idempotent.
-- ===========================================================================

-- Created and executed as the owning role, not as the applying superuser.
SET ROLE :wh_user;

-- ---------------------------------------------------------------------------
-- warehouse.pdp_hmac(value, salt) — contract 01's `personal` transform,
-- reproduced from custom_pdp_masking/MODULE_KNOWLEDGE.md §2.
--
-- Every one of the eleven pinned degrees of freedom in that document is
-- reproduced here, and the ones that are easy to get wrong are called out:
--
--   3. The SALT IS THE KEY and the value is the message. pgcrypto's signature
--      is hmac(data, key, type), so the arguments are (value, salt) in that
--      order -- swapping them compiles, runs, and silently produces a
--      warehouse full of digests that will never join with Backend's.
--   4/5. Both sides UTF-8, via convert_to(..., 'UTF8').
--   6. NO normalisation. No trim, no lower, no unaccent. ' Budi Santoso '
--      must differ from 'Budi Santoso'.
--   7. Lowercase hex. encode(..., 'hex') is lowercase in Postgres; asserted
--      below rather than assumed.
--   8. NULL in -> NULL out. NULL is preserved, never hashed to a constant.
--   9. '' in -> NULL out. Hashing the empty string would give every empty
--      cell one shared non-NULL digest, i.e. a fabricated join key.
--  11. Empty or absent salt raises. Never degrade to an unkeyed hash.
--
-- Item 10 (non-text input is an error) is enforced by the type signature plus
-- the loader-side guard in bin/load_fixture.py, not here.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION warehouse.pdp_hmac(p_value text, p_salt text)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
PARALLEL SAFE
AS $fn$
BEGIN
  -- Item 11 first, and deliberately as an exception rather than a NULL: a
  -- missing salt must stop the load, not quietly produce NULLs that look like
  -- "this partner had no name".
  IF p_salt IS NULL OR p_salt = '' THEN
    RAISE EXCEPTION 'PDP salt is empty or NULL - refusing to degrade to an unkeyed hash'
      USING ERRCODE = 'invalid_parameter_value';
  END IF;
  -- Items 8 and 9.
  IF p_value IS NULL OR p_value = '' THEN
    RETURN NULL;
  END IF;
  -- Items 3, 4, 5, 6, 7.
  RETURN encode(hmac(convert_to(p_value, 'UTF8'), convert_to(p_salt, 'UTF8'), 'sha256'), 'hex');
END
$fn$;

-- NOTE ON plpgsql RATHER THAN LANGUAGE sql. The obvious one-liner is a SQL
-- function with a CASE that calls a raising helper in its first branch. It is
-- wrong: an IMMUTABLE zero-argument function inside a CASE is a candidate for
-- constant folding at plan time, so the "salt is empty" branch can be
-- evaluated even when the salt is fine, and every load fails. plpgsql has real
-- control flow and no such hazard.

COMMENT ON FUNCTION warehouse.pdp_hmac IS
  'contract 01 `personal` transform. HMAC(key=salt, msg=value), SHA-256, lowercase hex. '
  'Byte-identical to custom_pdp_masking.pdp_hmac_sha256 - asserted against that module''s '
  'published known-answer vectors every time 60-functions.sql is applied.';

-- ---------------------------------------------------------------------------
-- SELF-TEST. Runs on every apply.
--
-- These four vectors are published in custom_pdp_masking/MODULE_KNOWLEDGE.md
-- §2 and asserted by that module's own test suite. If they ever stop matching,
-- every digest already in the warehouse is invalid and the change is a
-- migration, not a bug fix -- so this fails the apply loudly instead of
-- letting an unjoinable warehouse be built on top of it.
--
-- The two NEGATIVE vectors matter as much as the positive ones: they are what
-- catches a reimplementation as sha256(salt || value) -- which produces
-- 64 lowercase hex characters and looks completely correct.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
  v_salt   CONSTANT text := 'bct-demo-salt';
  v_other  CONSTANT text := 'other-tenant-salt';
BEGIN
  IF warehouse.pdp_hmac('budi.santoso@contoh.invalid', v_salt)
     <> '57890775652c2e05536c54638d280c1f2cde752d0fc52bf42ac3a76d53ddbd5e' THEN
    RAISE EXCEPTION 'PDP known-answer vector 1 FAILED (email / bct-demo-salt)';
  END IF;

  IF warehouse.pdp_hmac('budi.santoso@contoh.invalid', v_other)
     <> 'c24c6fc738f518543fe3b5cfb2e8e0bafd0464371333e91188552317f9b4f738' THEN
    RAISE EXCEPTION 'PDP known-answer vector 2 FAILED (cross-tenant separation)';
  END IF;

  IF warehouse.pdp_hmac('Budi Santoso', v_salt)
     <> 'a5c30f115ac845dd0cfafabe0326c71de7f1e7d3a869d252c4caa894ab4b978b' THEN
    RAISE EXCEPTION 'PDP known-answer vector 3 FAILED (name)';
  END IF;

  IF warehouse.pdp_hmac('Ir. Sri Wahyuni, S.T.', v_salt)
     <> '9a5f1b855c3e59c66e701fb93f6411627790d573b9d48cda9c7b74cf1a1e6b3b' THEN
    RAISE EXCEPTION 'PDP known-answer vector 4 FAILED (name with punctuation)';
  END IF;

  -- Negative: a concatenation-based reimplementation must NOT match.
  IF encode(digest(v_salt || 'Budi Santoso', 'sha256'), 'hex')
     = warehouse.pdp_hmac('Budi Santoso', v_salt) THEN
    RAISE EXCEPTION 'PDP negative vector FAILED: sha256(salt||value) matched the HMAC';
  END IF;

  -- Negative: no trimming, no case folding.
  IF warehouse.pdp_hmac(' Budi Santoso ', v_salt) = warehouse.pdp_hmac('Budi Santoso', v_salt) THEN
    RAISE EXCEPTION 'PDP negative vector FAILED: input was trimmed';
  END IF;
  IF warehouse.pdp_hmac('budi santoso', v_salt) = warehouse.pdp_hmac('Budi Santoso', v_salt) THEN
    RAISE EXCEPTION 'PDP negative vector FAILED: input was case folded';
  END IF;

  -- NULL and empty both yield NULL, and neither yields a shared digest.
  IF warehouse.pdp_hmac(NULL, v_salt) IS NOT NULL THEN
    RAISE EXCEPTION 'PDP NULL handling FAILED';
  END IF;
  IF warehouse.pdp_hmac('', v_salt) IS NOT NULL THEN
    RAISE EXCEPTION 'PDP empty-string handling FAILED';
  END IF;

  RAISE NOTICE 'PDP HMAC self-test: 4 known-answer vectors + 3 negative vectors PASSED';
END
$$;

-- ---------------------------------------------------------------------------
-- warehouse.apply_tenant_rls(schema, table) — the storage-layer tenant
-- boundary, applied as a dbt post-hook so it survives every rebuild.
--
-- THE PROBLEM THIS SOLVES. dbt drops and recreates a table on a full refresh.
-- Policies live on the table, so they go with it. A one-off `CREATE POLICY`
-- run by hand is therefore a control that silently disappears the first time
-- somebody runs `dbt build --full-refresh`, which is precisely the moment
-- nobody re-checks it. Calling this from a post-hook on every model makes the
-- policy a property of the model definition instead.
--
-- THE TWO POLICIES, and why there are two.
--
--   p_tenant_isolation   TO PUBLIC
--     USING (tenant_id = current_setting('app.tenant_id', true))
--     The real boundary. With the variable unset, current_setting(...,true)
--     returns NULL, `tenant_id = NULL` is NULL, and NULL is not true -- so the
--     row is not visible. FAIL CLOSED: forgetting to scope a connection
--     yields nothing, never everything.
--
--   p_transform_unscoped TO <owner>
--     USING (coalesce(current_setting('app.tenant_id', true), '') = '')
--     dbt has to read every tenant to build a model, and it cannot set a
--     tenant because it is building all of them. Without this, RLS would make
--     dbt see zero rows and every downstream model and every test would pass
--     vacuously on an empty table -- the worst possible failure, because it is
--     green. Note the condition: the transform role is unscoped ONLY while no
--     tenant is set. The instant a caller sets app.tenant_id, even as the
--     owner, this policy stops applying and only p_tenant_isolation remains.
--     That is what makes `SET app.tenant_id='x'` a genuine constraint for
--     every non-superuser identity, including the one dbt uses.
--
-- FORCE ROW LEVEL SECURITY is required because `warehouse` OWNS these tables,
-- and a table owner is exempt from its own policies unless forced.
--
-- A superuser still bypasses all of it. That is Postgres, not a choice made
-- here, and it is why the container's POSTGRES_USER is warehouse_admin and
-- why nothing queries data as that role.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION warehouse.apply_tenant_rls(p_schema text, p_table text)
RETURNS void
LANGUAGE plpgsql
AS $$
DECLARE
  v_owner text;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = p_schema AND table_name = p_table AND column_name = 'tenant_id'
  ) THEN
    RAISE EXCEPTION
      'apply_tenant_rls: %.% has no tenant_id column. Every fact and dimension must carry one '
      '(master prompt §3.3, contract 05).', p_schema, p_table;
  END IF;

  SELECT tableowner INTO v_owner FROM pg_tables WHERE schemaname = p_schema AND tablename = p_table;
  IF v_owner IS NULL THEN
    -- A view, not a table. Views inherit the RLS of what they select from.
    RETURN;
  END IF;

  EXECUTE format('ALTER TABLE %I.%I ENABLE ROW LEVEL SECURITY', p_schema, p_table);
  EXECUTE format('ALTER TABLE %I.%I FORCE ROW LEVEL SECURITY',  p_schema, p_table);

  EXECUTE format('DROP POLICY IF EXISTS p_tenant_isolation ON %I.%I',   p_schema, p_table);
  EXECUTE format('DROP POLICY IF EXISTS p_transform_unscoped ON %I.%I', p_schema, p_table);

  EXECUTE format(
    'CREATE POLICY p_tenant_isolation ON %I.%I FOR ALL TO PUBLIC '
    'USING (tenant_id = current_setting(''app.tenant_id'', true))',
    p_schema, p_table);

  EXECUTE format(
    'CREATE POLICY p_transform_unscoped ON %I.%I FOR ALL TO %I '
    'USING (coalesce(current_setting(''app.tenant_id'', true), '''') = '''')',
    p_schema, p_table, v_owner);
END
$$;

COMMENT ON FUNCTION warehouse.apply_tenant_rls IS
  'Applies the two tenant RLS policies to a mart. Called from a dbt post-hook on every model so '
  'the policy survives --full-refresh, which drops and recreates the table.';

RESET ROLE;
