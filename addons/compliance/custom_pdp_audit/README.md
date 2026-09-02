# Custom PDP Audit

Append-only, tamper-evident audit log for PDP-classified events. Storage
is the Postgres-side `pdp.audit_log` table created by
`postgres/init/02-pdp-schema.sql` — that file installs:

- The table itself with a `BEFORE UPDATE / DELETE / TRUNCATE` trigger
  that raises and rejects any modification (append-only at the DB level).
- A SHA-256 hash chain: each row's `hash` includes the previous row's
  `hash` (`prev_hash`) — any tampering breaks the chain and is detected
  by `pdp.verify_audit_chain()`.
- A read-only view `pdp.audit_log_v` for Odoo to consume with hex-encoded
  hashes.

The Odoo side of this module:

## Models

- `pdp.audit.log` — Odoo `SqlView`-style model reading from
  `pdp.audit_log_v`. List view with filters by user / model / date range
  / action / classification.
- `pdp.audited.mixin` — mixin to override `create` / `write` / `unlink`
  and insert into `pdp.audit_log` (Odoo runtime inherits role
  `odoo_pdp_writer` which has `INSERT` privilege only — `custom_pdp_writer`
  cannot read or delete the table).

## `pre_init_hook`

Verifies the `pdp` schema + view + roles already exist (they should from
the postgres init scripts). Aborts install if they don't.

## Security Groups

- `pdp.group_dpo` — view the audit log (read-only via view).

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`

## Install

Postgres-side schema is installed automatically on first
`docker compose up postgres` from `postgres/init/02-pdp-schema.sql`. Then
install this module from Apps.

## Verification

```bash
make verify-audit-chain
# or: docker compose exec postgres psql -U odoo -d <db> -c "SELECT * FROM pdp.verify_audit_chain();"
```
Returns zero rows when the chain is intact.

## Reference

- `postgres/init/02-pdp-schema.sql`
- `docs/pdp-compliance.md`
