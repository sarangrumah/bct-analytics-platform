# Custom PDP Consent

Purpose-based consent management — every marketing email, SMS, or
profile-sharing operation must reference a granted consent.

## Models

- `pdp.consent.purpose` — catalogue of purposes (marketing, analytics,
  third-party share, etc.) with a versioned privacy-notice text.
- `pdp.consent` — per-partner consent record: `partner_id`, `purpose_id`,
  `given_at`, `expires_at`, `withdrawn_at`, `evidence` (attached file or
  link), `version` (notice version at grant time).
- `_require_consent(partner, purpose_code)` helper — modules that send
  outbound communication call this before dispatch. Raises if no active
  consent exists.

## Portal

`views/portal_templates.xml` adds a portal page where the subject can
view all their consents and withdraw any of them. Withdrawal is audited
to `pdp.audit_log`.

## Security Groups

- `pdp.group_dpo` — manage consent purposes and view all consents.
- Portal users see their own consents only (record rules).

## Dependencies

- `custom_core`, `custom_pdp_taxonomy`, `custom_pdp_audit`, `portal`

## Install

Install after `custom_pdp_audit`. Default purposes seeded from
`data/pdp_consent_purpose_data.xml`.

## Reference

- `docs/pdp-compliance.md`
