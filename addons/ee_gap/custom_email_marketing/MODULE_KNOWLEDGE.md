---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_email_marketing
manifest_version: 19.0.0.2.0
---

# custom_email_marketing

## Purpose
Extends CE `mass_mailing` with EE-equivalent and UU-PDP-specific features: a reusable HTML template gallery + apply wizard, a A/B testing harness (`custom.email.ab.test`) that clones the parent mailing into two variants and picks a winner by opens/clicks/replies, a per-mailing PDP consent filter that drops recipients without an active `pdp.consent` for the chosen purpose, a dynamically-rendered UU PDP unsubscribe footer (Bahasa Indonesia) appended at send time, and a 3-strike auto-blacklist on `mailing.trace.set_bounced`.

Delivery channel remains exactly the standard `mass_mailing` SMTP path — this module does NOT add WhatsApp/SMS, only extends the existing email pipeline.

## Business Flow
- A user designs a mailing in CE `mass_mailing`. They can click "Apply Template" → opens `custom.email.apply.template.wizard` which fetches a `custom.email.template.gallery` row and writes `subject` + `body_arch` + `body_html` onto the mailing, plus `x_gallery_template_id` for telemetry, and bumps `times_used`.
- Optionally the user sets `x_consent_purpose_id` on the mailing (an existing `pdp.consent.purpose` like "marketing").
- On send (`_action_send_mail` override): if `x_consent_purpose_id` is set, `_filter_recipients_by_consent(res_ids)` resolves each record's partner and calls `pdp.consent.check_consent(partner, purpose_code)`; recipients without active consent are removed and the dropped count is written to `x_consent_filtered_count`.
- If `x_uu_pdp_footer` is True, `_get_pdp_footer_html()` builds an Indonesian-language footer with the controller name (`company.display_name`), DPO email (`company.x_pdp_dpo_email || company.email`), and the standard one-click unsubscribe URL; the footer is temporarily appended to `body_html` for the call to super then restored in `finally`.
- For A/B testing the user creates a `custom.email.ab.test` linked to a parent mailing with two subject+body variants and `split_pct`. `action_split_send()` clones the parent into `[A]` and `[B]` mailings, shuffles the audience, splits it by `split_pct`, calls `action_send_mail(res_ids=...)` on each variant, and schedules `evaluate_after = now + 24h`.
- The cron `cron_evaluate_winner` (every 30 min or as scheduled) picks running tests past their `evaluate_after` and calls `_evaluate_one`, which counts `mailing.trace` events (opens/clicks/replies) per variant, writes `variant_a_score / variant_b_score / winner`, and flips state to `concluded`.
- Tracking: every `mailing.trace.set_opened()` bumps `x_open_count` and stamps `x_first_open_at` on first open; `set_clicked` bumps `x_click_count`; `set_bounced` calls `_blacklist_bounce()` which adds the email to `mail.blacklist` after 3 distinct hard-bounce traces.

## Key Models
- `custom.email.template.gallery` — Reusable HTML template with category/language/thumbnail, `times_used` counter, suggested mailing lists.
- `custom.email.ab.test` — A/B run header (`draft/running/concluded`), two variant subject+body pairs, split %, metric, winner + scores.
- `mailing.mailing` (inherited) — Adds `x_consent_purpose_id`, `x_gallery_template_id`, `x_uu_pdp_footer`, `x_consent_filtered_count`; overrides `_action_send_mail`.
- `mailing.trace` (inherited) — Adds `x_first_open_at`, `x_open_count`, `x_click_count`; overrides `set_opened / set_clicked / set_bounced`.

