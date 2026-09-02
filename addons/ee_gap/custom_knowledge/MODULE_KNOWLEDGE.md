---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_knowledge
manifest_version: 19.0.0.2.0
---

# custom_knowledge

## Purpose
A CE-targeted reimplementation of Odoo Knowledge — an internal wiki / knowledge base. Articles (`knowledge.article`) are hierarchical (parent/child), rich-text (sanitized HTML), tagged (`knowledge.tag`), owner-attributed, and optionally restricted to specific `res.groups`. PostgreSQL `to_tsvector` + GIN index (built in `post_init_hook`) powers full-text search. Articles can be shared externally via token-protected `/knowledge/share/<token>`, started from reusable templates (`knowledge.article.template`), favourited per user, and snapshot-versioned on every body change.

This is the **canonical knowledge / wiki / SOP / runbook surface** for the platform. BRD analyzers should map "knowledge base / wiki / internal docs / runbook / SOP repository" requirements here.

## Business Flow
- User creates a `knowledge.article` with a name, optional parent, rich-text body, tags, optional `read_group_ids` restriction. On create, if `is_shared_externally=True` and no token, one is minted via `secrets.token_urlsafe(32)`.
- On any `write()` where `body` changes, the **previous** body is snapshotted into a new `knowledge.article.version` row with `version_no = current_count + 1` and `saved_by = uid` — append-only history.
- `action_apply_template(template_id)` overwrites `body` with `knowledge.article.template.body_template` (seeded categories: meeting_notes, project_brief, sop, runbook, onboarding).
- `action_toggle_favorite()` adds/removes the calling user from `favorite_user_ids`. The `is_favorite` computed boolean and `_search_is_favorite` enable a "My Favorites" filter.
- `action_generate_share_link()` rotates `share_token` + sets `is_shared_externally=True`, then shows a sticky notification with the full URL.
- `action_revoke_share_link()` clears the flag and token.
- Public route `GET /knowledge/share/<token>` (controller `KnowledgePortalController.share_article`) requires token length ≥ 16, `is_shared_externally=True`, and `share_token` match; renders `custom_knowledge.portal_share_article`.
- JSON endpoint `POST /knowledge/search` (auth=user) calls `search_articles(query, limit)` which uses `to_tsvector('english', name||' '||body) @@ plainto_tsquery(...)` with `ts_rank` ordering and `ts_headline` snippets, filtered to record-rule-accessible IDs. Fallback to `ilike` on `name` if pgsql refuses the tsquery.
- `_get_access_action` is overridden so mail-notification links to an externally-shared article route to the public share URL when `force_website=True`.
- Dynamic Properties: each article carries a `Properties` bag whose definition is inherited from `parent_id.property_definitions`, enabling per-subtree custom fields without `ir.model.fields` records.

## Key Models
- `knowledge.article` — Main wiki node; inherits `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin`.
- `knowledge.article.version` — Immutable append-only snapshot of a previous body.
- `knowledge.article.template` — Reusable starting body keyed by category (meeting_notes/project_brief/sop/runbook/onboarding/other).
- `knowledge.tag` — Free-form tag dictionary.

## Important Fields
- `knowledge.article.parent_id` (M2o self, ondelete=cascade, indexed) — hierarchy.
- `knowledge.article.body` (Html, sanitized, translate=True) — main content; change triggers versioning.
- `knowledge.article.search_vector` (Char, computed-stored) — denormalised tag-stripped concat of name+body, capped 8 000 chars; the real GIN index is on `to_tsvector` of name+body (built in `post_init_hook`).
- `knowledge.article.is_published` (Boolean, tracked) — visibility flag for internal users.
- `knowledge.article.read_group_ids` (M2m res.groups) — optional read restriction; empty = all Knowledge users.
- `knowledge.article.share_token` (Char, indexed, copy=False) — external share secret.
- `knowledge.article.is_shared_externally` (Boolean, tracked) — gates the public route.
- `knowledge.article.favorite_user_ids` (M2m res.users) — per-user pinning storage.
- `knowledge.article.is_favorite` (Boolean, computed, searchable, non-stored) — UI helper bound to env.uid.
- `knowledge.article.properties` / `property_definitions` (Properties / PropertiesDefinition) — Odoo 19 dynamic field bag; definition lives on the parent article.
- `knowledge.article.version_ids` (One2many) / `version_count` (Integer, computed).
- `knowledge.article.template.body_template` (Html, sanitized, translate=True).
- `knowledge.article.template.category` (Selection) — taxonomy for the template picker.

