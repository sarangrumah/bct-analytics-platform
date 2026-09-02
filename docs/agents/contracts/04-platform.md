# Contract 4 — Platform (Platform-Infra → everyone)

Status: **PUBLISHED at end of Phase 1.** Producer: Platform-Infra.
Consumers: Platform-Addons, Data Warehouse, Backend, Frontend, Security, QA.

Everything below is verified against a running stack, not designed on paper. Where this document
shows command output, that output was produced by running the command.

---

## 1. Compose topology

| Thing | Value |
|---|---|
| Compose project name | `odoo19-bct` |
| Container naming | `odoo19-bct-<service>` |
| Docker network | **`odoo19-bct_bct`** (bridge) |
| Base file | `docker-compose.yml` |
| Dev overlay | `docker-compose.dev.yml` |
| Observability overlay | `docker-compose.observability.yml` |

`.env` sets `COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml` and
`COMPOSE_PATH_SEPARATOR=:`, so a bare `docker compose ps` in the repo root resolves to the dev stack
scoped to this project. Scripts still pass `-p` and `-f` explicitly and never rely on that.

### Joining from a sibling overlay

The Data Warehouse and Backend agents add their own compose files. Two options, in order of
preference:

**A — same project, extra overlay** (preferred; this is what `docker-compose.observability.yml`
does). Add `-f docker-compose.analytics.yml` to the same `-p odoo19-bct` invocation and use
`networks: [bct]`. Nothing else is needed; service names resolve directly.

**B — separate compose project.** Declare the network as external:

```yaml
networks:
  bct:
    external: true
    name: odoo19-bct_bct
```

> If you use option B, pass `COMPOSE_IGNORE_ORPHANS=true` or expect a spurious "orphan containers"
> warning on every `up`. It is only a warning — compose never removes orphans without
> `--remove-orphans` — but it trains people to ignore compose output.

### Volumes

| Volume | Contents | Backed up by |
|---|---|---|
| `odoo19-bct_pgdata` | Postgres cluster | `scripts/tenant-backup.sh` (logical dump per tenant) |
| `odoo19-bct_odoodata` | Odoo filestore + sessions | `scripts/tenant-backup.sh` |
| `odoo19-bct_redisdata` | Redis AOF | not backed up — cache only, rebuildable |
| `odoo19-bct_promdata` | Prometheus TSDB | not backed up |
| `odoo19-bct_grafanadata` | Grafana state | not backed up — dashboards are code |
| `odoo19-bct_lokidata` | Loki chunks + index | not backed up |
| `odoo19-bct_alertmanagerdata` | Alertmanager silences | not backed up |
| `odoo19-bct_promtaildata` | Promtail positions | not backed up |

### Pinned image digests — do not float these

