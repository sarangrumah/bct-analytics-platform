-- ---------------------------------------------------------------------------
-- Cluster-wide privilege hardening.
--
-- PUBLIC is an implicit member of every role. Anything granted to PUBLIC is
-- granted to warehouse_reader too, which would quietly undo "read-only by
-- construction". So PUBLIC gets nothing it does not need.
-- ---------------------------------------------------------------------------

-- PostgreSQL 15+ already removes CREATE on `public` from PUBLIC. Repeated here
-- so the property is explicit and survives a restore into an older cluster.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

-- Deny CONNECT and TEMP on the maintenance database to everyone by default,
-- then hand CONNECT back to the identities that need it. No TEMP for the
-- reader: a temp table is a write, and CREATE TEMP would be a hole in the
-- "cannot write" property.
REVOKE ALL ON DATABASE postgres FROM PUBLIC;

SELECT format('GRANT CONNECT ON DATABASE postgres TO %I', :'reader_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE postgres TO %I', :'exporter_user')
\gexec

-- template1 is the template for anything created without an explicit template.
-- Odoo uses template0 (odoo.conf: db_template), but a hand-run `createdb` uses
-- template1, so lock it down as well.
REVOKE ALL ON DATABASE template1 FROM PUBLIC;

\echo '-- hardening: applied'
