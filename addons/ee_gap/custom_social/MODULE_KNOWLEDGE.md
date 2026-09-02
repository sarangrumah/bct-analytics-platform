---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_social
manifest_version: 19.0.0.1.0
---

# custom_social

## Purpose
A minimal social-media account + outbound post scheduler. Stores `social.account` rows per (`platform`, `handle`) with an encrypted API token (via `custom.ir.config.get_encrypted`), and `social.post` records that move through `draft → scheduled → published / failed / cancelled`. A daily cron picks scheduled posts whose `scheduled_at` is in the past and calls `_publish()` — which currently writes a synthetic `external_post_id` (`"manual-<iso>"`) without actually pushing to any platform. Provider-specific adapters are referenced in the docstring but NOT implemented in this module.

## Business Flow
- Admin registers a `social.account` per platform (facebook/instagram/x/linkedin/tiktok/youtube) with a handle. The encrypted token is stored under `custom_social.api_token.<account_id>` via `custom.ir.config`; `api_token_set` is a compute that surfaces presence to the UI.
- A user drafts a `social.post` (required `account_id`, `body`, `scheduled_at`).
- `action_schedule()` flips draft→scheduled (`UserError` if not draft) and writes a PDP audit row.
- `action_publish_now()` calls `_publish()` directly. `_publish()` is the per-platform adapter dispatch hook; the default in this module just synthesises `external_post_id = "manual-<iso-now>"`, stamps `published_at`, audits, and writes state=published. On exception it sets state=failed + `last_error`.
- `_cron_publish_due` (scheduled in `data/ir_cron_data.xml`) every tick searches scheduled posts past `scheduled_at` and calls `_publish()` per post (best-effort, logged on failure).
- `action_cancel()` cancels any non-published post.

## Key Models
- `social.account` — `(platform, handle)` unique; holds metadata + encrypted-token presence flag.
- `social.post` — Outbound post record (`mail.thread + pdp.audited.mixin`, classification `public`).

## Important Fields
- `social.account.platform` (Selection: facebook/instagram/x/linkedin/tiktok/youtube) — channel taxonomy.
- `social.account.handle` (Char, required) — `@handle` or page id; unique per platform.
- `social.account.api_token_set` (Boolean, computed) — non-stored compute over `custom.ir.config.get_encrypted`.
- `social.post.state` (Selection: draft/scheduled/published/failed/cancelled) — main workflow.
- `social.post.scheduled_at` (Datetime, required) — cron gate.
- `social.post.published_at` (Datetime, readonly) — set by `_publish()`.
- `social.post.external_post_id` (Char, readonly) — platform-issued id; currently `"manual-<iso>"`.
- `social.post.last_error` (Text, readonly) — captured exception string on failure.
- `social.post.media_attachment_id` (M2o `ir.attachment`) — optional image/video.

## Public Methods
- `social.post.action_schedule() / action_publish_now() / action_cancel()` — workflow buttons.
- `social.post._publish()` — adapter hook; default implementation is a stub.
- `social.post._cron_publish_due()` (`@api.model`) — scheduled action entrypoint.
- `social.account._ir_config_key()` — returns `"custom_social.api_token.<id>"`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mail`.
- **Inherits from:** `mail.thread`, `pdp.audited.mixin` (post).
- **Extended by:** intended extension surface is `social.post._publish()` per platform (FB/IG/X/LinkedIn). Not implemented in-tree.
- **External calls:** none in default code path. `_publish()` is where real platform SDK / Graph-API calls would go.
- **Cross-vertical:** generic.

## Gotchas
- **No actual platform integration.** `_publish()` is a stub — it does not call Facebook Graph, X API, Instagram, etc. Every "published" post is a mock.
- **No content validation per platform** (X char limit, IG image required, etc.) — body is free-text.
- **`media_attachment_id` is captured but not transmitted anywhere** since no real adapter exists.
- **`api_token_set` is a non-stored compute** — every list view triggers an `IrCfg.get_encrypted` call per row.
- **`_cron_publish_due` does not honour per-account rate limits** — if 1000 posts are due, all 1000 attempts run in the same cron tick.
- **PDP audit classification is `public`** for `social.post` (rightly so) but a published post may still contain PII in its body — no body-content scan.
- **`_publish()` synthesises an external_id even on the "manual" platform value** — there is no such platform in the selection. The string `"manual-<iso>"` is only a placeholder for the future adapter return.

## Out of Scope
- Real publishing to any social platform — every adapter is a stub.
- Engagement metrics (likes/comments/reposts) — no inbound webhook.
- Multi-image / carousel / video / story formats.
- Per-platform character / aspect-ratio / hashtag validation.
- Inbound social listening / mentions / DMs.
- Approval workflow before publishing (could be added via `custom_approval`).
