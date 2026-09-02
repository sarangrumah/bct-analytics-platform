---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_dashboards
manifest_version: 19.0.0.2.0
---

# custom_dashboards

## Purpose
A CE-targeted reimplementation of EE's `board` dashboard builder. Dashboards (`custom.dashboard`) own a One2many of tiles (`custom.dashboard.tile`); each tile computes a single KPI — count / sum / avg / last_value / formula / chart_bar / chart_pie — over any model + domain, caches the result as JSON on the row, and a cron auto-refreshes tiles whose cache exceeds their per-tile interval.

Adds publishing (`is_published`), per-group ACLs (`allowed_group_ids`), public read-only share-by-token endpoint (`/custom_dashboard/share/<token>`), drill-down from tile to underlying records, and an **Ask AI** entry point that forwards dashboard context + question to `custom.ai._recommend`.

## Business Flow
- User creates a `custom.dashboard` with a name + optional description; defaults to unpublished, private to owner.
- User adds `custom.dashboard.tile` rows: pick `tile_type`, set `model_name` (technical, e.g. `helpdesk.ticket`), domain (Odoo domain literal), and the relevant compute inputs (`measure_field` for sum/avg/last_value/chart; `groupby_field` for chart; `formula_expression` for formula).
- `action_refresh` on the tile evaluates the domain via `safe_eval`, dispatches to the per-type compute helper, stores result as JSON in `cached_value`, sets `cached_at`, clears or sets `last_error`. The `_compute_cached_display` renders a human string for scalar tiles or "N series" for charts.
- `_cron_refresh_stale_tiles` (cron) iterates all tiles and re-runs `action_refresh` on any whose `cached_at` is older than `refresh_interval_seconds` (floor 30s).
- `action_open_tile_records` returns an `ir.actions.act_window` on the tile's model+domain for drill-down.
- `action_generate_share_link` mints `share_token` (`secrets.token_urlsafe(32)`); `share_url` exposes `{web.base.url}/custom_dashboard/share/<token>`. `action_revoke_share_link` clears the token.
- `action_ask_ai(question)` packages dashboard + tile metadata + cached values via `_custom_ai_payload(question)` and calls `custom.ai._recommend(model="custom.dashboard", res_id=self.id, payload=…)`. Result text lands in `last_ai_answer` (HTML, sanitized), `last_ai_question`, `last_ai_at`, and is mirrored to chatter.
- Publish/unpublish gating is via `action_publish` / `action_unpublish`; ACL enforcement against `allowed_group_ids` is via record rules in `security/security.xml`.
- Public share controller `/custom_dashboard/share/<token>` renders `share_templates.xml` read-only.

## Key Models
- `custom.dashboard` — Container with metadata, owner, ACLs, share token, AI Q&A scratchpad, One2many tiles. Inherits `mail.thread` + `pdp.audited.mixin`.
- `custom.dashboard.tile` — Single KPI definition + cache; one row per tile.

## Important Fields
- `custom.dashboard.is_published` (Boolean, tracked) — gates list visibility for non-owners.
- `custom.dashboard.is_public` (Boolean, tracked) — enables `/custom_dashboard/share/<token>` rendering.
- `custom.dashboard.allowed_group_ids` (M2m res.groups) — read ACL beyond owner.
- `custom.dashboard.share_token` (Char, indexed, unique) — share URL secret.
- `custom.dashboard.last_ai_question` / `last_ai_answer` (Char / Html-sanitized) / `last_ai_at` (Datetime) — Ask AI scratchpad.
- `custom.dashboard.tile_ids` (One2many) — tile composition.
- `custom.dashboard.tile.tile_type` (Selection count/sum/avg/last_value/formula/chart_bar/chart_pie).
- `custom.dashboard.tile.model_name` (Char) — technical model; resolved via `self.env[...]`.
- `custom.dashboard.tile.domain` (Char) — string literal evaluated with `safe_eval`.
- `custom.dashboard.tile.measure_field` / `groupby_field` (Char) — compute inputs.
- `custom.dashboard.tile.formula_expression` (Text) — `safe_eval` expression with `env`/`domain`/`model`/`fields` in scope.
- `custom.dashboard.tile.refresh_interval_seconds` (Integer, default 300, floor 30) — cron staleness threshold.
- `custom.dashboard.tile.cached_value` (Text, JSON) — scalar `{"value": ...}` or chart `{"labels": [...], "data": [...]}`.
- `custom.dashboard.tile.cached_at` (Datetime, readonly) — last successful refresh.
- `custom.dashboard.tile.last_error` (Char, readonly) — last refresh failure message.

