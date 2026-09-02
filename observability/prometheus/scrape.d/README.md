# Prometheus scrape drop-in directory

`prometheus.yml` loads every `*.yml` in this directory through
`scrape_config_files`. Each file must carry a **top-level `scrape_configs:`
key**, exactly like the main config:

    scrape_configs:
      - job_name: warehouse
        static_configs:
          - targets: ["warehouse-exporter:9187"]
            labels:
              service: warehouse

No `global:` block — that stays in `prometheus.yml`.

> **This paragraph used to say the opposite** — that a drop-in was a bare list
> with no `scrape_configs:` key. That is wrong, and Prometheus 2.55 rejects it
> at startup rather than ignoring it:
>
>     FAILED: error loading scrape configs:
>       "/etc/prometheus/scrape.d/analytics-scrape.yml": yaml: unmarshal errors:
>       line 28: cannot unmarshal !!seq into config.ScrapeConfigs
>
> The Data Warehouse agent hit it, wrote the correct form in
> `analytics-scrape.yml`, and reported the README across the ownership boundary
> instead of editing it. Corrected 2026-08-31.

## Ownership

| Pattern | Owner |
|---|---|
| `analytics-*.yml` | **Data Warehouse agent** |
| anything else | Platform-Infra |

Platform-Infra owns the loading mechanism and never edits `analytics-*.yml`.
The Data Warehouse agent never edits `prometheus.yml`.

## Applying a change

    docker compose -p odoo19-bct restart prometheus

or, without a restart (`--web.enable-lifecycle` is enabled):

    curl -XPOST http://127.0.0.1:39090/-/reload

## Validate before you restart

A malformed file makes Prometheus refuse to start, and it will take the
existing dashboards down with it. Check first:

    docker run --rm -v "$PWD/observability/prometheus:/p" \
      prom/prometheus:v2.55.1 promtool check config /p/prometheus.yml
