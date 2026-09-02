#!/usr/bin/env python3
"""
generate-search-views.py — give every custom model a working Search / Filter / Group By.

Why this exists
---------------
285 of the 608 custom models with a real table shipped without a search view. In
Odoo that is not a cosmetic gap: with no search view, the control panel offers no
filters, no Group By, and searching the list falls back to `name` alone. Any list
longer than a screen becomes unusable.

The views are GENERATED rather than hand-written because the input is a database
introspection, not a design decision: which fields exist, their types and their
labels all come from ir_model_fields on a live database where every module is
installed. That is also why this is a script and not a one-off edit -- a new
module gets its search view by re-running it.

What it emits, per model:
  * text fields worth typing into  -> <field>, so they are searched
  * `active`                       -> an Archived filter
  * date / selection / many2one    -> Group By entries
It deliberately does NOT invent business filters ("overdue", "mine"): a wrong
filter is worse than an absent one. Those are added by hand, and this file is
then no longer the owner of that view.

Usage:
  python scripts/generate-search-views.py                 # dry run
  python scripts/generate-search-views.py --apply
  python scripts/generate-search-views.py --check         # CI gate: exit 1 if any gap
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import escape

REPO = Path(__file__).resolve().parent.parent
ADDONS = REPO / "addons"
PG_CONTAINER = "odoo19-bct-postgres"
DB = "bct"

# Fields worth offering as free-text search, in the order a user would try them.
NAME_HINTS = ("name", "code", "reference", "ref", "number", "no", "title",
              "login", "email", "barcode", "serial", "label", "subject")
# Relations worth offering as search + group-by. company_id is group-by only:
# typing a company name to filter is rare, grouping by it is not.
REL_SEARCH = ("partner_id", "product_id", "user_id", "employee_id", "order_id",
              "picking_id", "move_id", "invoice_id", "journal_id", "account_id",
              "provider_id", "tenant_id", "asset_id", "location_id")
SKIP_FIELDS = {"id", "create_uid", "create_date", "write_uid", "write_date",
               "display_name", "__last_update", "message_ids", "message_follower_ids",
               "activity_ids", "website_message_ids", "message_main_attachment_id"}
MAX_SEARCH = 7
MAX_GROUP = 7


def psql(sql: str) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "odoo", "-d", DB, "-tAF\x1f", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode:
        sys.exit(f"psql failed:\n{out.stderr}")
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def gaps() -> dict[str, list[str]]:
    """module -> models with a real table and no search view."""
    rows = psql("""
        WITH ours AS (
          SELECT m.model, d.module, replace(m.model,'.','_') AS tbl
          FROM ir_model m
          JOIN ir_model_data d ON d.res_id = m.id AND d.model = 'ir.model'
          WHERE m.transient = false
            AND (d.module LIKE 'custom%%' OR d.module IN
                 ('queue_job','auth_jwt','authenticate_keycloak','l10n_id_coa_10d'))
            -- OWNERSHIP, not mere association. Odoo writes an ir_model_data row
            -- for every module that touches a model, so extending res.partner
            -- makes it look "ours". If any non-custom module also claims it, the
            -- model belongs to Odoo and already has a search view we must not
            -- shadow -- res.company reached this generator exactly that way.
            AND NOT EXISTS (
              SELECT 1 FROM ir_model_data d2
              WHERE d2.model = 'ir.model' AND d2.res_id = m.id
                AND d2.module NOT LIKE 'custom%%'
                AND d2.module NOT IN
                    ('queue_job','auth_jwt','authenticate_keycloak','l10n_id_coa_10d')
            )
        ), real AS (
          SELECT o.* FROM ours o
          JOIN information_schema.tables t
            ON t.table_schema = 'public' AND t.table_name = o.tbl
        ), searched AS (
          SELECT DISTINCT model FROM ir_ui_view WHERE type = 'search' AND model IS NOT NULL
        )
        SELECT r.module, r.model
        FROM real r LEFT JOIN searched s ON s.model = r.model
        WHERE s.model IS NULL
        ORDER BY r.module, r.model;
    """)
    out: dict[str, list[str]] = defaultdict(list)
    for module, model in rows:
        out[module].append(model)
    return out


def fields_of(models: list[str]) -> dict[str, list[dict]]:
    quoted = ",".join("'" + m.replace("'", "''") + "'" for m in models)
    rows = psql(f"""
        -- field_description is jsonb in Odoo 19 (translated), so read the
        -- en_US value out of it rather than casting the whole document.
        -- The owning module matters: a field added by a module that DEPENDS on
        -- the one we are writing into is not in the registry yet when this view
        -- loads, and the install dies with "Field ... does not exist".
        SELECT m.model, f.name, f.ttype,
               COALESCE(f.field_description->>'en_US', f.name),
               f.relation, COALESCE(d.module, '')
        FROM ir_model_fields f
        JOIN ir_model m ON m.id = f.model_id
        LEFT JOIN ir_model_data d
               ON d.model = 'ir.model.fields' AND d.res_id = f.id
        WHERE m.model IN ({quoted}) AND f.store = true
        ORDER BY m.model, f.name;
    """)
    out: dict[str, list[dict]] = defaultdict(list)
    for model, name, ttype, label, relation, owner in rows:
        out[model].append({"name": name, "ttype": ttype, "label": label,
                           "relation": relation, "owner": owner})
    return out


def depends_closure() -> dict[str, set[str]]:
    """module -> itself plus every module it depends on, transitively."""
    direct: dict[str, list[str]] = {}
    for man in ADDONS.rglob("__manifest__.py"):
        try:
            data = ast.literal_eval(man.read_text(encoding="utf-8"))
        except (ValueError, SyntaxError, OSError):
            continue
        direct[man.parent.name] = data.get("depends", [])

    cache: dict[str, set[str]] = {}

    def walk(mod: str, seen: frozenset = frozenset()) -> set[str]:
        if mod in cache:
            return cache[mod]
        if mod in seen:
            return {mod}
        acc = {mod}
        for dep in direct.get(mod, []):
            acc |= walk(dep, seen | {mod})
        cache[mod] = acc
        return acc

    return {m: walk(m) for m in direct}


def build_view(model: str, flds: list[dict], allowed: set[str] | None = None) -> str:
    # A field owned by a module outside this one's dependency closure is not
    # in the registry when this view loads. Referencing it is a hard install
    # failure, so drop it rather than emit a view that cannot load.
    if allowed is not None:
        flds = [f for f in flds if not f["owner"] or f["owner"] in allowed]
    by_name = {f["name"]: f for f in flds if f["name"] not in SKIP_FIELDS}

    search, seen = [], set()
    for hint in NAME_HINTS:
        for f in by_name.values():
            if f["ttype"] in ("char", "text") and hint in f["name"] and f["name"] not in seen:
                search.append(f); seen.add(f["name"])
                break
        if len(search) >= MAX_SEARCH:
            break
    for rel in REL_SEARCH:
        if len(search) >= MAX_SEARCH:
            break
        f = by_name.get(rel)
        if f and f["ttype"] == "many2one" and f["name"] not in seen:
            search.append(f); seen.add(f["name"])

    groups, gseen = [], set()
    for f in by_name.values():
        if len(groups) >= MAX_GROUP:
            break
        if f["name"] in gseen or f["name"] == "active":
            continue
        if f["ttype"] in ("selection", "many2one", "date", "datetime"):
            groups.append(f); gseen.add(f["name"])

    xid = model.replace(".", "_") + "_view_search"
    lines = [f'        <record id="{xid}" model="ir.ui.view">',
             f'            <field name="name">{escape(model)}.search</field>',
             f'            <field name="model">{escape(model)}</field>',
             '            <field name="arch" type="xml">',
             f'                <search string="{escape(_title(model))}">']
    for f in search:
        lines.append(f'                    <field name="{f["name"]}"/>')
    if "active" in by_name:
        lines.append('                    <separator/>')
        lines.append('                    <filter name="inactive" string="Archived" '
                     'domain="[(\'active\', \'=\', False)]"/>')
    if groups:
        # Odoo 19 tightened the search-view RelaxNG: <group> takes no `expand`
        # and no `string` here, and a group-by filter must carry an explicit
        # empty domain. The 17-era `<group expand="0" string="Group By">` fails
        # validation with "Invalid attribute expand for element group".
        lines.append('                    <group>')
        for f in groups:
            lines.append(f'                        <filter name="groupby_{f["name"]}" '
                         f'string="{escape(f["label"])}" domain="" '
                         f'context="{{\'group_by\': \'{f["name"]}\'}}"/>')
        lines.append('                    </group>')
    lines += ['                </search>', '            </field>', '        </record>']
    return "\n".join(lines)


def _title(model: str) -> str:
    return " ".join(p.capitalize() for p in model.split(".")[-3:])


def module_dir(module: str) -> Path | None:
    hits = [p.parent for p in ADDONS.rglob("__manifest__.py") if p.parent.name == module]
    return hits[0] if hits else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if any custom model still lacks a search view")
    args = ap.parse_args(argv)

    missing = gaps()
    closure = depends_closure()
    total = sum(len(v) for v in missing.values())

    if args.check:
        # Vendored OCA is out of scope for the same reason the generator skips
        # it: the tree is kept byte-identical to upstream so it can be re-fetched.
        missing = {m: v for m, v in missing.items()
                   if (d := module_dir(m)) is None or "_vendor" not in d.parts}
        total = sum(len(v) for v in missing.values())
        if total:
            print(f"{total} custom models have no search view, in {len(missing)} modules:")
            for mod, models in sorted(missing.items(), key=lambda kv: -len(kv[1]))[:15]:
                print(f"  {mod:36} {len(models):3}  {', '.join(models[:3])}"
                      f"{' ...' if len(models) > 3 else ''}")
            print("\nRun: python scripts/generate-search-views.py --apply")
            return 1
        print("every custom model with a table has a search view")
        return 0

    print(f"{total} models without a search view, across {len(missing)} modules")
    print("APPLYING" if args.apply else "DRY RUN (pass --apply to write)")
    written = skipped = 0

    for module, models in sorted(missing.items()):
        mdir = module_dir(module)
        if mdir is None:
            print(f"  ?? {module:36} module directory not found; skipped")
            skipped += len(models)
            continue
        # _vendor/ is upstream OCA, kept byte-identical so it can be re-fetched.
        # A missing search view there is upstream's to fix, not ours.
        if "_vendor" in mdir.parts:
            print(f"  -- {module:36} vendored OCA; left untouched")
            skipped += len(models)
            continue
        flds = fields_of(models)
        allowed = closure.get(module, {module})
        records = [build_view(m, flds.get(m, []), allowed) for m in models if flds.get(m)]
        if not records:
            skipped += len(models)
            continue
        rel = "views/generated_search_views.xml"
        print(f"  {module:36} {len(records):3} view(s) -> {rel}")
        if not args.apply:
            written += len(records)
            continue

        body = ('<?xml version="1.0" encoding="utf-8"?>\n'
                "<!--\n"
                "    GENERATED by scripts/generate-search-views.py. Do not hand-edit:\n"
                "    re-running the generator overwrites this file.\n\n"
                "    To customise one of these views, move its record into a normal view\n"
                "    file in this module. The generator only emits views for models that\n"
                "    have none, so a moved record is left alone from then on.\n"
                "-->\n<odoo>\n    <data>\n\n"
                + "\n\n".join(records)
                + "\n\n    </data>\n</odoo>\n")
        target = mdir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(body)

        manifest = mdir / "__manifest__.py"
        text = manifest.read_text(encoding="utf-8")
        if rel not in text:
            if re.search(r'"data"\s*:\s*\[', text):
                text = re.sub(r'("data"\s*:\s*\[)', rf'\1\n        "{rel}",', text, count=1)
            else:
                text = re.sub(r"(\{)", rf'\1\n    "data": [\n        "{rel}",\n    ],', text, count=1)
            manifest.write_text(text, encoding="utf-8", newline="")
        written += len(records)

    print(f"\n{written} views {'written' if args.apply else 'would be written'}"
          + (f", {skipped} skipped" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
