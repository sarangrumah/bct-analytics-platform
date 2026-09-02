# Custom AI Bridge

Connects Odoo to the platform AI gateway (`ai-gateway` service) which
abstracts Claude / OpenAI / Ollama. All requests are HMAC-signed using the
shared secret defined in `GATEWAY_SHARED_SECRET`.

## Models / Services

- `custom.ai` — service exposing `chat(messages, **opts)` and
  `recommend(model, res_id, payload)`. Signs every request with HMAC
  (`X-Custom-Signature` header), timestamps it (replay defense, 5-min
  window), and forwards through the configured AI provider.
- `res.config.settings` — extension exposing provider override, default
  model, quality tier (`standard` / `high`), per-feature on-off toggles
  under Settings → Custom Platform → AI Intelligence.

## Wizards

- "Ask AI" wizard (`wizards/ai_recommend_wizard_views.xml`) — invoke from
  any record's action menu to get a structured recommendation.

## Security Groups

- Uses settings group from `custom_core`.

## Dependencies

- `custom_core`
- Python: `httpx`

## Install

Install after `custom_core`. Configure provider + API key under Settings →
Custom Platform → AI Intelligence.

## Reference

- AI Gateway service: `ai-gateway/app/`
- Architecture: `docs/architecture.md` (AI layer)
