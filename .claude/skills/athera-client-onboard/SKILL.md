---
name: athera-client-onboard
description: Provision a new ATHERA client end to end — database, control-plane registry row, subscription plan, CDC publication and slot, masking salt, Caddy route and network alias. Use when asked to add, onboard, suspend, reactivate or remove a client or tenant.
---

# Onboarding a client

The most-repeated operation on this platform, and the one with the most places to forget a step. Every step below has a way of failing silently if skipped.

## Decide the shape first

| | Odoo client | Insight-only client |
|---|---|---|
| `insight_source_kind` | `odoo` | `external_postgres` |
| Odoo database | provisioned here | none — they bring their own app |
| CDC source | this cluster's Odoo DB | the client's Postgres |
| Column classification | synced from `pdp_field_classification` | supplied per client |
| dbt models | the existing Odoo marts | bespoke, see `athera-insight-source` |

For an Insight-only client, do steps 1–3 and 6 here, then use the `athera-insight-source` skill for the data path.

## The slug rule

`^[a-z][a-z0-9_]{1,30}$` — lowercase, starts with a letter, **no dashes**. Not a style choice: the slug becomes a Postgres replication slot name, and slot names forbid dashes. `scripts/lib/common.sh validate_slug` and the `CHECK` on `tenant_registry.tenants.slug` enforce exactly the same expression. Two different rules for one identifier is how a client gets provisioned that CDC can never follow.

Reserved: `postgres`, `template0`, `template1`, `odoo`, and the admin database name.

## Steps

### 1. Register the client in the control plane

```bash
docker exec odoo19-bct-postgres psql -U odoo -d athera_admin -c "
INSERT INTO tenant_registry.tenants (slug, display_name, db_name, state, plan_code, insight_source_kind)
VALUES ('acme', 'Acme Corp', 'acme', 'provisioning', 'insight', 'odoo');"
```

Plans live in `tenant_registry.plans`; `products` there is the entitlement (`insight`, `odoo`, `agent`) that reaches the JWT.

### 2. Create the database (Odoo clients only)

```bash
make tenant-provision TENANT=acme
```

Creates the database, installs `$ODOO_INIT_MODULES`, and applies `scripts/lib/database-baseline.sql` so `warehouse_reader` gets SELECT and nothing else.

### 3. Mark it active

```bash
docker exec odoo19-bct-postgres psql -U odoo -d athera_admin -c "
UPDATE tenant_registry.tenants SET state='active', activated_at=now() WHERE slug='acme';"
```

Until this runs, the client authenticates successfully and is sent straight to `/subscription` — `tenant_registry.is_active()` answers false for `provisioning`.

### 4. Network alias, so RPC can name the database

`ODOO_DBFILTER` is `^%d$`, which applies to JSON-RPC exactly as it applies to a browser. Add to the `odoo` service in `compose/odoo.yml`:

```yaml
          - acme.${ATHERA_DOMAIN:-athera.localhost}
```

Skip this and the login gateway answers `upstream_unavailable` on a correct password.

### 5. Caddy route

Add a block to `caddy/Caddyfile` modelled on the `bct` one — including the `@longpoll` handler, or every bus subscription occupies an HTTP worker for its full timeout. There is **no wildcard route on purpose**: a wildcard plus `^%d$` makes every database in the cluster reachable by guessing its name.

```bash
MSYS_NO_PATHCONV=1 docker exec odoo19-bct-caddy caddy reload --config /etc/caddy/Caddyfile
```

### 6. Masking salt

```
WAREHOUSE_MASK_SALT_ACME=<48 random chars>
```

Uppercased slug. The CDC loader **refuses to start** rather than hash unkeyed, so a missing salt is a loud startup failure — which is the correct behaviour, not a bug.

### 7. CDC

```bash
make cdc-start TENANT=acme
```

Publication first, then the consumer. One loader per tenant: a second client means a second `cdc` container, because a loader binds to one `CDC_TENANT_DB` with its own publication and slot.

### 8. Warehouse registry

`warehouse.tenant_registry` needs a row too — `mask_salt_env` stores the NAME of the env var, never its value.

## Verify — and look for the negative

```bash
make control-plane-status
make cdc-status
curl -s -X POST http://127.0.0.1:38120/auth/login -H 'Content-Type: application/json' \
  -d '{"db":"acme","login":"admin","password":"..."}'
```

The login response must carry `subscription_active: true` and the plan's `products`. Then **prove the other direction** — a check that has never been observed to fail is not yet known to work:

```bash
docker exec odoo19-bct-postgres psql -U odoo -d athera_admin -c \
  "UPDATE tenant_registry.tenants SET state='suspended' WHERE slug='acme';"
# wait out LOGIN_GATEWAY_REGISTRY_CACHE_TTL (default 30s), log in again:
#   subscription_active must be false, and the portal must send /t/acme/* to /subscription
```

Also assert that the new client's rows are invisible to another tenant's session, and that the other tenant *has* rows — a zero that comes from an empty warehouse proves nothing.

## Suspending and reactivating

State alone is enough; nothing needs restarting. `state='suspended'` or a `valid_until` in the past both make `is_active()` false. The claim is re-read on every login **and every refresh**, so a lapse mid-session stops the next refresh rather than waiting for the next login.

## Gaps to know about

- No deprovision script. `scripts/tenant-provision.sh` has a TODO where it belongs.
- `LOGIN_GATEWAY_ALLOWED_DATABASES` must also name the new slug, or the gateway refuses it with the same response it gives bad credentials.