| Service | Image |
|---|---|
| odoo | `odoo:19.0@sha256:f99ffac95cb39a0924622ea4118481c95651d9c84187e5b30a21c2cc4419c7dd` |
| postgres | `postgres:16-alpine@sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| redis | `redis:7-alpine@sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf` |
| prometheus | `prom/prometheus:v2.55.1@sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3` |
| grafana | `grafana/grafana:11.4.0@sha256:d8ea37798ccc41061a62ab080f2676dda6bf7815558499f901bdb0f533a456fb` |
| loki | `grafana/loki:3.3.2@sha256:8af2de1abbdd7aa92b27c9bcc96f0f4140c9096b507c77921ffddf1c6ad6c48f` |
| promtail | `grafana/promtail:3.3.2@sha256:cb4990801ec58975c5e231057c2bcf204c85fac428eec65ad66e0016c64b9608` |
| alertmanager | `prom/alertmanager:v0.28.0@sha256:d5155cfac40a6d9250ffc97c19db2c5e190c7bc57c6b67125c94903358f8c7d8` |
| postgres_exporter | `prometheuscommunity/postgres-exporter:v0.16.0@sha256:6999a7657e2f2fb0ca6ebf417213eebf6dc7d21b30708c622f6fcb11183a2bb0` |
| node_exporter | `prom/node-exporter:v1.8.2@sha256:4032c6d5bfd752342c3e631c2f1de93ba6b86c41db6b167b9a35372c139e7706` |

Digests are written inline in the compose files, not read from `.env`. A digest is a security
control, not a tunable.

---

## 2. Postgres — connection details

Reachable from inside the compose network at **`postgres:5432`**, and from the host at
`127.0.0.1:35432`.

| Role | Attributes | For |
|---|---|---|
| `odoo` | superuser (created by initdb from `POSTGRES_USER`) | Odoo itself; schema migrations; provisioning |
| `warehouse_reader` | `LOGIN` + `REPLICATION` only | **CDC and analytics.** Cannot write. |
| `metrics_exporter` | `LOGIN` + `pg_monitor` | postgres_exporter, Grafana operational datasource |

### Connection URIs

From inside the network (this is what CDC, dbt and the semantic API use):

```
postgresql://warehouse_reader:${WAREHOUSE_READER_PASSWORD}@postgres:5432/<tenant_db>?sslmode=disable
postgresql://metrics_exporter:${POSTGRES_EXPORTER_PASSWORD}@postgres:5432/postgres?sslmode=disable
postgresql://odoo:${POSTGRES_PASSWORD}@postgres:5432/<tenant_db>
```

From the host:

```
postgresql://warehouse_reader:${WAREHOUSE_READER_PASSWORD}@127.0.0.1:35432/<tenant_db>
```

For a **logical replication** connection, append `replication=database`:

```
postgresql://warehouse_reader:${WAREHOUSE_READER_PASSWORD}@postgres:5432/<tenant_db>?replication=database
```

> `pg_hba.conf` is the image default (`host all all all scram-sha-256`). That is sufficient for
> logical replication and no change is needed: only *physical* replication connections match the
> `replication` keyword in the database column; a logical connection names a real database and is
> matched by `all`. Do not "fix" this by adding a `host replication` line.

### `warehouse_reader` is read-only by construction

Not by policy. `scripts/warehouse-reader-check.sh` proves it and is wired into `make verify`.
Verbatim, against database `bct`:

```
  1. SELECT on an Odoo table
  PASS (expected success) -> 5
  2. SELECT on another Odoo table
  PASS (expected success) -> 14
  3. CREATE TABLE in public
  PASS (correctly denied) -> ERROR: permission denied for schema public
  4. INSERT into an Odoo table
  PASS (correctly denied) -> ERROR: permission denied for table res_partner
  5. UPDATE an Odoo table
  PASS (correctly denied) -> ERROR: permission denied for table res_partner
  6. DELETE from an Odoo table
  PASS (correctly denied) -> ERROR: permission denied for table res_partner
  7. TRUNCATE an Odoo table
  PASS (correctly denied) -> ERROR: permission denied for table res_partner
  8. CREATE TEMP TABLE
  PASS (correctly denied) -> ERROR: permission denied to create temporary tables in database "bct"
  9. logical replication slot create + drop
  PASS created slot: bct_reader_check_1640
  PASS slot dropped, no WAL retained

     rolname      | rolsuper | rolcreatedb | rolcreaterole | rolreplication | rolbypassrls
------------------+----------+-------------+---------------+----------------+--------------
 warehouse_reader | f        | f           | f             | t              | f
```

`CREATE TEMP TABLE` is denied too. A temp table is still a write, and without
`REVOKE TEMPORARY ON DATABASE` the role would have one.

**How the grants are applied.** `scripts/lib/database-baseline.sql`, run by `init-db.sh` and
`tenant-provision.sh` after Odoo creates a database. It includes
`ALTER DEFAULT PRIVILEGES FOR ROLE odoo IN SCHEMA public GRANT SELECT ON TABLES TO warehouse_reader`,
so tables created by a later module install are covered automatically. If you install modules by a
route that bypasses those scripts, re-run `make init-db` to converge the grants.

---

## 3. Logical decoding — live, from first boot

Set in `postgres/postgresql.conf` before the cluster ever started, so Phase 3 CDC requires **no
restart of a running Odoo** (`docs/adr/0001-analytics-warehouse.md`).

```
$ docker compose exec -T postgres psql -U odoo -tAc \
    "show wal_level; show max_replication_slots; show max_wal_senders; show max_slot_wal_keep_size;"
