---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_livechat
manifest_version: 19.0.0.2.0
---

# custom_livechat

## Purpose
Extends CE `im_livechat` with EE-equivalent features: convert an active chat (`discuss.channel`) into a `helpdesk.ticket` with priority + last-50-message transcript, canned responses with `:shortcut` expansion, regex-driven chatbot scripts, simple skill-based + round-robin operator routing on the first inbound message, AI suggested reply via `custom_ai_bridge` with payload-hash caching, and a 1-5 visitor satisfaction rating with feedback.

This module is the operator-side companion to the platform's helpdesk: live chat is for synchronous conversation; once it needs to persist as a ticket, escalation creates exactly one `helpdesk.ticket` and links both records.

## Business Flow
- A visitor opens a livechat → CE creates a `discuss.channel` of type `livechat`. On the first `message_post` (this module's override) where no `livechat_operator_id` is assigned, `_custom_livechat_pick_operator(body_text)` runs: if `im_livechat.channel.x_skill_tags` contains any keyword appearing in the visitor message, the first matching operator wins; otherwise round-robin by least-busy (count of open channels per operator).
- The agent types `:shortcut` → JS asset (`canned_response_composer.js`) calls `custom.livechat.canned.response.expand_canned(shortcut)` which returns `{shortcut, body, name, found}` and increments `times_used`.
- A chatbot script (`custom.livechat.chatbot.script`) drives the early turns: each `custom.livechat.chatbot.step` has `step_type` ∈ `text / question / forward_to_operator / end`. `get_next_step(current_id, user_msg)` matches `expected_answers` regex (comma-separated) case-insensitively; on match → next sequential step; on miss → `next_step_default`. `forward_to_operator` / `end` terminate.
- The agent clicks "AI Suggested Reply" → `action_ai_suggest_reply()` builds a 10-message history payload, computes a sha1 hash, skips the AI call if `x_last_ai_query == payload_hash` (cache reuse), otherwise calls `custom.ai._recommend` and writes `x_ai_suggested_text` + cache key. The JS asset `ai_reply_clipboard.js` provides "Insert into Reply".
- The agent clicks "Escalate to Helpdesk" → `action_escalate_to_helpdesk()` builds an HTML transcript from the last 50 messages, picks the non-internal partner from `channel_partner_ids`, maps `x_helpdesk_priority` (`low/normal/high/urgent`) → ticket priority `0/1/2/3`, creates a `helpdesk.ticket`, links both ways (`x_helpdesk_ticket_id`, `x_escalated_to_helpdesk=True`), posts notes on both records, and returns an `act_window` opening the ticket. Idempotent: already-escalated channels just reopen the existing ticket.
- "Request Rating" → `action_request_visitor_rating()` flips `x_rating_requested=True` and posts a prompt. `submit_visitor_rating(channel_id, rating, feedback)` validates `rating ∈ {1..5}` and writes `x_rating` + `x_rating_feedback`.

## Key Models
- `discuss.channel` (inherited) — Adds escalation, AI suggest, routing override, satisfaction rating fields.
- `custom.livechat.canned.response` — Shortcut → HTML body lookup (`mail.thread`), unique shortcut.
- `custom.livechat.chatbot.script` — Script header, link to `im_livechat.channel`, `is_active`, `step_ids`.
- `custom.livechat.chatbot.step` — Ordered step (text/question/forward_to_operator/end) with regex `expected_answers` and `next_step_default`.
- `im_livechat.channel` (inherited) — Adds `x_skill_tags` (comma-separated) consumed by routing.

## Important Fields
- `discuss.channel.x_helpdesk_ticket_id` (M2o `helpdesk.ticket`) — escalation link.
- `discuss.channel.x_helpdesk_priority` (Selection: low/normal/high/urgent, default normal) — maps to ticket priority 0..3.
- `discuss.channel.x_escalated_to_helpdesk` (Boolean) — idempotency latch.
- `discuss.channel.x_ai_suggested_text` (Text) / `x_last_ai_query` (Char) — last AI suggestion + sha1 payload hash for caching.
- `discuss.channel.x_rating` (Selection 1..5) / `x_rating_feedback` (Text) / `x_rating_requested` (Boolean).
- `custom.livechat.canned.response.shortcut` (Char, required, unique, min length 2, no spaces).
- `custom.livechat.canned.response.times_used` (Integer, telemetry).
- `custom.livechat.chatbot.step.step_type` (Selection) — drives `get_next_step` dispatch.
- `custom.livechat.chatbot.step.expected_answers` (Char) — comma-separated regex patterns, case-insensitive.
- `im_livechat.channel.x_skill_tags` (Char) — comma-separated lowercase keywords for skill routing.

## Public Methods
- `discuss.channel.action_escalate_to_helpdesk()` — creates linked `helpdesk.ticket`, returns `act_window`.
- `discuss.channel.action_ai_suggest_reply()` — AI bridge call with hash caching.
- `discuss.channel.action_request_visitor_rating()` — prompts visitor.
- `discuss.channel.submit_visitor_rating(channel_id, rating, feedback)` (`@api.model`) — records rating.
- `discuss.channel._custom_livechat_pick_operator(query_text)` — skill / round-robin selector.
- `discuss.channel.message_post(**kwargs)` — overridden: triggers routing on first message.
- `custom.livechat.canned.response.expand_canned(shortcut)` (`@api.model`) — JS-facing shortcut expander.
- `custom.livechat.chatbot.step.get_next_step(current_id, user_msg)` (`@api.model`) — chatbot state machine.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_ai_bridge`, `custom_helpdesk`, `im_livechat`.
- **Inherits from:** `discuss.channel`, `im_livechat.channel` (CE).
- **Extended by:** none declared.
- **External calls:** `custom.ai._recommend`. Channel = WebSocket via `im_livechat` (unchanged).
- **Cross-vertical:** generic — sits above the helpdesk module specifically.

## Gotchas
- **Skill matching is keyword-substring on visitor body**, not embedding/intent — false positives if a tag word appears casually ("billing" mentioned but not the topic).
- **Round-robin uses count of open channels per operator** — relies on `livechat_end_dt` field existing on `discuss.channel`; falls back to 0 when missing, breaking round-robin (all operators tie and the first by `id` always wins).
- **`message_post` override swallows ALL exceptions in routing** (`_logger.debug`) — silent routing failures are common; check debug log when channels don't get operators assigned.
- **AI suggest cache key is sha1 of the FULL payload (last 10 messages)** — any new message busts the cache; "cached reply reused" is only triggered when the agent clicks twice with zero new messages in between.
- **Escalation transcript fetches at most 50 messages** (`_custom_livechat_recent_messages`) — long chats lose history.
- **Channel = live chat (im_livechat WebSocket).** This module does NOT add WhatsApp/Telegram/Messenger channels — those would need separate adapters. BRD analyzer should not propose new chat channels here.
- **Chatbot regex falls back to literal substring match** on `re.error` — typos in patterns silently degrade behaviour.
- **`x_helpdesk_priority` selection uses string keys (`low/normal/high/urgent`)** but `helpdesk.ticket.priority` uses `"0".."3"`. The hard-coded `priority_map` in `action_escalate_to_helpdesk` is the only translation point.
- **Visitor rating only records, doesn't aggregate or report** — no SLA/CSAT dashboard.

## Out of Scope
- Adding new chat channels (WhatsApp / Messenger / Telegram).
- Multi-language chatbot scripts (single `message` per step).
- Co-browsing / screen-share / file transfer beyond CE.
- Automated transfer to another agent mid-conversation.
- Conversation analytics / sentiment scoring.
