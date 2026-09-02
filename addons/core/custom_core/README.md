# Custom Core

Foundational module for the Custom Odoo 19 Platform. Every other `custom_*`
module depends on this one. Provides shared mixins, encrypted parameter
helpers, and the HMAC signing primitives used by the AI bridge and Coretax
adapter.

## Models / Services

- `custom.mixin.platform` — marker mixin enforcing the `x_custom_` prefix
  policy for fields added to core Odoo models (PDP-relevant relations).
- `custom.ir.config` — encrypt/decrypt `ir.config_parameter` values via
  Fernet. Master key comes from `CORETAX_SERTEL_MASTER_KEY` (re-used as the
  general platform master) or a per-tenant key in `ir.config_parameter`.
- `custom.security` — HMAC-SHA256 signer/verifier (used by
  `custom_ai_bridge` outbound calls and the abstract Coretax H2H adapter).

## Security Groups

Anchors the Settings UI group `custom_platform.group_platform_admin`
referenced by downstream modules.

## Dependencies

- `base`, `web`, `mail`

## Install

Install via Apps menu first — required by all other `custom_*` modules.

## Reference

- Architecture: `docs/architecture.md`
- "No-core-modify" principle: see Plan §"Prinsip Tidak Mengubah Core"
