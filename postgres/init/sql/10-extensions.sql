-- ---------------------------------------------------------------------------
-- Extensions on the maintenance database only.
--
-- Extensions are per-database, so this covers `postgres` (where
-- postgres_exporter connects). Tenant databases get their own set from
-- scripts/lib/database-baseline.sql at provisioning time, because they do not
-- exist yet at first boot.
-- ---------------------------------------------------------------------------

-- Required by postgresql.conf's shared_preload_libraries and by slow-query
-- triage. Preloading the library is not enough: the view needs the extension.
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
