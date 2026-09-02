# -*- coding: utf-8 -*-
"""Knowledge Article model.

Extends the original scaffold with:
- Full-text search backed by a GIN(to_tsvector) index (see hooks.py)
- Portal share token (auto-generated via secrets)
- Per-user favorites
- Dynamic Properties bag (parent_id is the definition record)
- Snapshot-on-body-change version history
- Apply-template helper that clones template body
"""

import logging
import secrets

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


def _new_share_token() -> str:
    """Return a URL-safe random token used for portal share links."""
    return secrets.token_urlsafe(32)


class KnowledgeArticle(models.Model):
    _name = "knowledge.article"
    _description = "Knowledge Article"
    _inherit = ["mail.thread", "mail.activity.mixin", "pdp.audited.mixin"]
    _order = "sequence, name"

    # ------------------------------------------------------------------
    # Core fields (kept from scaffold)
    # ------------------------------------------------------------------
    name = fields.Char(required=True, tracking=True)
    parent_id = fields.Many2one(
        "knowledge.article",
        string="Parent Article",
        ondelete="cascade",
        index=True,
    )
    child_ids = fields.One2many(
        "knowledge.article",
        "parent_id",
        string="Child Articles",
    )
    sequence = fields.Integer(default=10)
    body = fields.Html(sanitize=True, translate=True)
    is_published = fields.Boolean(default=False, tracking=True)
    tag_ids = fields.Many2many("knowledge.tag", string="Tags")
    owner_id = fields.Many2one(
        "res.users",
        string="Owner",
        default=lambda self: self.env.user,
        tracking=True,
    )
    read_group_ids = fields.Many2many(
        "res.groups",
        "knowledge_article_read_group_rel",
        "article_id",
        "group_id",
        string="Restricted Read Groups",
        help=("If set, only members of these groups can read this article. When empty, all Knowledge users may read."),
    )
    color = fields.Integer()
    display_name = fields.Char(
        compute="_compute_display_name",
        recursive=True,
        store=False,
    )

    # ------------------------------------------------------------------
    # Full-text search
    # ------------------------------------------------------------------
    # ``search_vector`` is a denormalised TEXT mirror of ``name || ' ' || body``
    # used by the SearchableMixin queries; the actual GIN index is on
    # ``to_tsvector(...)`` built in post_init_hook.
    search_vector = fields.Char(
        compute="_compute_search_vector",
        store=True,
        index=False,
        readonly=True,
    )

    # ------------------------------------------------------------------
    # Portal sharing
    # ------------------------------------------------------------------
    share_token = fields.Char(
        string="Share Token",
        copy=False,
        readonly=True,
        index=True,
    )
    is_shared_externally = fields.Boolean(
        string="Shared Externally",
        default=False,
        tracking=True,
        help="When enabled, anyone with the share link can view this article.",
    )

    # ------------------------------------------------------------------
    # Favorites
    # ------------------------------------------------------------------
    favorite_user_ids = fields.Many2many(
        "res.users",
        "knowledge_article_favorite_rel",
        "article_id",
        "user_id",
        string="Favorited By",
    )
    is_favorite = fields.Boolean(
        string="Is Favorite",
        compute="_compute_is_favorite",
        search="_search_is_favorite",
        store=False,
    )

    # ------------------------------------------------------------------
    # Properties bag (Odoo 19 dynamic fields)
    # ------------------------------------------------------------------
    properties = fields.Properties(
        string="Properties",
        definition="parent_id.property_definitions",
        copy=True,
    )
    property_definitions = fields.PropertiesDefinition(
        string="Property Definitions",
    )

    # ------------------------------------------------------------------
    # Versioning
    # ------------------------------------------------------------------
    version_ids = fields.One2many(
        "knowledge.article.version",
        "article_id",
        string="Versions",
        readonly=True,
    )
    version_count = fields.Integer(
        compute="_compute_version_count",
        string="# Versions",
    )

    # ------------------------------------------------------------------
    # Display name
    # ------------------------------------------------------------------
    @api.depends("name", "parent_id.display_name")
    def _compute_display_name(self):
        for rec in self:
            if rec.parent_id:
                rec.display_name = "%s / %s" % (rec.parent_id.display_name, rec.name or "")
            else:
                rec.display_name = rec.name or ""

    # ------------------------------------------------------------------
    # Computed: search_vector
    # ------------------------------------------------------------------
    @api.depends("name", "body")
    def _compute_search_vector(self):
        for rec in self:
            # strip tags crudely; tsvector index handles the real tokenization
            raw = (rec.name or "") + " " + (rec.body or "")
            # collapse whitespace, drop angle-bracketed content
            cleaned = []
            in_tag = False
            for ch in raw:
                if ch == "<":
                    in_tag = True
                    continue
                if ch == ">":
                    in_tag = False
                    cleaned.append(" ")
                    continue
                if not in_tag:
                    cleaned.append(ch)
            rec.search_vector = "".join(cleaned).strip()[:8000]

    # ------------------------------------------------------------------
    # Computed: favorites
    # ------------------------------------------------------------------
    @api.depends("favorite_user_ids")
    def _compute_is_favorite(self):
        uid = self.env.uid
        for rec in self:
            rec.is_favorite = uid in rec.favorite_user_ids.ids

    def _search_is_favorite(self, operator, value):
        positive = (operator == "=" and value) or (operator == "!=" and not value)
        domain_op = "in" if positive else "not in"
        return [("favorite_user_ids", domain_op, [self.env.uid])]

    # ------------------------------------------------------------------
    # Computed: version count
    # ------------------------------------------------------------------
    @api.depends("version_ids")
    def _compute_version_count(self):
        for rec in self:
            rec.version_count = len(rec.version_ids)

    # ------------------------------------------------------------------
    # CRUD overrides
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("is_shared_externally") and not vals.get("share_token"):
                vals["share_token"] = _new_share_token()
        return super().create(vals_list)

    def write(self, vals):
        """Snapshot the *previous* body when it is about to change."""
        snapshots = []
        if "body" in vals:
            for rec in self:
                if (rec.body or "") != (vals.get("body") or ""):
                    snapshots.append(
                        {
                            "article_id": rec.id,
                            "version_no": (rec.version_count or 0) + 1,
                            "body_snapshot": rec.body or "",
                            "saved_by": self.env.uid,
                        }
                    )
        if vals.get("is_shared_externally"):
            for rec in self:
                if not rec.share_token:
                    vals.setdefault("share_token", _new_share_token())
                    break
        res = super().write(vals)
        if snapshots:
            self.env["knowledge.article.version"].sudo().create(snapshots)
        return res

    # ------------------------------------------------------------------
    # Action helpers
    # ------------------------------------------------------------------
    def action_toggle_favorite(self):
        """Toggle membership of the current user in ``favorite_user_ids``."""
        self.ensure_one()
        if self.env.user in self.favorite_user_ids:
            self.favorite_user_ids = [(3, self.env.uid)]
        else:
            self.favorite_user_ids = [(4, self.env.uid)]
        return True

    def action_generate_share_link(self):
        """Generate (or rotate) the share token and mark as externally shared."""
        self.ensure_one()
        self.write(
            {
                "share_token": _new_share_token(),
                "is_shared_externally": True,
            }
        )
        base = (self.env["ir.config_parameter"].sudo().get_param("web.base.url") or "").rstrip("/")
        url = "%s/knowledge/share/%s" % (base, self.share_token)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Share link generated"),
                "message": url,
                "sticky": True,
                "type": "success",
            },
        }

    def action_revoke_share_link(self):
        self.ensure_one()
        self.write({"is_shared_externally": False, "share_token": False})
        return True

    def action_apply_template(self, template_id=None):
        """Clone ``template.body_template`` into ``self.body``.

        ``template_id`` may also be passed via ``self.env.context['default_template_id']``
        so the same method works from a button or from a wizard action.
        """
        self.ensure_one()
        template_id = template_id or self.env.context.get("default_template_id")
        if not template_id:
            raise UserError(_("No template provided."))
        template = self.env["knowledge.article.template"].browse(int(template_id))
        if not template.exists() or not template.is_active:
            raise UserError(_("Template not found or inactive."))
        self.body = template.body_template or ""
        return True

    def action_view_versions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Versions"),
            "res_model": "knowledge.article.version",
            "view_mode": "list,form",
            "domain": [("article_id", "=", self.id)],
            "context": {"default_article_id": self.id},
        }

    # ------------------------------------------------------------------
    # Full-text search API
    # ------------------------------------------------------------------
    @api.model
    def search_articles(self, query, limit=20):
        """Return [{id,name,rank,snippet}] ranked by ts_rank.

        Falls back to plain ``ilike`` if ``query`` is empty / pgsql refuses
        the tsquery (mis-parsed user input).
        """
        query = (query or "").strip()
        if not query:
            return []
        # Resolve accessible ids first (record-rule aware)
        accessible = self.search([]).ids
        if not accessible:
            return []
        try:
            self.env.cr.execute(
                """
                SELECT id,
                       name,
                       ts_rank(
                           to_tsvector('english',
                               coalesce(name,'') || ' ' || coalesce(body,'')),
                           plainto_tsquery('english', %s)
                       ) AS rank,
                       ts_headline('english',
                           coalesce(search_vector, ''),
                           plainto_tsquery('english', %s),
                           'MaxWords=20, MinWords=5, ShortWord=2'
                       ) AS snippet
                FROM knowledge_article
                WHERE id = ANY(%s)
                  AND to_tsvector('english',
                          coalesce(name,'') || ' ' || coalesce(body,''))
                      @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (query, query, accessible, query, int(limit)),
            )
            rows = self.env.cr.dictfetchall()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("knowledge full-text search failed: %s", exc)
            self.env.cr.rollback()
            hits = self.search(
                [("id", "in", accessible), ("name", "ilike", query)],
                limit=limit,
            )
            return [{"id": h.id, "name": h.name, "rank": 0.0, "snippet": ""} for h in hits]
        return rows

    # ------------------------------------------------------------------
    # Access action override (used by mail notifications etc.)
    # ------------------------------------------------------------------
    def _get_access_action(self, access_uid=None, force_website=False):
        """Route portal-shared articles to the public share URL."""
        self.ensure_one()
        if self.is_shared_externally and self.share_token and force_website:
            return {
                "type": "ir.actions.act_url",
                "url": "/knowledge/share/%s" % self.share_token,
                "target": "self",
                "res_id": self.id,
            }
        return super()._get_access_action(access_uid=access_uid, force_website=force_website)
