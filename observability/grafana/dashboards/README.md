# Grafana dashboard drop-in directory

Grafana provisions every `*.json` in this directory (and its subdirectories)
through the file provider configured in
`../provisioning/dashboards/dashboards.yml`. `foldersFromFilesStructure` is on,
so a subdirectory becomes a Grafana folder.

Adding a dashboard is: write the JSON here, `git add`, done. No provisioning
change, no restart — the provider rescans every 30 s.

## Ownership

| Pattern | Owner |
|---|---|
| `analytics-*.json` | **Data Warehouse agent** |
| anything else | Platform-Infra |

Platform-Infra owns the loading mechanism (`dashboards.yml`, the volume mount,
the datasource UIDs) and never edits `analytics-*.json`. The Data Warehouse
agent never edits `dashboards.yml`.

## Datasource UIDs to reference

Hard-code these `uid` values in dashboard JSON. They are stable and provisioned
in `../provisioning/datasources/datasources.yml`:

| uid | What |
|---|---|
| `prometheus` | Metrics (default) |
| `loki` | Logs |
| `postgres-oltp` | Odoo's OLTP Postgres as `metrics_exporter` — **operational metrics only**, `pg_monitor` grants no table data |

The Data Warehouse agent will provision the warehouse datasource in
`analytics-datasources.yml`; use whatever uid that file declares, not this one.
Business data must not be read through `postgres-oltp`.

## `allowUiUpdates: false`

Dashboards are code. An edit made in the Grafana UI is overwritten on the next
scan — that is intended. To keep a change: panel menu → *Share* → *Export* →
*Save JSON to file*, then commit it here.
