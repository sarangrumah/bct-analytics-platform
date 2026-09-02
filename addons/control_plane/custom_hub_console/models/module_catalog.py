# -*- coding: utf-8 -*-
"""Module catalog: scans ``addons/`` and registers every custom module
into a hub-managed catalog that can be deployed to tenants.

The catalog is partially overlapping with
``custom.module.capability.entry`` (from ``custom_brd_analyzer``) but
serves a different purpose: this one tracks **deployability** (which
tenant got which module, current version, maturity), not capability
tagging.
"""

from __future__ import annotations

import ast
import logging
import os
import re

from odoo import api, fields, models

_logger = logging.getLogger(__name__)

# Directories under ``addons/`` we expect for the platform.
_PLATFORM_BUCKETS = {
    "core": "core",
    "control_plane": "control_plane",
    "compliance": "compliance",
    "ee_gap": "ee_gap",
    "operations": "operations",
    "verticals": "vertical",
}


class CustomHubModuleCatalog(models.Model):
    _name = "custom.hub.module.catalog"
    _description = "Hub Module Catalog Entry"
    _order = "category, module_name"
    _rec_name = "module_name"

    module_name = fields.Char(required=True, index=True)
    version = fields.Char()
    category = fields.Selection(
        [
            ("core", "Core"),
            ("control_plane", "Control Plane"),
            ("compliance", "Compliance"),
            ("ee_gap", "EE-Gap"),
            ("operations", "Operations"),
            ("vertical", "Vertical"),
            ("odoo", "Odoo Standard"),
        ],
        index=True,
    )
    maturity = fields.Selection(
        [
            ("scaffold", "Scaffold"),
            ("partial", "Partial"),
            ("production", "Production"),
        ],
        default="partial",
        index=True,
    )
    source_module_id = fields.Many2one(
        "ir.module.module",
        string="Installed Module",
        ondelete="set null",
        help="Link to the Odoo ir.module.module row when this catalog "
        "entry corresponds to a module installed in *this* database.",
    )
    summary = fields.Text()
    capability_tag_ids = fields.Many2many(
        comodel_name="custom.module.capability.tag",
        relation="custom_hub_catalog_tag_rel",
        column1="catalog_id",
        column2="tag_id",
        string="Capability Tags",
    )
    depends_module_ids = fields.Many2many(
        comodel_name="custom.hub.module.catalog",
        relation="custom_hub_catalog_depends_rel",
        column1="catalog_id",
        column2="depends_id",
        string="Depends On",
    )
    models_own_count = fields.Integer(default=0)
    models_inherit_count = fields.Integer(default=0)
    deployment_count = fields.Integer(compute="_compute_deployment_count", store=False)
    last_scanned = fields.Datetime()

    _module_name_uniq = models.Constraint(
        "unique(module_name)",
        "Catalog entry module_name must be unique.",
    )

    # ------------------------------------------------------------------
    # Computes
    # ------------------------------------------------------------------
    def _compute_deployment_count(self):
        Deployment = self.env["custom.hub.module.deployment"].sudo()
        for rec in self:
            rec.deployment_count = Deployment.search_count(
                [("catalog_id", "=", rec.id), ("state", "in", ("installed", "upgrading"))]
            )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    @api.model
    def _addons_root(self) -> str:
        """Return the platform ``addons/`` root path."""
        # this file is at addons/<group>/custom_hub_console/models/... — the
        # ".." count below breaks if this module is ever nested any deeper.
        here = os.path.dirname(os.path.abspath(__file__))
        return os.path.normpath(os.path.join(here, "..", "..", ".."))

    @api.model
    def _action_scan_all(self):
        """Scan custom ``addons/`` buckets + index Odoo standard apps.

        Phases (all idempotent, never delete rows):
          1. Filesystem scan of platform buckets (custom modules).
          2. Index Odoo ``application=True`` modules (category ``odoo``).
          3. Link ``depends_module_ids`` for every row from the module
             registry's declared dependencies.
          4. Link seed industry packs to their catalog rows (only fills
             empty ``module_ids`` — never clobbers operator edits).

        Returns dict ``{'created': n, 'updated': m, 'total': k}``.
        """
        Modules = self.env["ir.module.module"].sudo()
        created, updated, total = self._scan_platform_buckets(Modules)

        # Phase 2: Odoo standard application modules.
        c2, u2 = self._scan_odoo_apps(Modules)
        created += c2
        updated += u2
        total += c2 + u2

        # Phase 3: dependency graph (needs all rows present first).
        self._link_dependencies(Modules)

        # Phase 4: seed packs -> catalog rows (same addon; defined on the
        # pack model). Guarded so a scan never fails if the pack model is
        # somehow unavailable.
        try:
            self.env["custom.hub.industry.pack"].sudo()._link_seed_modules()
        except Exception as exc:  # pragma: no cover - defensive
            _logger.debug("[hub_catalog] seed pack link skipped: %s", exc)

        _logger.info(
            "[hub_catalog] scan complete: created=%s updated=%s total=%s",
            created,
            updated,
            total,
        )
        return {"created": created, "updated": updated, "total": total}

    @api.model
    def _scan_platform_buckets(self, Modules):
        """Phase 1: scan the platform ``addons/`` buckets (custom modules)."""
        root = self._addons_root()
        created, updated, total = 0, 0, 0
        if not os.path.isdir(root):
            _logger.warning("[hub_catalog] addons root not found: %s", root)
            return created, updated, total

        for bucket, category in _PLATFORM_BUCKETS.items():
            bucket_path = os.path.join(root, bucket)
            if not os.path.isdir(bucket_path):
                continue
            for name in os.listdir(bucket_path):
                module_path = os.path.join(bucket_path, name)
                manifest = os.path.join(module_path, "__manifest__.py")
                if not os.path.isfile(manifest):
                    continue
                meta = self._parse_manifest(manifest)
                if not meta:
                    continue
                models_own, models_inherit = self._count_models(module_path)
                installed = Modules.search([("name", "=", name)], limit=1)
                vals = {
                    "module_name": name,
                    "version": meta.get("version") or "",
                    "category": category,
                    "summary": (meta.get("summary") or "")[:500],
                    "models_own_count": models_own,
                    "models_inherit_count": models_inherit,
                    "last_scanned": fields.Datetime.now(),
                    "source_module_id": installed.id if installed else False,
                }
                # Maturity heuristic: production if >=5 models + tests dir
                has_tests = os.path.isdir(os.path.join(module_path, "tests"))
                if models_own >= 5 and has_tests:
                    vals["maturity"] = "production"
                elif models_own == 0:
                    vals["maturity"] = "scaffold"
                else:
                    vals["maturity"] = "partial"

                existing = self.search([("module_name", "=", name)], limit=1)
                if existing:
                    existing.write(vals)
                    updated += 1
                else:
                    self.create(vals)
                    created += 1
                total += 1
        return created, updated, total

    @api.model
    def _scan_odoo_apps(self, Modules):
        """Phase 2: index Odoo standard *application* modules.

        Only ``application=True`` modules are indexed (the ~40-60 headline
        apps: Sales, Purchase, Inventory, Accounting, POS, CRM, HR, …) so
        the catalog stays readable rather than listing every technical
        module. Custom modules already classified by Phase 1 keep their
        platform category and are never reclassified to ``odoo``.
        """
        created, updated = 0, 0
        apps = Modules.search([("application", "=", True)])
        for mod in apps:
            existing = self.search([("module_name", "=", mod.name)], limit=1)
            # Leave our own platform modules under their bucket category.
            if existing and existing.category and existing.category != "odoo":
                continue
            vals = {
                "module_name": mod.name,
                "version": mod.installed_version or mod.latest_version or "",
                "category": "odoo",
                "maturity": "production",
                "summary": (mod.shortdesc or mod.summary or "")[:500],
                "last_scanned": fields.Datetime.now(),
                "source_module_id": mod.id,
            }
            if existing:
                existing.write(vals)
                updated += 1
            else:
                self.create(vals)
                created += 1
        return created, updated

    @api.model
    def _link_dependencies(self, Modules):
        """Phase 3: populate ``depends_module_ids`` (direct deps) for all rows.

        Declared dependencies are read uniformly from the module registry
        (``ir.module.module.dependencies_id``), covering both custom and
        Odoo-standard modules. Only deps that themselves have a catalog
        row are linked; technical deps without a row (``base``, ``mail``,
        ``analytic``, …) are skipped — Odoo resolves those at install time.
        """
        rows = self.search([])
        name_to_cat = {c.module_name: c.id for c in rows}
        for cat in rows:
            mod = Modules.search([("name", "=", cat.module_name)], limit=1)
            if not mod:
                continue
            dep_ids = []
            for dep in mod.dependencies_id:
                cid = name_to_cat.get(dep.name)
                if cid and cid != cat.id:
                    dep_ids.append(cid)
            cat.depends_module_ids = [(6, 0, dep_ids)]
        return True

    @api.model
    def _toposort_module_names(self, roots):
        """Deps-first topological order of ``roots`` + transitive
        ``depends_module_ids`` → list of ``module_name`` (de-duped,
        cycle-safe). Shared by the deployment resolver and industry packs.
        """
        order = []
        visited = set()
        temp = set()

        def visit(node):
            if node.id in visited:
                return
            if node.id in temp:
                # cycle: stop recursing but still emit this node
                return
            temp.add(node.id)
            for dep in node.depends_module_ids:
                visit(dep)
            temp.discard(node.id)
            visited.add(node.id)
            order.append(node.module_name)

        for root in roots:
            visit(root)
        return order

    @api.model
    def _parse_manifest(self, path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            node = ast.parse(src, filename=path)
            for stmt in node.body:
                if isinstance(stmt, ast.Expression):
                    return ast.literal_eval(stmt.body)
                if isinstance(stmt, ast.Expr):
                    return ast.literal_eval(stmt.value)
            # Some manifests are bare dicts on first line — fall through
            return ast.literal_eval(src)
        except Exception as exc:
            _logger.debug("manifest parse failed for %s: %s", path, exc)
            return None

    _NAME_RE = re.compile(r"^\s*_name\s*=\s*['\"]([\w.]+)['\"]", re.MULTILINE)
    _INHERIT_RE = re.compile(r"^\s*_inherit\s*=\s*['\"]([\w.]+)['\"]", re.MULTILINE)

    @api.model
    def _count_models(self, module_path: str) -> tuple[int, int]:
        own, inherit = 0, 0
        models_dir = os.path.join(module_path, "models")
        if not os.path.isdir(models_dir):
            return 0, 0
        for fname in os.listdir(models_dir):
            if not fname.endswith(".py"):
                continue
            full = os.path.join(models_dir, fname)
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    src = fh.read()
                own += len(self._NAME_RE.findall(src))
                inherit += len(self._INHERIT_RE.findall(src))
            except Exception:
                continue
        return own, inherit

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_open_deploy_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Deploy Module",
            "res_model": "custom.hub.deploy.module.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_catalog_id": self.id},
        }
