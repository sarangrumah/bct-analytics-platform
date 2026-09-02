# -*- coding: utf-8 -*-
"""Capability catalog — one entry per scanned ``custom_*`` module.

Auto-populated by ``_scan_all_modules()`` (called from a button, the monthly
cron, and the post-install hook). Each entry stores enough structured metadata
about a module for the AI analyzer to reason about whether a BRD requirement
can be fulfilled by it.
"""

from __future__ import annotations

import ast
import hashlib
import logging
import os
import re
from pathlib import Path

from odoo import _, api, fields, models
from odoo.modules import get_modules, get_module_path

_logger = logging.getLogger(__name__)


CATEGORY_BY_PATH_SEGMENT = {
    "core": "core",
    "control_plane": "control_plane",
    "compliance": "compliance",
    "ee_gap": "ee_gap",
    "operations": "operations",
    "verticals": "vertical",
}


# Map keywords found in the manifest summary/description/category to capability tag codes.
_KEYWORD_TAG_MAP: dict[str, list[str]] = {
    "rental": ["rental"],
    "lease": ["rental"],
    "withholding": ["withholding"],
    "pph": ["withholding", "indonesian-tax"],
    "ppn": ["indonesian-tax"],
    "coretax": ["indonesian-tax", "coretax"],
    "pajakku": ["indonesian-tax", "coretax"],
    "consolidation": ["consolidation", "accounting"],
    "intercompany": ["intercompany", "accounting"],
    "approval": ["approval-workflow"],
    "barcode": ["barcode-scan"],
    "wms": ["wms"],
    "hht": ["hht"],
    "audit": ["audit-trail"],
    "pdp": ["pdp", "audit-trail"],
    "ppob": ["ppob"],
    "ai": ["ai"],
    "anomaly": ["ai", "anomaly-detection"],
    "rfid": ["barcode-scan", "rfid"],
    "manufacturing": ["manufacturing"],
    "mrp": ["manufacturing"],
    "plm": ["manufacturing", "plm"],
    "payroll": ["payroll", "indonesian-payroll"],
    "bpjs": ["indonesian-payroll", "indonesian-tax"],
    "attendance": ["attendance"],
    "fleet": ["fleet"],
    "voip": ["voip"],
    "whatsapp": ["whatsapp"],
    "ecommerce": ["ecommerce"],
    "subscription": ["subscription"],
    "marketing": ["marketing"],
    "crm": ["crm"],
    "helpdesk": ["helpdesk"],
    "knowledge": ["knowledge"],
    "field service": ["field-service"],
    "iot": ["iot"],
    "quality": ["quality"],
    "tenant": ["multi-tenant"],
    "multi-tenant": ["multi-tenant"],
}