logical
10
10
2GB
```

`max_slot_wal_keep_size` is **`2GB`**, not `-1`. This is the safety valve: a slot that falls more
than 2 GB behind is invalidated by Postgres and its WAL released. The warehouse then needs a resync.
That is the deliberate trade — sacrifice the warehouse, protect the ERP.

Because it is load-bearing it is also monitored, verified end to end by creating a real slot and
watching the metric arrive in Prometheus:

| Alert | Threshold | Severity |
|---|---|---|
| `ReplicationSlotWalRetentionWarning` | > 512 MiB for 10m | warning |
| `ReplicationSlotWalRetentionCritical` | > 1 GiB for 5m | critical |
| `ReplicationSlotInvalidated` | `wal_status="lost"` | critical — the cap fired; resync needed |
| `ReplicationSlotInactive` | no consumer for 15m | warning |

Metric names confirmed present on `postgres_exporter:9187`:
`pg_replication_slots_pg_wal_lsn_diff`, `pg_replication_slots_active`,
`pg_replication_slot_wal_status`, `pg_settings_max_slot_wal_keep_size_bytes`.

**Naming convention for Phase 3** (used by the marked hook in `scripts/tenant-provision.sh`):

| Object | Name |
|---|---|
| Publication | `bct_cdc_<slug>` |
| Replication slot | `bct_slot_<slug>` (`pgoutput`) |

Tenant slugs must match `^[a-z][a-z0-9_]{1,30}$` — **no dashes**, because Postgres replication slot
names forbid them. `scripts/lib/common.sh:validate_slug` enforces this everywhere.

> Create the publication **before** the slot. WAL retention starts the instant a slot exists, and
> the 2 GB cap starts counting immediately. A slot created before its consumer is ready is exactly
> the failure the cap exists to bound.
>
> On teardown, `pg_drop_replication_slot()` **before** `DROP DATABASE`, or the drop blocks.

---

## 4. Reserved host ports — do not bind these

All bound to `127.0.0.1`, never `0.0.0.0`. This host also runs `odoo19-platform-*` (18xxx/19xxx),
`odoo19-analytics-*` (2xxxx) and `smart-warga-postgres-1` (5433), and **ports 8069 and 5432 are
already taken**.

| Port | Service | Status |
|---|---|---|
| `38069` | odoo http | **in use** (Platform-Infra) |
| `38072` | odoo longpolling / gevent | **in use** (Platform-Infra) |
| `35432` | postgres (Odoo OLTP) | **in use** (Platform-Infra) |
| `36379` | redis | **in use** (Platform-Infra) |
| `33001` | grafana | **in use** (Platform-Infra) |
| `39090` | prometheus | **in use** (Platform-Infra) |
| `39093` | alertmanager | **in use** (Platform-Infra) |
| `33100` | loki | **in use** (Platform-Infra) |
| `35433` | warehouse postgres | reserved — **Data Warehouse** |
| `38120` | login-gateway | reserved — **Backend** |
| `38200` | semantic-api | reserved — **Backend** |
| `33000` | insight-portal | reserved — **Frontend** |

`scripts/dev-bootstrap.sh` checks the four base ports and fails with the name of the offending
container rather than letting the stack come up unhealthy.

---

## 5. `.env` variable names — extend, do not rename

Every name below already exists in `.env.example` with `changeme` for every secret. Add new
variables; do not rename these, because the compose files, `odoo/render-config.py` and the scripts
all read them.

**Compose / host**
`COMPOSE_PROJECT_NAME` `COMPOSE_FILE` `COMPOSE_PATH_SEPARATOR` `TZ` `BIND_ADDRESS`

**Ports**
`ODOO_HOST_HTTP_PORT` `ODOO_HOST_LONGPOLLING_PORT` `POSTGRES_HOST_PORT` `REDIS_HOST_PORT`
`GRAFANA_HOST_PORT` `PROMETHEUS_HOST_PORT` `ALERTMANAGER_HOST_PORT` `LOKI_HOST_PORT`
`WAREHOUSE_HOST_PORT` `LOGIN_GATEWAY_HOST_PORT`

**Postgres**
`POSTGRES_HOST` `POSTGRES_PORT` `POSTGRES_DB` `POSTGRES_USER` `POSTGRES_PASSWORD`
`WAREHOUSE_READER_USER` `WAREHOUSE_READER_PASSWORD`
`POSTGRES_EXPORTER_USER` `POSTGRES_EXPORTER_PASSWORD`

**Odoo**
`ODOO_DB_HOST` `ODOO_DB_PORT` `ODOO_DB_USER` `ODOO_DB_PASSWORD` `ODOO_DB_MAXCONN`
`ODOO_DB_NAME` `ODOO_DBFILTER` `ODOO_ADMIN_PASSWD` `ODOO_LIST_DB` `ODOO_PROXY_MODE`
`ODOO_HTTP_PORT` `ODOO_LONGPOLLING_PORT` `ODOO_WORKERS` `ODOO_MAX_CRON_THREADS`
`ODOO_LIMIT_MEMORY_SOFT` `ODOO_LIMIT_MEMORY_HARD` `ODOO_LIMIT_REQUEST` `ODOO_LIMIT_TIME_CPU`
`ODOO_LIMIT_TIME_REAL` `ODOO_LIMIT_TIME_REAL_CRON` `ODOO_LOG_LEVEL` `ODOO_WITHOUT_DEMO`
`ODOO_EMAIL_FROM` `ODOO_SMTP_SERVER` `ODOO_SMTP_PORT` `ODOO_INIT_MODULES`

**Redis**
`REDIS_HOST` `REDIS_PORT` `REDIS_PASSWORD` `REDIS_MAXMEMORY`

**Observability**
`GRAFANA_ADMIN_USER` `GRAFANA_ADMIN_PASSWORD` `GRAFANA_ROOT_URL`
`PROMETHEUS_RETENTION_TIME` `PROMETHEUS_RETENTION_SIZE` `LOKI_RETENTION_PERIOD`
`ALERTMANAGER_SMTP_FROM` `ALERTMANAGER_SMTP_SMARTHOST`

**Backup**
`BACKUP_DIR` `BACKUP_RETENTION_DAYS`

**Reserved for contracts 01 and 02** — declared by Platform-Infra, consumed by others:
`WAREHOUSE_MASK_SALT_DEFAULT` `WAREHOUSE_MASK_SALT_BCT` (one per tenant, suffix uppercased slug)
`LOGIN_GATEWAY_JWT_ALGORITHM` `LOGIN_GATEWAY_JWT_PRIVATE_KEY_PATH`
`LOGIN_GATEWAY_JWT_PUBLIC_KEY_PATH` `LOGIN_GATEWAY_JWT_KID` `LOGIN_GATEWAY_JWT_ISSUER`
`LOGIN_GATEWAY_JWT_AUDIENCE` `LOGIN_GATEWAY_JWKS_URL` `LOGIN_GATEWAY_ACCESS_TOKEN_TTL`
`LOGIN_GATEWAY_REFRESH_COOKIE_NAME`
`WAREHOUSE_DB` `WAREHOUSE_HOST_PORT` `DBT_THREADS`
`WAREHOUSE_ADMIN_USER` `WAREHOUSE_ADMIN_PASSWORD` (superuser; runs DDL and backups only)
`WAREHOUSE_DB_USER` `WAREHOUSE_DB_PASSWORD` (schema owner; dbt connects as this)
`WAREHOUSE_LOADER_USER` `WAREHOUSE_LOADER_PASSWORD` (CDC loader; INSERT into `raw.*` only, no marts)
`WAREHOUSE_RLS_USER` `WAREHOUSE_RLS_PASSWORD` (SELECT only, NOBYPASSRLS; the semantic-api identity)

> Four warehouse roles, not one, added by the Data Warehouse and Backend agents. A Postgres **superuser
> bypasses RLS unconditionally** and no policy can stop it, so sharing one role between DDL,
> dbt and the dashboard would make every tenant-isolation test pass while proving nothing.
> This is the same "read-only by construction" argument as `warehouse_reader` in section 2.

**How to add a secret.** Put the key in `.env.example` with the literal value `changeme` and add a
length to `LENGTHS` in `scripts/gen-env-secrets.py` if the default 32 characters is wrong. The
generator is idempotent — it never rotates a value that already exists in `.env`, because rotating a
Postgres password behind a live volume produces an Odoo that cannot reach its own database with no
error that says so. Use `--rotate KEY` to rotate deliberately.

The generated alphabet is `[A-Za-z0-9]` only. A `$`, `'` or `:` in a password travels through shell,
YAML, ini files and libpq URIs and eventually produces a failure that looks like a wrong password
rather than a quoting bug.

