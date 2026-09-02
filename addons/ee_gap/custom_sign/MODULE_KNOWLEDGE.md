---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_sign
manifest_version: 19.0.0.1.0
---

# custom_sign

## Purpose
A lightweight e-signature workflow for the platform. A `sign.template` wraps a reusable PDF (`ir.attachment`); a `sign.request` bundles that template with an ordered list of `sign.request.signer` rows. On send, every signer gets a unique tokenised public portal URL (`/sign/<token>`) where they view the document and submit either a drawn signature (canvas data URL → base64 PNG) or a typed-name fallback. Aggregated state (`partially_signed` → `signed`) is recomputed on every signer submission, with PDP audit + `mail.thread` tracking throughout.

This is the **canonical e-signature module** for the platform. Anything in a BRD that mentions "electronic signature / DocuSign-like / multi-signer routing / signature collection" should map here.

## Business Flow
- Admin uploads a PDF, creates a `sign.template` pointing at its `ir.attachment`.
- User creates a `sign.request` (default state `draft`, name from `ir.sequence` `sign.request`, fallback `SIGN-???`), picks the template, and adds `sign.request.signer` rows with `name`/`email`/optional `role`/`sequence`/optional `partner_id`.
- `action_send()` guards `state == 'draft'` and `signer_ids` non-empty, then mints a `secrets.token_urlsafe(32)` `access_token` for each signer missing one, flips request state to `sent`, stamps `sent_at`, audits `sign_request_sent`.
- Each signer receives a `/sign/<access_token>` URL (email/notification mechanism is out of scope here — only token generation lives in this module). When the URL is opened, `SignPortal.sign_open()` looks up the signer, calls `mark_opened(ip, ua)` which transitions `waiting`→`opened` with IP + user-agent capture, and renders `custom_sign.sign_page`.
- Signer POSTs to `/sign/<token>/submit` with `signature_data` (data:image base64 URL) and/or `signature_text`. The controller decodes the data URL, calls `submit_signature(signature_data, signature_text)`. That method enforces "not already signed/declined", "at least one of drawn or typed provided", writes signature + signed_at, audits `sign_signer_signed`, then calls `request._refresh_state()`.
- `_refresh_state()` recomputes: all-signed → `signed` + stamp `completed_at` + audit `sign_request_complete`; any-signed-but-not-all → `partially_signed`.
- `decline(reason)` flips the signer to `declined` and posts a chatter note on the request (does not change request state on its own).
- `action_cancel()` is allowed from any non-`signed` state and audits.

## Key Models
- `sign.template` — Reusable PDF + label; bound to one `ir.attachment`.
- `sign.request` — One signature collection round; owns ordered signer list and aggregated state. Inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- `sign.request.signer` — One row per addressee; carries token, state, IP/UA, signature blob/text. Inherits `mail.thread`, `mail.activity.mixin`.

## Important Fields
- `sign.request.name` (Char) — `ir.sequence`-allocated identifier.
- `sign.request.state` (Selection draft/sent/partially_signed/signed/cancelled, tracked, indexed) — workflow.
- `sign.request.template_id` (M2o sign.template, required).
- `sign.request.attachment_id` (related from template, stored) — denormalised for direct attachment access.
- `sign.request.signer_ids` (One2many).
- `sign.request.signed_count` / `total_signers` (Integer, computed) — for UI progress.
- `sign.request.sent_at` / `completed_at` (Datetime, readonly).
- `sign.request.requested_by_id` (M2o res.users, required, defaults to env.user).
- `sign.request.signer.access_token` (Char, readonly, copy=False, indexed) — the only auth for the public portal.
- `sign.request.signer.state` (Selection waiting/opened/signed/declined, tracked).
- `sign.request.signer.signature_data` (Binary, attachment=True) — drawn signature as PNG bytes (base64-encoded after data-URL strip).
- `sign.request.signer.signature_text` (Char) — typed-name fallback.
- `sign.request.signer.ip_address` / `user_agent` (Char, readonly) — captured at `mark_opened`, immutable thereafter.
- `sign.request.signer.opened_at` / `signed_at` (Datetime, readonly).

## Public Methods
- `sign.request.action_send()` — guarded draft→sent + token mint + audit.
- `sign.request.action_cancel()` — any state except `signed` → `cancelled`.
- `sign.request._refresh_state()` — internal aggregator called from signer submission.
- `sign.request.signer.mark_opened(ip, ua)` — waiting→opened, captures IP/UA.
- `sign.request.signer.submit_signature(signature_data, signature_text)` — main signing entry; audits on the parent request.
- `sign.request.signer.decline(reason)` — declines a signer.
- Controllers (public, csrf=True on submit): `GET /sign/<token>`, `POST /sign/<token>/submit`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `mail`, `portal`, `website`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` (financial classification on request).
- **Extended by:** vertical modules typically link `sign.request` from their workflows (e.g. contract approved → create sign.request). The pattern is to call `env['sign.request'].create({...})` after your business flow reaches the signature gate.
- **External calls:** none — purely on-platform; no DocuSign/HelloSign/Adobe Sign integration.
- **Cross-vertical:** generic e-signature surface. `custom_documents` is **parallel**, not nested — sign templates point at `ir.attachment` directly, not at `document.document` rows. If you need DMS+sign linkage, store the `document.document.id` in your business record alongside the `sign.request.id`.

## Gotchas
- **`/sign/<token>/submit` controller has CSRF enabled (`csrf=True`)** — public-route + CSRF means the form must include a CSRF token. Make sure the rendered `sign_page` template uses `<t t-call="web.csrf_token"/>` or the equivalent.
- **`access_token` is the only authentication** — anyone with the URL can sign. No email-verify, no OTP, no IP allow-list. IP/UA are recorded but not enforced.
- **Signature drawing is base64-of-base64**: the controller does `base64.b64encode(base64.b64decode(data_url_tail))` — net effect is re-encoding the bytes, which is wasted CPU but functionally identical. The stored blob is base64 PNG bytes.
- **`sign.request._pdp_audit_classification()` returns `"financial"` hardcoded** — every sign request audits as financial regardless of underlying document content.
- **`_refresh_state` is called only from `submit_signature`** — declining a signer does NOT advance request state; if the request has 3 signers and one declines, the request stays in `sent`/`partially_signed` indefinitely unless an operator cancels.
- **No reminder cron** — once sent, signers are never nudged. Email delivery itself is left to whichever module sends the URL.
- **`attachment_id` on the template is `ondelete="restrict"`** — you cannot delete a PDF that's referenced by a template.
- **No signature placement / coordinates / form fields on the PDF** — the signature is collected separately and stored as a blob; the PDF is **not** rebuilt with the signature embedded. Downstream rendering is out of scope.
- **No bulk send / templates with placeholders** — one request = one PDF = one set of named signers.

## Out of Scope
- **PDF re-rendering with embedded signature images** — store-only; no compositing.
- **Field-level placement (initial here, date there)** — flat signature capture only.
- **Cryptographic signing (PKCS, X.509, eIDAS, PrivyID, etc.)** — typed/drawn signatures only; not legally-qualified e-signatures.
- **Audit trail PDF** — no separate certificate of completion document is generated.
- **Reminder emails / SLA tracking on signature aging** — not implemented.
- **External e-signature provider integration** — no DocuSign/HelloSign/PrivyID adapters.
- **Document-management linkage** — `custom_documents` is independent.
