---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_forum
manifest_version: 19.0.0.1.0
---

# custom_forum

## Purpose
Extends CE `website_forum` with three capabilities: AI toxicity / spam moderation that can auto-close posts and notify moderators (via `custom_ai_bridge`), PDP-aware author display masking (`Anonymous-<id>` alias on `display_name`), and Indonesian-tier reputation badges on `res.users` derived from karma. A trending-topics aggregator (`custom.forum.trending.topic`) rebuilds top-N tag rankings per period via cron. An hourly cron batch-scores unscored active posts.

## Business Flow
- A user posts on the forum (`forum.post`, CE-managed state `active/pending/close/offensive/flagged`).
- The hourly cron `cron_ai_moderate_pending_posts` selects up to 50 posts with `x_ai_moderation_score is False AND state=active` and calls `action_ai_moderate()`.
- `action_ai_moderate()` per post: calls `custom.ai._recommend` with `{content: post.content[:4000]}`, parses `score` (float) + `label` (mapped via `_parse_ai_label`: toxic/offensive/abuse → `flag`; junk/advertisement/promotion → `spam`; uncertain/borderline → `review`; else `safe`).
  - Writes `x_ai_moderation_score`, `x_ai_moderation_label`, `x_ai_moderated_at`.
  - If label ∈ {flag, spam}: posts a chatter note, flips post state to `close` (only if currently in `active/pending`), schedules `mail.mail_activity_data_todo` for every user in `custom_forum.group_manager`.
  - If label=spam AND score > `custom_forum.spam_threshold` (default 0.8): emails the manager group via `message_post(partner_ids=...)`.
- Helpful-vote count: `forum.post.vote.create/write/unlink` triggers `_compute_x_helpful_count` (`sum(1 for v in vote_ids if str(v.vote)=='1'`).
- Author masking: when `x_pdp_author_masked=True`, `_compute_display_name` (override of CE) appends `— Anonymous-<id>` to the post's `display_name`. Helper `_get_masked_author_label()` for templates.
- Reputation: `res.users.x_indonesia_badge` (computed, stored) maps `karma` to one of `pemula(0+) / lanjut(200+) / ahli(1000+) / master(5000+)`.
- Trending: `cron_compute_trending` rebuilds `custom.forum.trending.topic` for periods `day/week/month`. Score = `post_count*2 + view_count`; top 10 per forum per period. Old rows for the period are unlinked before rewriting.

## Key Models
- `forum.post` (inherited) — Adds AI moderation fields, helpful count, PDP masking flag, masked display name.
- `forum.post.vote` (inherited) — Recomputes parent's `x_helpful_count` on every CRUD.
- `custom.forum.trending.topic` — Aggregated (forum_id, tag_id, period) trend row; unique constraint.
- `res.users` (inherited) — Adds `x_indonesia_badge` derived from karma.

## Important Fields
- `forum.post.x_ai_moderation_score` (Float 0..1) — toxicity probability.
- `forum.post.x_ai_moderation_label` (Selection: safe/review/flag/spam, default safe).
- `forum.post.x_ai_moderated_at` (Datetime) — last scoring.
- `forum.post.x_pdp_author_masked` (Boolean) — flips display_name to `<title> — Anonymous-<id>`.
- `forum.post.x_helpful_count` (Integer, computed, stored) — count of `vote==+1`.
- `custom.forum.trending.topic.score` (Integer) — `post_count*2 + view_count`.
- `custom.forum.trending.topic.period` (Selection: day/week/month) — refresh cadence.
- `custom.forum.trending.topic.rank` (Integer) — per-forum 1..10.
- `res.users.x_indonesia_badge` (Selection: pemula/lanjut/ahli/master) — karma tier.
- Module constants: `_DEFAULT_SPAM_THRESHOLD = 0.8`, `_AI_BATCH_LIMIT = 50`, `_TOP_N = 10`, `_PERIOD_DAYS = {day:1, week:7, month:30}`.
- `ir.config_parameter` `custom_forum.spam_threshold` — runtime override of spam threshold.

## Public Methods
- `forum.post.action_ai_moderate()` — single/batch AI moderation.
- `forum.post.cron_ai_moderate_pending_posts()` (`@api.model`) — hourly batch.
- `forum.post._get_spam_threshold()` (`@api.model`) — reads ir.config_parameter or default.
- `forum.post._notify_forum_moderators(label, score)` — activity scheduler.
- `forum.post._email_forum_admin_spam(score)` — manager notification.
- `forum.post._get_masked_author_label()` — template helper.
- `custom.forum.trending.topic._compute_trending_for_period(period)` (`@api.model`) — per-period rebuild.
- `custom.forum.trending.topic.cron_compute_trending()` (`@api.model`) — runs all three periods.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `website_forum`.
- **Inherits from:** `forum.post`, `forum.post.vote`, `res.users` (CE).
- **Extended by:** none declared.
- **External calls:** `custom.ai._recommend`. No HTTP.
- **Cross-vertical:** generic.

## Gotchas
- **Auto-close target state is `close`** (not `closed` — CE's selection uses `close`). The hard-coded constant `_FLAG_TARGET_STATE = "close"` documents this.
- **AI moderation only re-scores posts where `x_ai_moderation_score is False`** — once scored (even at 0.0) the cron skips it. Manual rescore requires clearing the field.
- **Author masking modifies `display_name` only** — the underlying `create_uid`/author fields are unchanged; only the rendered name on `display_name` is masked. Database queries and admin views still see the real author.
- **Karma badge thresholds tuple is iterated in order and breaks on first match**, but the tuple is sorted DESCENDING (master, ahli, lanjut, pemula 0) — the first match is the highest tier. This is intentional but easy to misread.
- **`x_helpful_count` is recomputed on every vote CRUD** — `vote` is a CE Selection of `-1/0/1` stored as string; the code does `str(v.vote) == "1"`.
- **Trending compute unlinks ALL entries for the period before rewriting** — there's a window where the table is empty for that period.
- **Spam email notification uses `message_post(partner_ids=...)`** — relies on inbound notification settings; not a direct `mail.mail`.
- **Moderation activity is scheduled per manager user** — large moderation teams get many activities; no throttling.

## Out of Scope
- Per-language toxicity tuning (model is whatever `custom_ai_bridge` is wired to).
- Reputation bonus/penalty mutation from this module (uses CE karma only).
- Cross-forum moderation policies (moderator group is global).
- Anonymous posting workflow (only display masking is implemented, not write-time anonymisation).
- Trending dashboards / website widgets — `custom.forum.trending.topic` is data-only.