---

## 6. Makefile target namespace

### Taken by Platform-Infra

```
help                    dev-bootstrap           build                   config
up-dev                  down                    down-hard               restart
ps                      logs                    stats
init-db                 install-modules         psql                    shell                sh
tenant-provision        tenant-backup           tenant-restore
warehouse-reader-check  scan-secret             verify
up-obs                  down-obs
```

### Reserved for later agents — free, and claimed here so nobody collides

| Agent | Reserved names |
|---|---|
| Data Warehouse | `up-analytics` `down-analytics` `dbt-run` `dbt-test` `dbt-docs` `warehouse-backup` `warehouse-restore` |
| Backend | `up-gateway` `up-semantic` `cdc-start` `cdc-status` |
| Frontend | `up-portal` `portal-build` |
| Security | `lint` `sast` `sbom` `sign` `ci-local` |

`make` takes the **last** definition of a duplicated target, silently. Check this list before naming
a new one.

Argument variables in use: `TENANT=` `MODULES=` `FROM=` `INTO=` `SERVICE=` `ARGS=`.

---

## 7. Extension points other agents own

Platform-Infra owns each **mechanism**; the named agent owns the **content**. Neither edits the
other's files.

| Drop-in | Loaded by | Owner of the content |
|---|---|---|
| `observability/prometheus/scrape.d/analytics-*.yml` | `scrape_config_files` in `prometheus.yml` | Data Warehouse |
| `observability/prometheus/rules/analytics-*.yml` | `rule_files: /etc/prometheus/rules/*.yml` | Data Warehouse |
| `observability/grafana/dashboards/analytics-*.json` | Grafana file provider, `foldersFromFilesStructure` | Data Warehouse |
| `observability/grafana/provisioning/datasources/analytics-*.yml` | Grafana merges every `*.yml` in that directory | Data Warehouse |
| `addons/<module>/` | `addons_path` in `odoo/odoo.conf`, mounted read-only in the dev overlay | Platform-Addons |
| `postgres/conf.d/local.conf` | `include_if_exists` at the end of `postgresql.conf` | operator, untracked |
| CDC block in `scripts/tenant-provision.sh` | marked `PHASE 3 CDC HOOK` | Data Warehouse / Backend |

