---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_documents
manifest_version: 19.0.0.1.0
---

# custom_documents

## Purpose
A lightweight workspace-organised Document Management System (DMS) for the platform. Each `document.document` lives in a `document.workspace` (hierarchical, member-gated), wraps an `ir.attachment` (the real file storage), and carries metadata: tags (`document.tag`), PDP classification (`pdp.classification`, default inherited from workspace), description, owner, lifecycle state (draft/published/archived), token-protected share link with expiry, and immutable version history (`document.version`).

Every CRUD-side action writes a PDP audit row, and `_pdp_audit_classification()` is overridden so each document carries its own classification code into the audit stream. This is the **canonical DMS module** — other modules that need file storage with versioning + classification should depend here.

## Business Flow
- Admin defines a `document.workspace` (code unique, optional parent, members list, `default_classification_id`).
- User creates a `document.document` with a name, target workspace, and an `attachment_id` (the uploaded file). On `create()`:
  - If `classification_id` is not set, it is auto-populated from `workspace_id.default_classification_id` via `_compute_classification` (stored, non-readonly).
  - A `document.version` row with `version=1` and a "Initial version" comment is created.
- `action_upload_new_version(attachment_id, comment)`: looks up the latest version number, creates a new `document.version` with `version = latest+1`, swaps the document's `attachment_id` to point at the new file, audits `document_new_version`.
- `action_publish()` flips state draft→published + audit `document_publish`. `action_archive()` flips to archived + audit.
- `action_generate_share_link()` mints `share_token` (`secrets.token_urlsafe(32)`), sets `share_expires_at = now + 7 days`, audits.
- `action_revoke_share()` clears token + expiry, audits.
- All versions are immutable: `document.version.write` raises `UserError` unless `document_version_internal` context flag is set; `unlink` is unconditionally forbidden.

## Key Models
- `document.workspace` — Hierarchical container; carries default classification + member ACL.
- `document.document` — A logical document (one current file + N historical versions); inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- `document.version` — Append-only history row pointing at a snapshot `ir.attachment`.
- `document.tag` — Free-form tag dictionary (Many2many on `document.document`).

## Important Fields
- `document.document.workspace_id` (M2o document.workspace, required, indexed) — primary scoping.
- `document.document.attachment_id` (M2o ir.attachment, required, ondelete=cascade, copy=False) — current file pointer.
- `document.document.classification_id` (M2o pdp.classification, computed-stored, non-readonly) — falls back to workspace default; writable for override.
- `document.document.filename` / `mimetype` / `file_size` (related from attachment).
- `document.document.state` (Selection draft/published/archived, tracked) — lifecycle.
- `document.document.share_token` (Char, readonly, copy=False) — share URL secret.
- `document.document.share_expires_at` (Datetime) — defaults to +7 days from generation.
- `document.document.owner_id` (M2o res.users, required, defaults to env.user).
- `document.document.tag_ids` (M2m document.tag).
- `document.version.document_id` (M2o, ondelete=cascade, indexed).
- `document.version.attachment_id` (M2o ir.attachment, ondelete=restrict) — restrict prevents deleting an attachment still referenced by history.
- `document.version.version` (Integer, required) — monotonic per document; `(document_id, version)` unique.
- `document.workspace.code` (Char, unique, indexed) — stable external identifier.
- `document.workspace.member_ids` (M2m res.users) — workspace ACL.
- `document.workspace.default_classification_id` (M2o pdp.classification) — inherited by new docs.

## Public Methods
- `document.document.action_publish()` / `action_archive()` — lifecycle transitions with audit.
- `document.document.action_generate_share_link()` / `action_revoke_share()` — token management.
- `document.document.action_upload_new_version(attachment_id, comment)` — version append + swap current.
- `document.document._compute_classification()` — workspace-default cascade.
- `document.document._compute_version_count()` — counter.
- `document.document._pdp_audit_classification()` — returns the doc's own classification code (or `internal`).

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_pdp_taxonomy`, `mail`, `portal`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` on `document.document`.
- **Extended by:** `custom_ai_features` overrides `document.document.create()` to auto-suggest classification + tags via AI; `custom_spreadsheet` depends on this for share-token plumbing; `custom_knowledge` depends for cross-linking.
- **External calls:** none.
- **Cross-vertical:** generic DMS surface. Any BRD requirement about "document repository / file storage / versioning / share link" should map here. `custom_sign` operates over `ir.attachment` directly (not `document.document`) — they're parallel surfaces, not nested.

## Gotchas
- **Share token has a 7-day expiry but no controller enforcement is in THIS module** — `share_expires_at` is set on generate but the public-route check belongs to whichever portal renders the URL. There is no controller file in `custom_documents`; downstream consumers must enforce expiry.
- **`classification_id` is computed but writable (`readonly=False`, stored)** — recomputation on `workspace_id` change will only overwrite if the field is currently empty. Moving a doc between workspaces does NOT auto-reclassify.
- **`attachment_id` is `ondelete=cascade`** — deleting an attachment outside the document workflow nukes the document. The current-file pointer is fragile; historical versions are protected via `ondelete=restrict`.
- **`document.version` is immutable but `version` number is supplied by the caller** — `action_upload_new_version` correctly computes `latest+1`, but a direct `Version.create` with `document_version_internal` context can fabricate any number.
- **`unlink` of versions is forbidden** — re-installing or re-creating documents with the same primary key range will leave orphaned versions after document delete cascade (versions cascade-delete via the document FK).
- **Workspace `member_ids` is declared but enforcement is via `security/security.xml`** — record rules must be checked when changing membership semantics.
- **No PDF / OCR / preview** — pure file blob storage.
- **`is_published`-style flag** is the `state` selection here, NOT a separate boolean.

## Out of Scope
- **OCR / full-text indexing of attachment content** — not implemented; AI auto-classify (from `custom_ai_features`) only reads plain-text excerpts.
- **Inline preview / annotations / collaborative editing** — out of scope.
- **Folder sharing as a unit** — only individual docs get share tokens; workspaces have no share endpoint.
- **WebDAV / S3 / external storage backends** — `ir.attachment` storage backend is whatever the host configures globally.
- **Quotas / retention policies** — no automatic cleanup of archived or expired-share docs.
- **E-signature inside the document** — `custom_sign` is a separate, parallel module operating on `ir.attachment` (sign templates).