## Important Fields
- `mailing.mailing.x_consent_purpose_id` (M2o `pdp.consent.purpose`) — gating purpose; recipients without active consent are dropped.
- `mailing.mailing.x_uu_pdp_footer` (Boolean, default True) — UU PDP footer toggle.
- `mailing.mailing.x_consent_filtered_count` (Integer, readonly) — # recipients excluded on last send.
- `mailing.mailing.x_gallery_template_id` (M2o) — telemetry back-link to gallery.
- `custom.email.ab.test.split_pct` (Integer, default 50, constrained 1..99) — % to variant A.
- `custom.email.ab.test.winner_metric` (Selection: opens/clicks/replies) — score metric.
- `custom.email.ab.test.evaluate_after` (Datetime) — cron gate (`sent_at + 24h`).
- `custom.email.ab.test.variant_a_score / variant_b_score / winner` (Integer/Selection) — outcome.
- `mailing.trace.x_first_open_at / x_open_count / x_click_count` (Datetime/Integer) — engagement counters.
- `custom.email.template.gallery.category` (Selection: welcome/newsletter/promo/transactional/reminder) — taxonomy.
- `HARD_BOUNCE_BLACKLIST_THRESHOLD = 3` (module constant) — distinct hard-bounce traces per email before auto-blacklist.

## Public Methods
- `custom.email.template.gallery.action_apply_to_mailing(mailing_id)` — copies template onto a mailing.
- `custom.email.ab.test.action_split_send()` — clones + dispatches variants.
- `custom.email.ab.test.action_evaluate_winner() / _evaluate_one() / cron_evaluate_winner()` — winner picking.
- `mailing.mailing._get_pdp_footer_html(res_id, email_to)` — footer renderer.
- `mailing.mailing._filter_recipients_by_consent(res_ids)` — returns `(kept_ids, filtered_count)`.
- `mailing.mailing._action_send_mail(res_ids)` — overridden: consent filter + footer.
- `mailing.mailing.action_open_apply_template_wizard()` — opens wizard.
- `mailing.trace._blacklist_bounce()` — adds to `mail.blacklist` after threshold.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_consent`, `mass_mailing`, `queue_job`.
- **Inherits from:** `mass_mailing`'s `mailing.mailing` + `mailing.trace`; `mail.thread` on new models.
- **Extended by:** none declared.
- **External calls:** SMTP via `mass_mailing` super; no HTTP from this module.
- **Cross-vertical:** generic; channel = email only.

## Gotchas
- **Single channel: email** (SMTP via `mass_mailing`). The A/B harness only handles email mailings — no SMS/WhatsApp A/B.
- **Consent filter resolves partner via `getattr(rec, 'partner_id', False)`** for non-`res.partner` mailing models; records without `partner_id` are KEPT (the code can't prove non-consent), so consent filter is permissive for `mailing.contact` audiences.
- **Footer is appended by mutating `body_html` then restoring** in a try/finally — concurrent sends on the same mailing record could race; in practice `_action_send_mail` is serialised by `mass_mailing` so this is safe but worth knowing.
- **A/B audience source is `parent._get_remaining_recipients()`**, shuffled with `random.shuffle` (no fixed seed) — re-running `action_split_send` would produce different splits, but the constraint `state != draft` prevents that.
- **`evaluate_after` is hard-coded to `sent_at + 24h`** — not configurable per A/B test.
- **Auto-blacklist counts only `failure_type=mail_bounce` + `trace_status=bounce` traces** — soft bounces are NOT counted.
- **`body_arch` is written by template-apply but body sanitisation is `sanitize=False`** — gallery HTML is trusted; XSS by malicious template authors is possible.
- **`x_uu_pdp_footer` footer falls back to a generic notice without a clickable unsubscribe link** when `res_id`/`email_to` aren't passed (typical for batch sends); per-recipient unsubscribe still works via the standard `mass_mailing` token in the mail itself.
- **A/B winner cron evaluates ALL running tests past `evaluate_after`** — there is no rate limit; a backlog can run many evals in one tick.

## Out of Scope
- Multi-channel mailing (SMS/WhatsApp/push).
- Recipient-level personalisation beyond what `mass_mailing` provides.
- Drip / multi-step sequences — those live in `custom_marketing_automation`.
- A/B testing across more than two variants.
- Bounce categorisation beyond hard/soft mail-bounce.
