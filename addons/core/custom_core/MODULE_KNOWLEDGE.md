---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_core
manifest_version: 19.0.0.2.0
---

# custom_core

## Purpose
Foundational shared module for the Custom Odoo 19 Platform. Carries no business logic of its own — it provides cross-cutting primitives that every other custom module reuses: HMAC signing helpers, Fernet-encrypted `ir.config_parameter` storage, an OS-level `secure_endpoint` controller decorator (HMAC + CIDR allowlist + timestamp drift + nonce replay protection with optional Redis), a marker mixin enforcing the `x_custom_` field-prefix convention, and the "Settings > Custom Platform" anchor menu.

## Business Flow
- A downstream module signs an outbound request: `header, ts = self.env["custom.security"].sign_payload(body_bytes)` → `{header} = "t=<unix_ts>,v1=<hex_hmac_sha256>"`. Used by `custom_ai_bridge`, `custom_adapter_framework` (its own copy), `custom_super_admin.orchestrator_client`.
- A downstream module stores a secret encrypted at-rest: `self.env["custom.ir.config"].set_encrypted("my.module.api_key", plaintext)` → row in `ir.config_parameter` with value prefixed `ENC::<fernet_token>`. Reading with `get_encrypted(key)` transparently decrypts. Master key from `CORETAX_SERTEL_MASTER_KEY` env (accepts 44-char Fernet, 64-char hex, or any string padded to 32 bytes).
- A downstream controller defends an endpoint: decorator `@secure_endpoint("scope_name")` → checks `X-Forwarded-For` / `remote_addr` against `custom_core.secure_endpoint.<scope>.allowed_cidrs`, verifies `X-Signature` HMAC-SHA256 against `custom_core.secure_endpoint.<scope>.secret`, requires `X-Timestamp` within ±300s, replay-protects via `_NonceStore` (process-local dict + optional Redis when `redis_url` configured). Every accept/reject is logged to `custom.adapter.call.log` (if available).

## Key Models
- `custom.security` — AbstractModel; HMAC signing + verification helpers. Reads secrets from env vars (`GATEWAY_SHARED_SECRET`, `ORCHESTRATOR_SHARED_SECRET`, generic via `sign_for(secret_key, body)`).
- `custom.ir.config` — AbstractModel; Fernet wrapper over `ir.config_parameter` for encrypted-at-rest secrets. Prefix marker `ENC::` distinguishes encrypted rows.
- `custom.mixin.platform` — AbstractModel; marker mixin asserting the `x_custom_` field-prefix convention via `_custom_validate_field_prefix`.
- `res.config.settings` (inherited) — anchor for the "Custom Platform" settings page (read-only label only; downstream modules attach their toggles here).

## Important Fields
None — all three custom models are `AbstractModel`. Only `res.config.settings.custom_platform_label` (Char, readonly, default text) exists as a settings-page anchor.

## Public Methods
- `custom.security.sign_payload(body: bytes) -> (header_str, ts_int)` — uses `GATEWAY_SHARED_SECRET`.
- `custom.security.sign_for(secret_key_env_name: str, body: bytes) -> (header, ts)` — generic signer for any env-held secret.
- `custom.security.verify_signature(header, body, max_skew=300) -> bool` — verifies header format `t=<ts>,v1=<hex>`, checks skew, constant-time compare.
- `custom.security._gateway_secret()` / `_orchestrator_secret()` — env reads that raise `RuntimeError` if `"changeme"` substring is detected.
- `custom.ir.config.set_encrypted(key, plaintext)` — Fernet-encrypt + persist with `ENC::` prefix.
- `custom.ir.config.get_encrypted(key, default=None)` — auto-decrypt if value starts with `ENC::`, else passthrough.
- `custom.ir.config.encrypt_value(plaintext)` (`@api.model`) — Fernet-encrypt an arbitrary string → `ENC::<token>` using the same master key; reusable field-level crypto (e.g. PII at rest), not tied to `ir.config_parameter`. Returns `False` for empty input; raises `UserError` if the `cryptography` lib is absent.
- `custom.ir.config.decrypt_value(token)` (`@api.model`) — reverse of `encrypt_value`; pass-through for empty / non-`ENC::` values (so un-migrated plaintext still reads), returns `None` on tamper/`InvalidToken`/key failure (fail-closed, logged).
- `custom.mixin.platform._custom_validate_field_prefix(field_name)` — boolean check `field_name.startswith("x_custom_")`.
- `controllers.secure_endpoint.secure_endpoint(scope_name)` — controller-route decorator; not on a model, imported as `from odoo.addons.custom_core.controllers.secure_endpoint import secure_endpoint`.

## Integration Points
- **Depends on:** `base`, `web`, `mail`.
- **Inherits from:** `res.config.settings` (label anchor only).
- **Extended by:** every other custom module (declares `custom_core` in its `depends`).
- **External calls:** none. Pure local primitives.
- **Cross-vertical:** generic — universal substrate.
- **Env vars consumed:** `GATEWAY_SHARED_SECRET`, `ORCHESTRATOR_SHARED_SECRET`, `CORETAX_SERTEL_MASTER_KEY`, plus any secret-key env name passed to `sign_for()`. Optional `redis_url` / `custom_core_redis_url` in `odoo.conf` for nonce store.

## Gotchas
- **Secrets must be in env, not in DB.** `_gateway_secret` raises `RuntimeError` if the env var is missing or contains the substring `"changeme"` — deliberate to fail-loud on stock dev configs.
- **`CORETAX_SERTEL_MASTER_KEY` is reused as the platform-wide Fernet key**, despite the misleading name. Rotate per `security/policies/secret-rotation.md`.
- **The `_PREFIX = "ENC::"` marker is a magic string.** A plaintext value that happens to start with `ENC::` will be misinterpreted as encrypted on read.
- **Nonce store falls back to a process-local `dict`** if Redis is not configured — replay protection breaks across multiple Odoo workers without Redis.
- **`@secure_endpoint` writes audit rows to `custom.adapter.call.log`** if the model exists, but does NOT require `custom_adapter_framework` as a depend. Audit-log writes happen under `env(su=True)` and silently swallow exceptions.
- **No PDP audit integration** — `custom_core` is below `custom_pdp_audit` in the stack and cannot reference it.
- **`_NONCE_TTL_S = 600` and `_TS_DRIFT_MAX_S = 300`** are module-level constants, not config parameters.
- **`res_groups_privilege_custom_platform` is for custom_core's own two groups only** — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form. When sixteen modules all reused this privilege, saving a user kept one custom group and silently dropped the rest; that is how `custom_accounting_reports.group_report_user` reached 0 members on a production tenant and took the whole Accounting Reports menu down with it. Give a new module its own `res.groups.privilege` and point it at `module_category_custom_platform` — that category, not the privilege, is what keeps the selectors grouped together on the form.

## Out of Scope
- **Multi-tenant key rotation** — single master key; rotating it invalidates all previously encrypted parameters.
- **Audit logging of `custom.security` calls** — sign/verify do not log; only `secure_endpoint`'s controller layer logs.
- **Group/permission scaffolding** — only `security/custom_security.xml` for ir.model.access; no platform-wide groups beyond what each downstream module declares.
- **The "Custom Platform" settings page only renders the anchor label.** All actual toggles come from downstream modules.