class CustomModuleCapabilityEntry(models.Model):
    _name = "custom.module.capability.entry"
    _description = "Module Capability Catalog Entry"
    _order = "category, module_name"
    _rec_name = "module_name"

    module_name = fields.Char(required=True, index=True)
    module_path = fields.Char()
    category = fields.Selection(
        [
            ("core", "Core"),
            ("control_plane", "Control Plane"),
            ("compliance", "Compliance"),
            ("ee_gap", "EE-Gap"),
            ("operations", "Operations"),
            ("vertical", "Vertical"),
        ],
        index=True,
    )
    summary = fields.Text()
    capability_tag_ids = fields.Many2many(
        "custom.module.capability.tag",
        "custom_module_capability_entry_tag_rel",
        "entry_id",
        "tag_id",
        string="Capability Tags",
    )
    depends = fields.Json(default=list)
    models_own = fields.Json(default=list)
    models_inherit = fields.Json(default=list)
    routes = fields.Json(default=list)
    fields_summary = fields.Json(
        default=list,
        help="List of {model, field, type} dicts harvested per model (max 50 per module).",
    )
    methods = fields.Json(
        default=list,
        help="Public method names declared in models/*.py (max 40).",
    )
    wizards = fields.Json(
        default=list,
        help="Transient/wizard model _name values found under wizards/ or matching '.wizard'.",
    )
    readme_excerpt = fields.Text(
        help="First ~1500 chars of README.md / README.rst at module root.",
    )
    knowledge_md = fields.Text(
        help="Full contents of MODULE_KNOWLEDGE.md at module root. "
        "Curated knowledge file (purpose, business flow, key models, "
        "integration points, gotchas) for the BRD analyzer LLM to "
        "consume. Edit-by-developer; generated baseline lives in git.",
    )
    knowledge_status = fields.Selection(
        [
            ("missing", "Missing"),
            ("draft", "Draft (LLM-generated, not yet reviewed)"),
            ("reviewed", "Reviewed by developer"),
            ("drift", "Drift (source changed since knowledge was last written)"),
        ],
        default="missing",
        index=True,
        help="Lifecycle marker. 'drift' is set when the source hash no longer "
        "matches the hash recorded when MODULE_KNOWLEDGE.md was last "
        "generated/edited — the knowledge file may be stale.",
    )
    declared_tags = fields.Json(
        default=list,
        help="Tags declared by the developer in __manifest__.py via the "
        "'capability_tags' key. Authoritative — overrides keyword "
        "inference when present.",
    )
    source_hash = fields.Char(
        index=True,
        help="SHA-256 digest of (manifest + models/ + controllers/) at the last scrape. Pillar of drift detection.",
    )
    knowledge_md_hash = fields.Char(
        help="SHA-256 of MODULE_KNOWLEDGE.md content at last scrape. Used to "
        "detect when the knowledge file itself was edited, which resets "
        "last_knowledge_source_hash.",
    )
    last_knowledge_source_hash = fields.Char(
        help="The source_hash recorded at the moment MODULE_KNOWLEDGE.md was "
        "last written (or last manually marked clean). Drift = "
        "source_hash != last_knowledge_source_hash.",
    )
    views_count = fields.Integer(default=0)
    reports_count = fields.Integer(default=0)
    security_groups = fields.Json(
        default=list,
        help="Group XML-IDs referenced from security/ir.model.access.csv.",
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
    version = fields.Char()
    last_scanned = fields.Datetime()
    notes = fields.Text()

    _module_name_uniq = models.Constraint(
        "unique(module_name)",
        "Capability entry module_name must be unique.",
    )

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    @api.model
    def action_rescan(self):
        count = self._scan_all_modules()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Capability Catalog"),
                "message": _("Scanned %s modules.") % count,
                "sticky": False,
                "type": "success",
            },
        }

    @api.model
    def _scan_all_modules(self) -> int:
        """Walk every available addon, harvest hub modules whose name starts
        with ``custom_``, and upsert a catalog entry per module.

        Returns number of entries written.
        """
        Tag = self.env["custom.module.capability.tag"]
        # Make sure we know the full tag vocabulary up-front to avoid N queries.
        tag_cache: dict[str, int] = {t.technical_code: t.id for t in Tag.sudo().search([])}

        written = 0
        for mod_name in get_modules():
            if not mod_name.startswith("custom_"):
                continue
            try:
                payload = self._scrape_module(mod_name)
            except Exception as exc:  # pragma: no cover - resilient scan
                _logger.warning("BRD scan: skipping %s: %s", mod_name, exc)
                continue
            if not payload:
                continue
            tag_ids = self._resolve_tag_ids(payload, tag_cache, Tag)
            vals = dict(payload, capability_tag_ids=[(6, 0, tag_ids)], last_scanned=fields.Datetime.now())
            existing = self.sudo().search([("module_name", "=", mod_name)], limit=1)
            # Drift resolution — runs against the *previous* record state.
            self._resolve_drift_status(vals, existing)
            if existing:
                existing.write(vals)
            else:
                self.sudo().create(vals)
            written += 1
        return written

    @api.model
    def _scrape_module(self, mod_name: str) -> dict | None:
        path = get_module_path(mod_name)
        if not path:
            return None
        manifest_data = self._read_manifest(path)
        if manifest_data is None:
            return None
        category = self._infer_category(path)
        models_own, models_inherit, fields_summary, methods, wizards = self._scrape_models(path)
        routes = self._scrape_routes(path)
        readme_excerpt = self._scrape_readme(path)
        knowledge_md, knowledge_status = self._scrape_knowledge(path)
        source_hash = self._compute_source_hash(path)
        knowledge_md_hash = hashlib.sha256((knowledge_md or "").encode("utf-8")).hexdigest() if knowledge_md else ""
        declared_tags = list(manifest_data.get("capability_tags") or [])
        views_count = self._count_files(os.path.join(path, "views"), ".xml")
        reports_count = self._count_files(os.path.join(path, "report"), ".xml") + self._count_files(
            os.path.join(path, "reports"), ".xml"
        )
        security_groups = self._scrape_security_groups(path)
        summary = manifest_data.get("summary") or manifest_data.get("description") or ""
        return {
            "module_name": mod_name,
            "module_path": path,
            "category": category,
            "summary": (summary or "").strip()[:4000],
            "depends": manifest_data.get("depends") or [],
            "models_own": models_own,
            "models_inherit": models_inherit,
            "routes": routes,
            "fields_summary": fields_summary,
            "methods": methods,
            "wizards": wizards,
            "readme_excerpt": readme_excerpt,
            "knowledge_md": knowledge_md,
            "knowledge_status": knowledge_status,
            "knowledge_md_hash": knowledge_md_hash,
            "source_hash": source_hash,
            "declared_tags": declared_tags,
            "views_count": views_count,
            "reports_count": reports_count,
            "security_groups": security_groups,
            "version": manifest_data.get("version"),
            "maturity": self._infer_maturity(
                models_own=models_own,
                routes=routes,
                fields_summary=fields_summary,
                methods=methods,
                readme_excerpt=readme_excerpt,
            ),
        }

    @staticmethod
    def _read_manifest(path: str) -> dict | None:
        manifest_file = os.path.join(path, "__manifest__.py")
        if not os.path.isfile(manifest_file):
            return None
        try:
            with open(manifest_file, "r", encoding="utf-8") as fh:
                raw = fh.read()
            return ast.literal_eval(raw)
        except Exception as exc:
            _logger.debug("BRD scan: bad manifest in %s: %s", path, exc)
            return None

    @staticmethod
    def _infer_category(path: str) -> str | bool:
        parts = Path(path).parts
        for seg in parts:
            if seg in CATEGORY_BY_PATH_SEGMENT:
                return CATEGORY_BY_PATH_SEGMENT[seg]
        # An unmapped addons group must stay visibly uncategorised: guessing here
        # silently mislabels every module in a newly added group.
        _logger.warning(
            "BRD scan: no addons group in %s maps to a category — add it to "
            "CATEGORY_BY_PATH_SEGMENT (and the `category` Selection)",
            path,
        )
        return False

    # Module-level regex constants for model scraping.
    _RE_NAME = re.compile(r"^\s*_name\s*=\s*['\"]([\w.]+)['\"]", re.MULTILINE)
    _RE_INHERIT_SINGLE = re.compile(r"^\s*_inherit\s*=\s*['\"]([\w.]+)['\"]", re.MULTILINE)
    _RE_INHERIT_LIST = re.compile(r"^\s*_inherit\s*=\s*\[([^\]]+)\]", re.MULTILINE)
    _RE_FIELD = re.compile(r"^\s+(\w+)\s*=\s*fields\.(\w+)\s*\(", re.MULTILINE)
    _RE_PUBLIC_METHOD = re.compile(r"^\s{4}def\s+([a-z][\w]*)\s*\(", re.MULTILINE)

    @classmethod
    def _scrape_models(cls, path: str) -> tuple[list[str], list[str], list[dict], list[str], list[str]]:
        """Walk models/ and wizards/ to harvest model names, inherited models,
        field declarations, public methods, and transient/wizard model names.

        Returns: (models_own, models_inherit, fields_summary, methods, wizards).
        """
        own: set[str] = set()
        inh: set[str] = set()
        fields_acc: list[dict] = []
        methods: set[str] = set()
        wizards: set[str] = set()
        # Scan both models/ and wizards/ — modules often split transient models.
        scan_dirs = [
            (os.path.join(path, "models"), False),
            (os.path.join(path, "wizards"), True),
            (os.path.join(path, "wizard"), True),
        ]
        # Track per-file "current model" so we can associate fields with their model.
        for base_dir, is_wizard_dir in scan_dirs:
            if not os.path.isdir(base_dir):
                continue
            for root, _dirs, files in os.walk(base_dir):
                for fn in files:
                    if not fn.endswith(".py"):
                        continue
                    try:
                        with open(os.path.join(root, fn), "r", encoding="utf-8") as fh:
                            src = fh.read()
                    except OSError:
                        continue
                    file_names = cls._RE_NAME.findall(src)
                    own.update(file_names)
                    inh.update(cls._RE_INHERIT_SINGLE.findall(src))
                    for chunk in cls._RE_INHERIT_LIST.findall(src):
                        inh.update(re.findall(r"['\"]([\w.]+)['\"]", chunk))
                    # Wizards: by folder convention or by ".wizard" in _name.
                    if is_wizard_dir:
                        wizards.update(file_names)
                    wizards.update(n for n in file_names if ".wizard" in n)
                    # Field harvest — primary "owner" model is the first _name
                    # or first _inherit found in the file. Cheap, good enough.
                    owner = (file_names[0] if file_names else None) or (cls._RE_INHERIT_SINGLE.findall(src) or [None])[
                        0
                    ]
                    for fname, ftype in cls._RE_FIELD.findall(src):
                        if len(fields_acc) >= 50:
                            break
                        fields_acc.append({"model": owner or "", "field": fname, "type": ftype})
                    # Public methods (4-space indent, lowercase first char, no underscore prefix).
                    for m in cls._RE_PUBLIC_METHOD.findall(src):
                        if len(methods) >= 40:
                            break
                        methods.add(m)
        return sorted(own), sorted(inh), fields_acc, sorted(methods), sorted(wizards)

    @staticmethod
    def _scrape_readme(path: str) -> str:
        for fname in ("README.md", "README.rst", "README.txt", "readme.md"):
            candidate = os.path.join(path, fname)
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as fh:
                        return fh.read(1500)
                except OSError:
                    return ""
        return ""

    @staticmethod
    def _resolve_drift_status(vals: dict, existing) -> None:
        """Mutate ``vals`` so ``knowledge_status`` and ``last_knowledge_source_hash``
        reflect drift state.

        Rules:
        * If the knowledge file is missing → status='missing', last_hash cleared.
        * If knowledge_md_hash changed since the last scrape (i.e. dev edited
          MODULE_KNOWLEDGE.md), refresh last_knowledge_source_hash =
          current source_hash. Status comes from the frontmatter.
        * Else if source_hash != last_knowledge_source_hash AND knowledge
          present → status='drift' overrides the frontmatter value.
        * Else keep status as parsed from the file (draft|reviewed).
        """
        new_source_hash = vals.get("source_hash") or ""
        new_knowledge_md_hash = vals.get("knowledge_md_hash") or ""
        has_knowledge = bool(vals.get("knowledge_md"))
        if not has_knowledge:
            vals["knowledge_status"] = "missing"
            vals["last_knowledge_source_hash"] = ""
            return
        previous_md_hash = existing.knowledge_md_hash if existing else ""
        previous_locked = existing.last_knowledge_source_hash if existing else ""
        if new_knowledge_md_hash != previous_md_hash:
            # The .md file itself changed — treat it as fresh; lock in the
            # current source hash. Status comes from frontmatter.
            vals["last_knowledge_source_hash"] = new_source_hash
            return
        # File unchanged. Compare source against locked-in hash.
        if previous_locked and new_source_hash != previous_locked:
            vals["knowledge_status"] = "drift"
            vals["last_knowledge_source_hash"] = previous_locked
        else:
            # First time we see this record, or no drift.
            vals["last_knowledge_source_hash"] = previous_locked or new_source_hash

    @staticmethod
    def _compute_source_hash(path: str) -> str:
        """SHA-256 digest of the module's source-bearing files.

        Includes manifest + every .py under models/ and controllers/. Stable
        ordering (sorted relative paths). Used as drift-detection ground
        truth: changes here mean knowledge file may be stale.
        """
        h = hashlib.sha256()
        candidates: list[str] = []
        manifest_p = os.path.join(path, "__manifest__.py")
        if os.path.isfile(manifest_p):
            candidates.append(manifest_p)
        for sub in ("models", "controllers", "wizards", "wizard"):
            sub_p = os.path.join(path, sub)
            if not os.path.isdir(sub_p):
                continue
            for root, _dirs, files in os.walk(sub_p):
                for fn in files:
                    if fn.endswith(".py"):
                        candidates.append(os.path.join(root, fn))
        candidates.sort()
        for fp in candidates:
            rel = os.path.relpath(fp, path).replace(os.sep, "/")
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            try:
                with open(fp, "rb") as fh:
                    h.update(fh.read())
            except OSError:
                continue
            h.update(b"\n---FILE---\n")
        return h.hexdigest()

    @staticmethod
    def _scrape_knowledge(path: str) -> tuple[str, str]:
        """Read MODULE_KNOWLEDGE.md (curated knowledge file) if present.

        Returns ``(body, status)`` where status is parsed from the optional
        YAML frontmatter (``status: draft|reviewed``). Cap body at 6000 chars
        so a single module entry stays cache-friendly.
        """
        candidate = os.path.join(path, "MODULE_KNOWLEDGE.md")
        if not os.path.isfile(candidate):
            return "", "missing"
        try:
            with open(candidate, "r", encoding="utf-8") as fh:
                raw = fh.read(8000)
        except OSError:
            return "", "missing"
        status = "draft"
        body = raw
        # YAML frontmatter: lines between two '---' markers at file start.
        if raw.lstrip().startswith("---"):
            stripped = raw.lstrip()
            end = stripped.find("\n---", 3)
            if end != -1:
                fm = stripped[3:end]
                rest_start = stripped.find("\n", end + 4)
                body = stripped[rest_start + 1 :] if rest_start != -1 else ""
                m = re.search(r"^\s*status\s*:\s*([a-zA-Z_]+)\s*$", fm, re.MULTILINE)
                if m:
                    val = m.group(1).strip().lower()
                    if val in ("reviewed", "draft", "missing"):
                        status = val
        return body[:6000], status

    @staticmethod
    def _count_files(folder: str, ext: str) -> int:
        if not os.path.isdir(folder):
            return 0
        return sum(1 for root, _dirs, files in os.walk(folder) for fn in files if fn.endswith(ext))

    @staticmethod
    def _scrape_security_groups(path: str) -> list[str]:
        csv_path = os.path.join(path, "security", "ir.model.access.csv")
        if not os.path.isfile(csv_path):
            return []
        try:
            with open(csv_path, "r", encoding="utf-8") as fh:
                src = fh.read()
        except OSError:
            return []
        # XML-IDs of groups appear in the group_id column, e.g. base.group_user.
        groups = set(re.findall(r"\b([a-z_][\w]*\.group_[\w]+)\b", src))
        return sorted(groups)

    @staticmethod
    def _scrape_routes(path: str) -> list[str]:
        ctrl_dir = os.path.join(path, "controllers")
        routes: set[str] = set()
        if not os.path.isdir(ctrl_dir):
            return []
        re_route = re.compile(r"@(?:http\.)?route\(\s*['\"]([^'\"]+)['\"]")
        re_route_list = re.compile(r"@(?:http\.)?route\(\s*\[([^\]]+)\]")
        for root, _dirs, files in os.walk(ctrl_dir):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                try:
                    with open(os.path.join(root, fn), "r", encoding="utf-8") as fh:
                        src = fh.read()
                except OSError:
                    continue
                routes.update(re_route.findall(src))
                for chunk in re_route_list.findall(src):
                    routes.update(re.findall(r"['\"]([^'\"]+)['\"]", chunk))
        return sorted(routes)

    @staticmethod
    def _infer_maturity(
        *,
        models_own: list[str],
        routes: list[str],
        fields_summary: list[dict],
        methods: list[str],
        readme_excerpt: str,
    ) -> str:
        score = (
            len(models_own) * 2
            + len(routes)
            + len(fields_summary) // 10
            + (5 if readme_excerpt else 0)
            + len(methods) // 5
        )
        if score >= 20:
            return "production"
        if score >= 5:
            return "partial"
        return "scaffold"

    def _resolve_tag_ids(self, payload: dict, tag_cache: dict, Tag) -> list[int]:
        """Resolve tag codes to tag ids.

        Priority:
        1. ``declared_tags`` from the module's manifest ``capability_tags`` key
           — authoritative, dev-curated.
        2. Keyword inference from summary + module_name + model names + README
           — fallback for modules that haven't declared tags yet.
        """
        declared = list(payload.get("declared_tags") or [])
        found_codes: set[str] = set(c for c in declared if c)
        if not declared:
            # Fallback inference only when no declared tags exist.
            models_blob = (
                " ".join(payload.get("models_own") or []) + " " + " ".join(payload.get("models_inherit") or [])
            )
            haystack = " ".join(
                [
                    (payload.get("summary") or "").lower(),
                    payload.get("module_name", "").lower(),
                    models_blob.lower(),
                    (payload.get("readme_excerpt") or "").lower(),
                ]
            )
            for keyword, codes in _KEYWORD_TAG_MAP.items():
                if keyword in haystack:
                    found_codes.update(codes)
        result: list[int] = []
        for code in found_codes:
            tag_id = tag_cache.get(code)
            if tag_id is None:
                new = Tag.sudo().create({"name": code.replace("-", " ").title(), "technical_code": code})
                tag_cache[code] = new.id
                tag_id = new.id
            result.append(tag_id)
        return result

    # ------------------------------------------------------------------
    # Cron entry-point
    # ------------------------------------------------------------------

    @api.model
    def _cron_rescan_catalog(self):
        try:
            count = self._scan_all_modules()
            _logger.info("custom_brd_analyzer: monthly catalog rescan wrote %s entries", count)
        except Exception as exc:  # pragma: no cover
            _logger.exception("custom_brd_analyzer: catalog rescan failed: %s", exc)

    @api.model
    def _cron_drift_notify(self):
        """Daily: scan catalog, then post a mail.activity to BRD admins if any
        module has knowledge_status='drift'. Cheap & lossless — admins decide
        whether to regenerate."""
        try:
            self._scan_all_modules()  # refresh state
        except Exception:  # pragma: no cover
            _logger.exception("custom_brd_analyzer: drift cron rescan failed")
            return
        drifted = self.sudo().search([("knowledge_status", "=", "drift")])
        if not drifted:
            _logger.info("custom_brd_analyzer: drift cron — no drift detected")
            return
        names = drifted.mapped("module_name")
        _logger.info("custom_brd_analyzer: drift cron — %d module(s) drift: %s", len(names), ", ".join(names[:10]))
        # Try posting mail.activity to brd admin group users.
        try:
            group = self.env.ref("custom_brd_analyzer.group_brd_admin", raise_if_not_found=False)
        except Exception:
            group = None
        if not group:
            return
        admin_users = group.user_ids.filtered(lambda u: u.active and not u.share)
        if not admin_users:
            return
        Activity = self.env["mail.activity"].sudo()
        ActivityType = self.env.ref("mail.mail_activity_data_todo", raise_if_not_found=False)
        body = (
            "Knowledge drift detected in %d hub module(s):\n\n- %s\n\n"
            "Open Capability Catalog → filter 'Knowledge Drift' → use "
            "the 'Regenerate Knowledge' action to refresh."
        ) % (len(names), "\n- ".join(names))
        for user in admin_users:
            try:
                Activity.create(
                    {
                        "summary": "BRD Analyzer: %d module(s) knowledge drift" % len(names),
                        "note": body,
                        "user_id": user.id,
                        "res_model_id": self.env["ir.model"]._get_id(self._name),
                        "res_id": drifted[0].id,
                        "activity_type_id": ActivityType.id if ActivityType else False,
                    }
                )
            except Exception:  # pragma: no cover
                _logger.exception("drift notify: activity post failed for user %s", user.id)

    # ------------------------------------------------------------------
    # Auto-rescan on module upgrade
    #
    # We piggy-back on registry load: if the installed module version (from
    # ir.module.module) differs from the version we last rescanned at, the
    # catalog schema and/or scraping logic likely changed → rescan once.
    # Cheap version compare beats running a full walk on every server start.
    # ------------------------------------------------------------------

    @api.model
    def _maybe_rescan_on_upgrade(self) -> None:
        Param = self.env["ir.config_parameter"].sudo()
        Module = self.env["ir.module.module"].sudo()
        mod = Module.search([("name", "=", "custom_brd_analyzer")], limit=1)
        if not mod:
            return
        current = mod.latest_version or ""
        key = "custom_brd_analyzer.last_rescan_version"
        last = Param.get_param(key, default="") or ""
        if current and current != last:
            try:
                count = self._scan_all_modules()
                Param.set_param(key, current)
                _logger.info(
                    "custom_brd_analyzer: auto-rescan on upgrade (%s → %s) wrote %s entries",
                    last or "-",
                    current,
                    count,
                )
            except Exception:  # pragma: no cover
                _logger.exception("custom_brd_analyzer: auto-rescan on upgrade failed")

    def _register_hook(self):
        super()._register_hook()
        # Defer to a fresh env after the registry is fully built, so model
        # graph + dependencies are stable when we walk modules.
        try:
            with self.pool.cursor() as cr:
                from odoo import api as _api, SUPERUSER_ID as _SU

                env = _api.Environment(cr, _SU, {})
                env["custom.module.capability.entry"]._maybe_rescan_on_upgrade()
        except Exception:  # pragma: no cover
            _logger.exception("custom_brd_analyzer: _register_hook auto-rescan failed")

    # ------------------------------------------------------------------
    # Catalog snapshot for AI prompt
    # ------------------------------------------------------------------

    @api.model
    def _build_gap_matrix(self) -> dict:
        """Pre-computed map of capability tag → list of modules covering it.

        Per (tag, module) score: maturity weight (production=3, partial=2) +
        knowledge weight (reviewed=2, draft=1, drift=0, missing=0). Sorted
        descending by score.

        The LLM uses this as a fast index to AVOID proposing new modules whose
        capability tag already has score >= 3 (production module + at least
        draft knowledge).
        """
        entries = self.sudo().search([("maturity", "in", ("partial", "production"))])
        matrix: dict[str, list[dict]] = {}
        for e in entries:
            mat_score = 3 if e.maturity == "production" else 2
            ks = e.knowledge_status or "missing"
            kn_score = {"reviewed": 2, "draft": 1, "drift": 0, "missing": 0}.get(ks, 0)
            score = mat_score + kn_score
            tag_codes = e.capability_tag_ids.mapped("technical_code")
            for code in tag_codes:
                matrix.setdefault(code, []).append(
                    {
                        "module": e.module_name,
                        "score": score,
                        "knowledge_status": ks,
                    }
                )
        for code in matrix:
            matrix[code].sort(key=lambda r: r["score"], reverse=True)
        return matrix

    @api.model
    def _build_prompt_catalog(self, maturities: tuple[str, ...] = ("partial", "production")) -> list[dict]:
        """Compact dict-list for embedding in the LLM prompt.

        Only includes modules with maturity in ``maturities`` (default: skip
        scaffolds — they cannot fulfil requirements yet).
        """
        entries = self.sudo().search([("maturity", "in", list(maturities))])
        out: list[dict] = []
        for e in entries:
            # Pluck top-N fields (variety beats quantity — prefer first per model).
            fields_payload = []
            seen_models: set[str] = set()
            for row in e.fields_summary or []:
                if not isinstance(row, dict):
                    continue
                fields_payload.append(
                    {
                        "model": row.get("model") or "",
                        "field": row.get("field") or "",
                        "type": row.get("type") or "",
                    }
                )
                seen_models.add(row.get("model") or "")
                if len(fields_payload) >= 30:
                    break
            # Curated knowledge takes priority — when reviewed by a dev,
            # it is the single best signal for the LLM. Auto-scraped fields
            # remain as fallback for modules where knowledge_md is still
            # draft or missing.
            knowledge = e.knowledge_md or ""
            knowledge_cap = 4000 if e.knowledge_status == "reviewed" else 2500
            out.append(
                {
                    "module": e.module_name,
                    "category": e.category,
                    "summary": (e.summary or "")[:600],
                    "knowledge": knowledge[:knowledge_cap],
                    "knowledge_status": e.knowledge_status or "missing",
                    "readme": (e.readme_excerpt or "")[:800],
                    "tags": e.capability_tag_ids.mapped("technical_code"),
                    "models": (e.models_own or [])[:20],
                    "models_inherit": (e.models_inherit or [])[:15],
                    "fields": fields_payload,
                    "methods": (e.methods or [])[:15],
                    "wizards": (e.wizards or [])[:10],
                    "routes": (e.routes or [])[:10],
                    "views_count": e.views_count or 0,
                    "reports_count": e.reports_count or 0,
                    "security_groups": (e.security_groups or [])[:10],
                    "depends": (e.depends or [])[:30],
                    "maturity": e.maturity,
                }
            )
        return out
