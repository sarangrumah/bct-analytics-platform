---
name: athera-up
description: Bring up, verify, or diagnose the ATHERA stack — all four product stacks or one of them. Use when asked to start the stack, check whether it is healthy, work out why a service is down, or confirm a change works in the running environment.
---

# Bringing ATHERA up

Four product stacks, one compose project (`odoo19-bct`). The split is by product so a product can move to its own VPS later; it is not a split by concern.

| Stack | File | Services |
|---|---|---|
| odoo | `compose/odoo.yml` + `compose/odoo.dev.yml` | postgres, redis, odoo |
| insight | `compose/insight.yml` | warehouse-db, warehouse-exporter, cdc, semantic-api, insight-portal, dbt (profile `tools`) |
| platform | `compose/platform.yml` | login-gateway, caddy |
| observability | `compose/observability.yml` | prometheus, grafana, loki, promtail, alertmanager, 2 exporters |

## Normal bring-up

```bash
make up          # every product stack, in the one order that works
make up-obs      # observability, separate on purpose
make cdc-start   # only when a publication is wanted; see below
```

`make up` runs `scripts/up-all.sh`, which probes each endpoint from the host afterwards rather than trusting container health. A container reporting healthy and a published port answering are different claims.

## Two rules that are not stylistic

**Order is load-bearing.** odoo → warehouse → login-gateway → semantic-api → insight-portal. Starting `semantic-api` before the gateway leaves it unable to fetch JWKS, so it answers **401 on every valid login** — which reads as the user's password being wrong and sends people to debug the wrong service.

**`cdc` is not started by `make up`.** It needs a publication that only `make cdc-start` creates, and a replication slot begins retaining WAL the instant it exists against a 2 GB cap (ADR 0001). A slot with no consumer ready is exactly the failure that cap bounds.

## Never do these

- `docker compose down` without `-p odoo19-bct`. This host also runs `odoo19-platform-*`, `odoo19-analytics-*` and `smart-warga-*`; their data is not recoverable from here. A volume has already been lost this way once.
- `make down-hard` casually. It deletes every volume, and the rebuilt stack installs only `$ODOO_INIT_MODULES`. Anything installed by hand since the last cold start is gone with it.
- Pass `-f` without `--env-file .env`. The compose files live in `compose/`, so compose looks for `.env` there and every `${VAR}` silently resolves empty. The Makefile does this for you; hand-written commands must not forget it.

## Reaching Odoo by hand

`ODOO_DBFILTER` is `^%d$` — Odoo picks the database from the **first label of the Host header**. A request to `127.0.0.1` resolves `%d` to `127`, matches nothing, and answers 303 to a database selector that `list_db=False` has disabled.

```bash
curl -H 'Host: bct.athera.localhost'          http://127.0.0.1:38069/web/login   # 200
curl -H 'Host: athera_admin.athera.localhost' http://127.0.0.1:38069/web/login   # 200
curl                                          http://127.0.0.1:38069/web/login   # 303, and correct
```

Through Caddy, use `--resolve` instead of editing the hosts file:

```bash
curl -sk --resolve app.athera.localhost:38443:127.0.0.1 https://app.athera.localhost:38443/
```

## Ports

| Port | Service |
|---|---|
| 33000 | insight-portal |
| 33001 / 39090 / 39093 / 33100 | grafana / prometheus / alertmanager / loki |
| 35432 / 35433 | postgres (OLTP) / warehouse-db |
| 36379 | redis |
| 38069 / 38072 | odoo http / longpolling |
| 38080 / 38443 | caddy http / https |
| 38120 / 38200 | login-gateway / semantic-api |

All bound to `127.0.0.1`. Never publish on `0.0.0.0` here.

## Verifying

```bash
make verify   # 20 checks, including that the OTHER stacks on this host are untouched
make ps       # every product stack in one view
make test     # the integration suite
```

`make verify` failing on `/web/login` is usually the Host header, not Odoo.

## Diagnosing

1. `make ps` — is the container there at all? A missing one is different from an unhealthy one.
2. `docker logs --tail 40 odoo19-bct-<service>` — `make logs SERVICE=x` also works.
3. For anything auth-shaped, check the chain end to end rather than guessing:

```bash
curl -s http://127.0.0.1:38120/.well-known/jwks.json | head -c 200   # gateway alive, 2 kids
curl -s http://127.0.0.1:38200/healthz                                # semantic-api sees the warehouse
```

A `upstream_unavailable` from the gateway on a correct password almost always means `LOGIN_GATEWAY_ODOO_URL` is not naming a database — it must be a hostname whose first label is the tenant (`http://bct.athera.localhost:8069`), and `compose/odoo.yml` must carry the matching network alias.