## Public Methods
- `knowledge.article.action_toggle_favorite()` — per-user favourite toggle.
- `knowledge.article.action_generate_share_link()` — rotate token + flag external.
- `knowledge.article.action_revoke_share_link()` — clear both.
- `knowledge.article.action_apply_template(template_id=None)` — clone template body.
- `knowledge.article.action_view_versions()` — open version list filtered to this article.
- `knowledge.article.search_articles(query, limit=20)` (`@api.model`) — ts_rank + ts_headline FTS, ilike fallback.
- `knowledge.article._compute_search_vector()` — strips HTML tags into denormalised char.
- `knowledge.article._get_access_action()` — overridden to route external-share links to portal URL.
- Controllers: `GET /knowledge/share/<token>` (public), `POST /knowledge/search` (auth=user JSON).
- `post_init_hook` in `hooks.py` — creates the `to_tsvector` GIN index after install.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `custom_documents`, `mail`, `portal`.
- **Inherits from:** `mail.thread`, `mail.activity.mixin`, `pdp.audited.mixin` on article.
- **Extended by:** verticals may seed `knowledge.article.template` rows via XML data (the module already ships a seed file: `data/knowledge_article_template_seed.xml`).
- **External calls:** none.
- **Cross-vertical:** generic knowledge surface. Pairs with `custom_documents` (which holds binary files) — articles for free-form text, documents for attachments.

## Gotchas
- **Versioning snapshots the OLD body, not the new** — restoring "version 3" gives you the body that existed before version 3 was written. Indexing is `version_no = (count at time of write) + 1`, monotonic per article.
- **`search_articles` runs a raw `cr.execute`** with `to_tsvector('english', ...)` — Indonesian content tokenises poorly under the `english` config. Consider switching to `'simple'` or `'indonesian'` if/when content is primarily ID — currently hardcoded.
- **`accessible = self.search([]).ids` is loaded into memory** before the SQL query and passed as `id = ANY(%s)` — for very large knowledge bases this is O(N) memory.
- **`_get_access_action` only short-circuits when `force_website=True`** — internal user notification clicks still flow through the normal Odoo action; only website-context links go to the share URL.
- **`is_shared_externally` is a simple boolean** — no per-link expiry, no view-count limit, no IP allow-list.
- **Token length check is `len(token) < 16`** in the controller — anyone crafting a 16+ char arbitrary string can probe; the secret strength still lives in `secrets.token_urlsafe(32)` ≈ 256 bits.
- **`read_group_ids` is implemented as a M2m field** but the ACL resolution lives in `security/record_rules.xml`; record-rule edits require careful testing.
- **Properties definition lives on `parent_id`** — root articles cannot define their own properties unless they are themselves children. The seed template hierarchy must account for this.
- **`_compute_display_name` is `recursive=True`** — deep hierarchies will read all ancestors' display names; cap nesting in practice.

## Out of Scope
- **Real-time collaborative editing / cursors / OT-CRDT** — out of scope.
- **Inline comments / annotations on article body** — only chatter at the bottom.
- **Inter-article references / backlinks graph** — not auto-extracted.
- **Public-facing publishing as a website** — only single-article share-by-token public render.
- **Multi-language body translations beyond `translate=True`** — relies on Odoo's standard translation pipeline.
- **AI-driven summarisation / chat-with-article** — not integrated here; `custom_ai_features` is the AI surface.
- **External wiki sync (Confluence, Notion, GitHub wiki)** — not implemented.
