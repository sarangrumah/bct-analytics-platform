---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_pdp_consent
manifest_version: 19.0.0.2.0
---

# custom_pdp_consent

## Purpose
Implements **subject-consent capture and lifecycle** for UU 27/2022 (Indonesian PDP Law). Holds a master taxonomy of consent purposes (`pdp.consent.purpose`) and an append-style log of individual recorded consents (`pdp.consent`) per partner, with computed `active`/`expired`/`withdrawn` state, evidence attachment, version, and an audited withdrawal action.

Provides a customer-facing portal (`/my/consents`) so data subjects can view and withdraw their own consents, and an `@api.model` `check_consent(partner, purpose_code)` API that any downstream module can call before processing PII.

## Business Flow
- Admin/DPO defines `pdp.consent.purpose` rows (code, name, `requires_renewal_days`) — seeded from `data/pdp_consent_purpose_data.xml`.
- A consent is recorded by creating a `pdp.consent` (partner_id + purpose_id, optional evidence binary + `evidence_filename`, `version` defaulted to `"1.0"`). On `create()` an audit row `consent_grant` is pushed to `pdp.audit_log`.
- `_compute_expires_at` derives `expires_at = given_at + requires_renewal_days`; `_compute_state` resolves to `active`/`expired`/`withdrawn` reactively.
- Exclusion constraint `_partner_purpose_unique_active` (PostgreSQL `EXCLUDE` with `WHERE withdrawn_at IS NULL`) blocks a second un-withdrawn consent for the same `(partner_id, purpose_id)`.
- Subject (or operator) calls `action_withdraw(reason)` → stamps `withdrawn_at = now()`, writes audit row `consent_withdraw`. Withdrawn rows stay for evidence; they can be superseded by a fresh grant.
- Downstream callers gate processing via `self.env["pdp.consent"].check_consent(partner, "marketing_email")` — returns `True` only if an un-withdrawn, un-expired record exists.
- Portal `/my/consents` lists all consents for `request.env.user.partner_id`; POST to `/my/consents/<id>/withdraw` runs `action_withdraw` (CSRF on, partner-ownership check).

## Key Models
- `pdp.consent.purpose` — Catalog of consent purposes (code, name, `requires_renewal_days`, active, sequence). `code` is globally unique.
- `pdp.consent` — Subject consent record: partner × purpose × given_at, with computed state, evidence binary, audited via `pdp.audited.mixin`.

## Important Fields
- `pdp.consent.partner_id` (M2o `res.partner`, required, `ondelete="cascade"`) — data subject; deleting the partner cascades the consent rows.
- `pdp.consent.purpose_id` (M2o `pdp.consent.purpose`, required, `ondelete="restrict"`) — purpose cannot be deleted while consents reference it.
- `pdp.consent.purpose_code` (Char, `related="purpose_id.code"`, stored) — denormalised for `check_consent` lookups by code.
- `pdp.consent.given_at` (Datetime, default=now, required) — moment of grant.
- `pdp.consent.expires_at` (Datetime, computed/stored from `given_at` + `purpose_id.requires_renewal_days`) — False means no expiry.
- `pdp.consent.withdrawn_at` (Datetime) — set by `action_withdraw`; presence flips state to `withdrawn`.
- `pdp.consent.state` (Selection: active/expired/withdrawn, computed/stored) — derived; not user-writeable.
- `pdp.consent.evidence` (Binary `attachment=True`) + `evidence_filename` (Char) — signed form/screenshot.
- `pdp.consent.version` (Char, default `"1.0"`) — version of the consent text/notice presented to the subject.
- `pdp.consent.purpose.requires_renewal_days` (Integer) — `>0` triggers `expires_at` computation; `0` = perpetual.

## Public Methods
- `pdp.consent.check_consent(partner, purpose_code)` (`@api.model`) — returns `True` iff an un-withdrawn, un-expired consent matches. Accepts a recordset or raw int.
- `pdp.consent.action_withdraw(reason=None)` — idempotent (skips already-withdrawn); stamps `withdrawn_at`, writes `consent_withdraw` audit row with reason.
- `pdp.consent.create()` (overridden) — adds `consent_grant` audit row per created record.
- Portal: `/my/consents` (GET, auth=user), `/my/consents/<int:consent_id>/withdraw` (POST, csrf=True).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `portal`.
- **Inherits from:** `pdp.audited.mixin` (on `pdp.consent`), `portal.CustomerPortal` (controller).
- **Extended by:** none declared; vertical modules call `check_consent` rather than subclassing.
- **External calls:** none.
- **Cross-vertical:** generic — any tenant with PII processing under UU 27/2022.

## Gotchas
- `check_consent` uses `sudo()` — it bypasses record rules. Callers must not pass an untrusted partner id.
- The `EXCLUDE` constraint is PostgreSQL-specific and requires the `btree_gist` extension (provided by `custom_pdp_audit`'s pre-init hook via `btree_gin`/standard extensions). If running on a fresh DB without it, install will fail with "operator class btree_gist does not exist".
- `version` is a free-text Char with no enforced changelog; treating it as semver is convention only.
- `evidence` is `attachment=True` so it lives in filestore, not the DB column — backup strategy must cover both.
- Portal withdrawal silently redirects to `/my/consents` on auth/ownership failure (no 403); reads like "nothing happened" to the caller.
- No bulk-grant / migration tool: every consent must be created one record at a time.
- **This module's groups sit on its own `custom_pdp_consent.res_groups_privilege_pdp_consent` privilege** ("PDP Consent") — Odoo 19 renders every group sharing one `res.groups.privilege` as a single pick-one dropdown on the user form, so a privilege shared across modules makes saving a user silently drop the other modules' groups. The privilege carries `custom_core.module_category_custom_platform`, so the selector still appears alongside the other custom modules. Do not point new groups at `custom_core.res_groups_privilege_custom_platform`.

## Out of Scope
- **Consent collection UI / notice rendering** — this module assumes evidence is captured elsewhere and uploaded as a binary.
- **Re-consent campaigns / mass renewal cron** — expiry is computed but no automation chases subjects to renew.
- **Cross-tenant consent portability** — consents are tied to the tenant DB; no export/import shape.
- **Granular field-level consent** — consent is at the `purpose` level, not per-field. Use `custom_pdp_masking` for field-level controls.
- **Legal basis other than consent** (legitimate interest, contract, legal obligation) — not modelled; only "consent" basis is recorded here.