## Public Methods
- `custom.dashboard.action_publish()` / `action_unpublish()`.
- `custom.dashboard.action_refresh_all_tiles()` — fan-out refresh.
- `custom.dashboard.action_generate_share_link()` / `action_revoke_share_link()`.
- `custom.dashboard.action_open_tile_records(tile_id)` — drill-down delegate.
- `custom.dashboard.action_ask_ai(question)` — AI Q&A entry.
- `custom.dashboard._custom_ai_payload(question)` — payload builder (dashboard + all tiles + cached values).
- `custom.dashboard.tile.action_refresh()` — main compute loop; dispatches by `tile_type`.
- `custom.dashboard.tile._cron_refresh_stale_tiles()` (`@api.model`) — cron entry.
- `custom.dashboard.tile.action_open_tile_records()` — `ir.actions.act_window` on model+domain.
- `custom.dashboard.tile._compute_count/_sum/_avg/_last_value/_formula/_chart` — per-type computers.
- Controller: `GET /custom_dashboard/share/<token>` (public).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `board`.
- **Inherits from:** `mail.thread`, `pdp.audited.mixin` on `custom.dashboard`.
- **Extended by:** verticals may seed dashboards via XML data files; the tile compute registry is in-Python so adding a new `tile_type` requires forking `custom_dashboard_tile.py`.
- **External calls:** `custom.ai._recommend` (via `custom_ai_bridge`) for Ask AI.
- **Cross-vertical:** generic KPI surface; the BRD analyzer should map any "dashboard / KPI tile / chart / executive summary" requirement here.

## Gotchas
- **`safe_eval` is used for both domain AND formula** — `formula_expression` gets `env` and `fields` in its scope, which is **substantial power**. Treat write access to tile as semi-admin; the security file should restrict tile editing.
- **No record-level ACL on share endpoint beyond token + is_public** — anyone with the URL can view; rotate aggressively.
- **Cache refresh is cron-driven only** — there is no model invalidation hook; a tile may show stale data up to its `refresh_interval_seconds`. Manual `action_refresh` is the only force.
- **`read_group` results may include falsy group labels** — the chart helper renders `False` as `""`, which can collide visually with empty-string groups.
- **Per-tile `sum`/`avg` does `search_read` (no read_group)** — for very large domains this is O(N) memory. Use `formula` with `read_group` for big aggregations.
- **`board` is in `depends` but only for menu/icon parity** — this module does not subclass `board.board`.
- **`is_public` and `allowed_group_ids` are independent** — a public-flagged dashboard with a token bypasses `allowed_group_ids`.

## Out of Scope
- **Interactive grid drag-and-drop layout** — tiles are listed by `sequence` only; no positional grid.
- **Time-range pickers / cross-tile filters** — domain is per-tile static.
- **Export to PDF / scheduled email digests** — not implemented.
- **Drill-down with breadcrumb back to dashboard** — `action_open_tile_records` opens a fresh act_window.
- **Native chart rendering** — `chart_bar`/`chart_pie` only produce `{labels, data}` JSON; the UI is responsible for charting.
- **Per-user personalised tile parameters** — tiles are global to the dashboard.
