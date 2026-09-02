---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_studio_lite
manifest_version: 19.0.0.1.0
---

# custom_studio_lite

## Purpose
A minimal CE-friendly substitute for Odoo Enterprise Studio's custom-field manager. Admins declare custom fields against any model through DB records (`studio.custom.field`) rather than editing source code; clicking **Apply** materialises an `ir.model.fields` row on the target model.

The scope is deliberately narrow: **fields only**, not view inserts (despite the manifest summary mentioning view extensions, the model does not implement any `ir.ui.view` creation). Use this module for quick vertical-specific column additions; for anything more invasive, fork a proper module.

## Business Flow
- Admin creates a `studio.custom.field` row: pick `model_id`, choose `field_type` from the supported list, set `name` (label) and `technical_name` (must match `^x_studio_[a-z0-9_]{1,60}$`), optionally `required` / `readonly` / `help_text`.
- For `selection` type, fill `selection_values` with one `key|label` line per option.
- Click `action_apply()` — the wizard creates (or updates if `ir_model_fields_id` already linked) the underlying `ir.model.fields` row via `sudo()`. State flips `draft` → `applied`; failures land in `error` with `last_error` populated. A PDP audit row is written for both success and failure paths.
- Once applied, the field is a real ORM column on the target model — visible to views, ORM, exports.

## Key Models
- `studio.custom.field` — Declarative descriptor for a single custom field; owns the lifecycle from draft to applied/error.

## Important Fields
- `studio.custom.field.technical_name` (Char, regex-validated) — must begin with `x_studio_`; uniqueness enforced per `(model_id, technical_name)`.
- `studio.custom.field.model_id` (M2o ir.model, ondelete=cascade) — target model; deletion of the model deletes the declaration.
- `studio.custom.field.model_name` (Char, related, stored) — denormalised technical model name for searching.
- `studio.custom.field.field_type` (Selection: char/text/integer/float/boolean/date/datetime/selection) — limited subset of Odoo field types.
- `studio.custom.field.selection_values` (Text) — newline-separated `key|label` pairs; parsed at apply time and serialised as `str(list[tuple])` into `ir.model.fields.selection`.
- `studio.custom.field.required` / `readonly` (Boolean) — propagated to the materialised field.
- `studio.custom.field.ir_model_fields_id` (M2o ir.model.fields, readonly, copy=False) — back-pointer to the materialised field; used to detect "update vs create" on re-apply.
- `studio.custom.field.state` (Selection draft/applied/error) — lifecycle marker.
- `studio.custom.field.last_error` (Text, readonly) — exception text from last failed apply.

## Public Methods
- `studio.custom.field.action_apply()` — main button; creates or updates the linked `ir.model.fields`, transitions state, writes PDP audit.
- `studio.custom.field._check_technical_name()` (`@api.constrains`) — enforces the `x_studio_*` regex.
- `studio.custom.field._pdp_audit_classification()` — returns `"internal"`.

## Integration Points
- **Depends on:** `custom_core`, `custom_pdp_audit`, `base`.
- **Inherits from:** `pdp.audited.mixin`.
- **Extended by:** none in tree. Verticals that need to add columns at runtime should create `studio.custom.field` rows via XML data files (then trigger `action_apply` from a post-init hook), not fork this module.
- **External calls:** none.
- **Cross-vertical:** generic admin tool; not vertical-locked.

## Gotchas
- **No view extension support** — the manifest summary advertises "view-extension manager" but the implementation only creates `ir.model.fields`. Adding `x_studio_*` to forms/lists still requires manual XML inheritance or studio-style view edits in a separate module.
- **Selection storage format is a stringified list of tuples** (`str(pairs)`), not JSON — this matches Odoo's internal serialisation but means programmatic edits must `ast.literal_eval` rather than `json.loads`.
- **Apply is destructive on update** — re-applying overwrites label / help / required / readonly on the existing `ir.model.fields`. The field's `ttype` cannot be safely changed once data exists; this module does not guard against type changes.
- **No delete-field workflow** — unlinking a `studio.custom.field` record only cascades from `ir.model` deletion. The materialised `ir.model.fields` is **not** removed when the declaration is unlinked.
- **`sudo()` write to `ir.model.fields`** — bypasses ACLs by design; only `studio_custom_field` access groups gate the feature.
- **No installable validation** that the target model is a real `models.Model` (not transient, not abstract).
- **Field changes require module/registry reload** before they're queryable via ORM in some contexts — Odoo handles this automatically on `ir.model.fields` create in 19, but a fresh worker may need a refresh.

## Out of Scope
- **Custom view inheritance / arch_db editing** — not implemented; ony `ir.model.fields` rows.
- **Relational fields** (Many2one / Many2many / One2many) — supported types are scalars + selection only.
- **Computed / related fields** — not supported.
- **Field-level access groups / record rules** — fall back to model-level ACLs.
- **Custom report / dashboard generation** — out of scope.
- **Reverting / removing materialised fields** — no UI for safe field deletion (would risk data loss).
- **Migration of x_studio_ fields between databases** — must be exported/imported via standard XML data files.
