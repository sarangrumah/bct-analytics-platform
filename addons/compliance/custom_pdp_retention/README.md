# Custom PDP Retention

Define retention policies per model + classification; a daily cron
scans records that have exceeded the configured retention window and
applies the configured action.

## Models

- `pdp.retention.policy` — fields: `model_id`, `classification_id`,
  `retention_days`, `action` (`anonymize` / `archive` / `delete`),
  `active`. Defaults are seeded from `data/pdp_retention_defaults.xml`
  (e.g. inactive partners → anonymize after 730 days).

## Cron

`data/pdp_retention_cron.xml` schedules `_cron_apply_retention()` once
per day. The cron iterates active policies, finds eligible records
(`create_date < now - retention_days`), and applies the action. Every
operation is audited.

## Dashboard

`views/pdp_retention_policy_views.xml` provides a tabular dashboard:
policy × model × records-eligible × last-run × next-run.

## Security Groups

- `pdp.group_dpo` — manage policies and view dashboard.

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`

## Install

Install after `custom_pdp_audit`. Review seed defaults in
`data/pdp_retention_defaults.xml` before enabling — they ship inactive
so you can edit before going live.

## Reference

- `docs/pdp-compliance.md` § "Retention"