Grafana datasource UIDs to reference from dashboards: `prometheus`, `loki`, `postgres-oltp`.

> `postgres-oltp` connects as `metrics_exporter` and holds `pg_monitor` only — operational metrics,
> **no table data**. Business data belongs in the warehouse datasource with RLS. A Grafana
> datasource is reachable by every user with Editor rights, which is why it gets the narrowest
> identity that still answers operational questions.

Applying a Prometheus drop-in without a restart (`--web.enable-lifecycle` is on):
`curl -XPOST http://127.0.0.1:39090/-/reload`. Validate first — a malformed file makes Prometheus
refuse to start and takes the existing dashboards down with it:

```
docker run --rm -v "$PWD/observability/prometheus:/p" \
  prom/prometheus:v2.55.1 promtool check config /p/prometheus.yml
```

---

## 8. Odoo runtime facts consumers need

| Fact | Value |
|---|---|
| Runtime uid | `100` (`odoo`), non-root |
| setuid/setgid binaries in the image | none — stripped, asserted at build time |
| HTTP inside the network | `http://odoo:8069` |
| Longpolling / gevent | `odoo:8072` |
| Health endpoint | `GET /web/health?db_server_status=1` → 200, or 500 when Postgres is unreachable |
| Config file at runtime | `/opt/odoo/conf/odoo.conf`, mode 0600, **rendered at start** from `/opt/odoo/odoo.conf.template` |
| Filestore | `/var/lib/odoo/filestore/<database>` in volume `odoo19-bct_odoodata` |
| Extra addons | `/mnt/extra-addons`, mounted **read-only** from `./addons` |
| `list_db` | `False` — the database manager is off; databases come from `tenant-provision.sh` |
| `dbfilter` | `^bct$` by default, anchored both ends |
| `db_template` | `template0` |

`admin_passwd` is a `FileOnlyOption` in `odoo/tools/config.py`: Odoo offers no CLI flag and no
environment variable for it. That is why `odoo.conf` is a template rendered by
`odoo/render-config.py` at container start, and why editing `odoo/odoo.conf` in the dev overlay
takes effect on `docker compose restart odoo` with no rebuild.

**Adding a config option:** Odoo only *warns* on an unknown key in the config file and then stores it
unparsed. A typo is silent. Validate the name against the image before adding one:

```
docker run --rm --entrypoint python3 <odoo-image> -c \
  "import re;print(sorted(set(re.findall(r'dest=[\'\\\"]([a-z0-9_]+)', open('/usr/lib/python3/dist-packages/odoo/tools/config.py').read()))))"
```

**Serving more than one tenant over HTTP** requires widening `ODOO_DBFILTER`, e.g.
`^(bct|acme)$`. `tenant-provision.sh` prints the exact line and deliberately does not apply it:
widening the filter changes which tenants are reachable over HTTP and should be a reviewed edit, not
a side effect of a provisioning run.

---

## 9. Backups

`scripts/tenant-backup.sh <slug>` writes to `backups/<slug>/<UTC stamp>/`:

