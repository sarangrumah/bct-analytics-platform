---
name: athera-vps-split
description: Check whether an ATHERA product can be moved to its own VPS, and carry out the move. Use when asked to separate a product onto its own server, plan a deployment topology, or assess what a VPS split would break.
---

# Splitting a product onto its own VPS

The compose files are already split by product so this is possible. That layout is necessary and not sufficient — what actually decides whether a service can move is how it finds its neighbours and what state it shares.

## The readiness test

A service can move when **all four** hold:

1. **Every neighbour is reached through an `.env` URL**, not a hard-coded hostname. Check the compose service and the image:
   ```bash
   grep -rn "http://" compose/<stack>.yml
   grep -rn "localhost\|127.0.0.1\|http://odoo\|http://postgres" <service>/app/
   ```
   A default of `http://other:8080` is fine — it is a default. A literal in the code is not.
2. **No shared volume or socket** with a service staying behind.
3. **No shared Postgres cluster** with a service staying behind, unless both move together.
4. **Its secrets are its own** and reach it through env, not through a bind mount from a sibling.

## What is ready today

| Stack | Movable | Notes |
|---|---|---|
| observability | yes | needs its own promtail per host — see below |
| agent | yes | stateless; `ai-gateway` holds an HMAC secret and an LLM key |
| insight | yes, **with a caveat** | the CDC→Odoo link becomes cross-host |
| platform | **partly** | `caddy` and `login-gateway` move; the control-plane database cannot leave the admin Odoo |
| odoo | yes | it is the anchor everything else points at |

## The three traps

### 1. CDC → Odoo's Postgres is a replication connection, not HTTP

If Insight leaves the host Odoo is on, this becomes a cross-host **logical replication** link. `warehouse_reader` already holds `REPLICATION`, so no grant changes — what changes is the network it crosses.

- Require TLS (`sslmode=verify-full`) and pin the server certificate.
- Firewall 5432 to the Insight host only. A publicly reachable Postgres with a replication role is a full copy of the client's data.
- Watch `max_slot_wal_keep_size`. A slot whose consumer is now a network partition away retains WAL on the **Odoo** host; the 2 GB cap (ADR 0001) is what stops a warehouse outage becoming an Odoo outage, and it matters far more once a network sits between them.

### 2. `custom_super_admin` cannot be split from its database

Its cron runs `cr.execute("SELECT ... FROM tenant_registry.action_log_v")` on its own cursor. Postgres has no cross-database SELECT, which is why `tenant_registry` lives **inside** the admin Odoo database rather than in a master database of its own. So:

> The control-plane schema, the admin Odoo database and `custom_super_admin` move as one unit or not at all.

Splitting them needs the module changed to go through the orchestrator's API instead of a direct cursor. That is a code change, not a deployment change.

### 3. Promtail mounts the docker socket

It reads the local daemon, so it is inherently per-host. Each VPS runs its own, shipping to one central Loki. Prometheus can scrape across hosts, but the targets must then be reachable and authenticated — the current `scrape.d` assumes one bridge network.

## Doing the move

1. **Env first, on the single host.** Change every inter-service URL from a service name to the real address and confirm the stack still works. This is the only step that can be tested before any hardware exists — do not skip it.
2. Stand up the new host and copy `.env` with the split-specific values.
3. Move the stack's compose file and start it.
4. Point the old host's URLs at the new address.
5. **TLS and firewall before the first byte of real data crosses.** Every one of these links carried only loopback traffic until now.
6. Add per-host promtail; point it at the central Loki.
7. Update Caddy upstreams — `caddy/Caddyfile` names services by compose name today.

## Verify after

```bash
make verify            # on the host that still has Odoo
make test              # the isolation and 403 tests matter most here
make cdc-status        # slot active, and retained WAL not growing
```

Then re-prove tenant isolation across the new boundary. Isolation was previously enforced inside one Postgres and one docker network; after a split the same guarantees rest on TLS and firewall rules that did not exist before, and the tests are what tell you whether they hold.

## Do not

- Move a service by changing its compose file only. If URLs still resolve by docker DNS, it will appear to work on one host and fail on two.
- Split the control-plane database from the admin Odoo. See trap 2.
- Publish anything on `0.0.0.0` "temporarily" to get the split working. Reach the far side over a private network or a tunnel.