| File | What |
|---|---|
| `database.dump` | `pg_dump --format=custom --compress=9 --no-owner --no-acl` |
| `filestore.tar.gz` | `/var/lib/odoo/filestore/<db>` |
| `manifest.json` | tenant, timestamp, sizes, SHA-256, git commit and branch |
| `SHA256SUMS` | verified by `tenant-restore.sh` **before** it drops anything |

**Both halves, always.** A database-only Odoo backup restores to a system where every attachment,
logo, product image and generated PDF is a broken link, because `ir_attachment` rows point at
filestore paths the dump does not contain.

`scripts/tenant-restore.sh <slug> <dir> [--into OTHER]` verifies checksums, stops Odoo (it holds a
connection pool, and `DROP DATABASE` fails while any session is attached), recreates the database
with `TEMPLATE template0 LC_COLLATE 'C'` (the shape Odoo itself uses), restores both halves and
re-applies `database-baseline.sql`. `--into` restores as a different tenant, which is how a restore
is rehearsed without risking the original.

Verified by round trip: same row counts, same filestore file count, and an identical aggregate
SHA-256 over every filestore file in source and restored database.

`jq` is not installed on the host and is not a dependency of anything here. JSON goes through
`python3`.

---

## 10. Measured footprint

Two figures, because only one of them is honest on its own. `docker stats` immediately after
`make up-dev` catches Odoo before any worker has loaded the registry; the number roughly doubles on
the first real request and then stays there. Budget against the second.

Idle, immediately after `make up-dev`, one initialised tenant database:

```
NAME                  MEM USAGE / LIMIT     MEM %
odoo19-bct-postgres   135.8MiB / 15.25GiB   0.87%
odoo19-bct-redis      3.633MiB / 15.25GiB   0.02%
odoo19-bct-odoo       189.8MiB / 15.25GiB   1.22%
```
**Total: 329 MiB.**

Settled, after a full `make verify` run (both Odoo workers have loaded the registry, Postgres has
warmed its cache) — **budget against this one**:

```
NAME                  MEM USAGE / LIMIT
odoo19-bct-postgres   158.3MiB / 15.25GiB
odoo19-bct-redis      3.648MiB / 15.25GiB
odoo19-bct-odoo       557.9MiB / 15.25GiB
```
**Base stack total: 755 MiB.** The constraint is 4 GiB, so roughly 3.3 GiB of headroom.

With the observability overlay running as well (`make up-obs`):

```
odoo19-bct-prometheus          37.87MiB
odoo19-bct-grafana             70.16MiB
odoo19-bct-loki                73.75MiB
odoo19-bct-promtail            33.57MiB
odoo19-bct-alertmanager        14.82MiB
odoo19-bct-postgres-exporter   17.61MiB
odoo19-bct-node-exporter       12.23MiB
```

**Full stack total: 593 MiB.**

Worst case for Odoo is `ODOO_WORKERS * ODOO_LIMIT_MEMORY_HARD` = 2 × 1280 MiB, reached only under
sustained load. Budget against that number, not against idle, when adding services to the VPS.

---

## 11. Line endings — read this before adding a file

The primary dev host is Windows with `core.autocrlf=true`. `.gitattributes` sets
`* text=auto eol=lf` for the whole tree. **Do not weaken it.** A CRLF `.sh` mounted into a Linux
container fails with `bad interpreter: /bin/sh^M`, and a CRLF `.env` puts a trailing `\r` inside a
password, which presents as an authentication failure with a correct-looking password.

`scripts/dev-bootstrap.sh` fails the bootstrap if any tracked `*.sh`, `*.py`, `*.sql`, `Makefile` or
`Dockerfile` carries CRLF in the git index. To repair: `git add --renormalize . && git commit`.

### Related Windows trap: MSYS argument conversion

Git Bash rewrites POSIX-looking arguments into Windows paths before a native `.exe` sees them. Two
consequences that cost real time:

```
$ docker compose exec -T odoo find / -xdev -perm /6000 -type f
find: invalid mode 'C:/Program Files/Git/6000'          # /6000 was rewritten
```

Prefix with `MSYS_NO_PATHCONV=1` for any docker command carrying a container-side absolute path.

But **do not export it globally**. `scripts/lib/common.sh` scopes it to the `dc()` function
precisely because exporting it breaks every other native tool — the host's `python3.exe` then
receives `/e/Projects/...` literally and resolves it as `E:\e\Projects\...`. For the same reason
`dc()` passes *relative* `-f` paths and `cd`s to the repo root first: a relative path needs no
conversion, so container paths and compose file paths can both be correct in the same invocation.
