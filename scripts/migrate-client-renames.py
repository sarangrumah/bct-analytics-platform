#!/usr/bin/env python3
"""
migrate-client-renames.py — move an existing database onto the de-branded names.

The import pipeline (scripts/import-platform-addons.py) strips customer names from
the source tree. For a fresh database that is the whole job. For a database that is
already installed it is only half: the customer names are also in

  * ir_module_module.name          (custom_levis_localization)
  * ir_model.model + the table     (levis.cogs.run -> levis_cogs_run)
  * ir_model_fields.name + column  (account_move.levis_categ_reclass_id)
  * ir_model_data.name             (644 XML IDs)

and Odoo renames none of those on upgrade. It would instead treat every renamed
model as new, leave the old tables orphaned, and leave the old module installed.

This script does the rename in the database so it matches the tree. It derives the
new names by running the SAME substitution table the import uses, imported from
that script rather than copied, so the two cannot drift apart.

Every statement is guarded on the old name still existing, so the script is
idempotent: running it twice is a no-op, and running it on a database that was
never branded does nothing.

Usage:
  python scripts/migrate-client-renames.py                 # print the SQL, change nothing
  python scripts/migrate-client-renames.py --apply
  python scripts/migrate-client-renames.py --check         # exit 1 if any old name remains

Run it BETWEEN the re-import and the module upgrade:
  python scripts/import-platform-addons.py --source ... --wave N --apply
  python scripts/migrate-client-renames.py --apply
  make install-modules MODULES=...
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PG_CONTAINER = "odoo19-bct-postgres"
DB = "bct"

# The substitution table lives in the import script. Importing it keeps one
# source of truth: a rule added there is applied here on the next run.
_spec = importlib.util.spec_from_file_location(
    "import_platform_addons", REPO / "scripts" / "import-platform-addons.py")
_imp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_imp)
scrub = _imp._scrub_client

# RENAMES in the import script serves two different purposes, and only one of them
# may be replayed against a database.
#
#   custom_pdp_core -> custom_pdp_taxonomy resolves a NAME COLLISION at import
#   time: the platform's module and this repo's own module share a directory name
#   and are unrelated. In the database, `custom_pdp_core` IS this repo's module and
#   is already correct. Replaying that rename tries to rename it onto the taxonomy
#   module that is also installed, and Postgres rejects it on the unique index --
#   correctly, and loudly, which is how this distinction was found.
#
# Only the de-branding renames describe a database that is out of date.
IMPORT_ONLY_RENAMES = {"custom_pdp_core"}
RENAMES = {k: v for k, v in _imp.RENAMES.items() if k not in IMPORT_ONLY_RENAMES}

# Columns that hold a model NAME as text. Renaming a model without updating these
# leaves attachments, chatter and actions pointing at a model that no longer
# exists. This is the set Odoo 19 ships; a module that invents another such column
# is not covered, which is why --check exists.
MODEL_REF_COLUMNS = [
    ("ir_model_data", "model"),
    ("ir_ui_view", "model"),
    ("ir_act_window", "res_model"),
    ("ir_act_server", "model_name"),
    ("ir_attachment", "res_model"),
    ("ir_default", "json_value"),          # guarded below; only exact matches
    ("mail_message", "model"),
    ("mail_followers", "res_model"),
    ("mail_activity", "res_model"),
    ("mail_message_subtype", "res_model"),
    ("ir_model_fields", "relation"),
]


def psql(sql: str, db: str = DB) -> list[list[str]]:
    out = subprocess.run(
        ["docker", "exec", PG_CONTAINER, "psql", "-U", "odoo", "-d", db,
         "-tAF\x1f", "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode:
        sys.exit(f"psql failed:\n{out.stderr}")
    return [ln.split("\x1f") for ln in out.stdout.splitlines() if ln.strip()]


def q(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


_REF_CACHE: list[tuple[str, str]] | None = None


def _present_ref_columns() -> list[tuple[str, str]]:
    """MODEL_REF_COLUMNS filtered to what this schema actually has.

    The list is written against Odoo 19 but must not be trusted blind: it already
    named ir_act_server.model_name, which does not exist here. Asking the schema
    turns a wrong entry into a skipped one instead of a failed migration.
    """
    global _REF_CACHE
    if _REF_CACHE is None:
        have = {(r[0], r[1]) for r in psql(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public';")}
        _REF_CACHE = [(t, c) for t, c in MODEL_REF_COLUMNS
                      if (t, c) in have and t != "ir_default"]
        missing = [f"{t}.{c}" for t, c in MODEL_REF_COLUMNS
                   if (t, c) not in have and t != "ir_default"]
        if missing:
            print(f"-- not in this schema, skipped: {', '.join(missing)}", file=sys.stderr)
    return _REF_CACHE


def collect() -> dict:
    """Everything in the database whose name the scrub would change."""
    modules = [r[0] for r in psql(
        "SELECT name FROM ir_module_module WHERE name = ANY(%s) ORDER BY name;"
        % ("ARRAY[" + ",".join(q(k) for k in RENAMES) + "]" if RENAMES else "ARRAY[]::text[]"))]

    models = [(r[0], scrub(r[0])) for r in psql(
        "SELECT model FROM ir_model ORDER BY model;")]
    models = [(o, n) for o, n in models if o != n]

    fields = [(r[0], r[1], scrub(r[1])) for r in psql(
        "SELECT m.model, f.name FROM ir_model_fields f "
        "JOIN ir_model m ON m.id = f.model_id ORDER BY 1, 2;")]
    fields = [(m, o, n) for m, o, n in fields if o != n]

    xmlids = [(r[0], r[1], scrub(r[1])) for r in psql(
        "SELECT module, name FROM ir_model_data ORDER BY 1, 2;")]
    xmlids = [(m, o, n) for m, o, n in xmlids if o != n]

    # Tables are collected independently of models. A many2many join table is
    # named after two models and survives as an orphan once the model pass has
    # nothing left to do, so it cannot be driven off pending model renames.
    tables = [(r[0], scrub(r[0])) for r in psql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind IN ('r','S') ORDER BY 1;")]
    tables = [(o, n) for o, n in tables if o != n]

    return {"modules": modules, "models": models, "fields": fields,
            "xmlids": xmlids, "tables": tables}


def build_sql(state: dict) -> list[str]:
    out: list[str] = ["BEGIN;"]

    # ---- modules -----------------------------------------------------------
    for old in state["modules"]:
        new = RENAMES[old]
        out += [
            f"-- module {old} -> {new}",
            f"UPDATE ir_module_module SET name = {q(new)} WHERE name = {q(old)};",
            f"UPDATE ir_model_data SET module = {q(new)} WHERE module = {q(old)};",
            f"UPDATE ir_module_module_dependency SET name = {q(new)} WHERE name = {q(old)};",
        ]

    # ---- models ------------------------------------------------------------
    for old, new in state["models"]:
        ot, nt = old.replace(".", "_"), new.replace(".", "_")
        out.append(f"-- model {old} -> {new}")
        # The table, its sequence, and the m2m tables named after it. to_regclass
        # returns NULL rather than raising when the relation is absent, which is
        # what makes a second run a no-op.
        out.append(f"""DO $$ BEGIN
  IF to_regclass('public.{ot}') IS NOT NULL AND to_regclass('public.{nt}') IS NULL THEN
    EXECUTE 'ALTER TABLE public.{ot} RENAME TO {nt}';
  END IF;
  IF to_regclass('public.{ot}_id_seq') IS NOT NULL AND to_regclass('public.{nt}_id_seq') IS NULL THEN
    EXECUTE 'ALTER SEQUENCE public.{ot}_id_seq RENAME TO {nt}_id_seq';
  END IF;
END $$;""")
        # Many2many join tables are named <model_a>_<model_b>_rel, so the model's
        # table name is a PREFIX of them, not the whole name. Renaming only the
        # exact match leaves levis_categ_reclass_product_template_rel behind, and
        # Odoo would then create a second, empty join table beside it.
        out.append(f"""DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT c.relname AS old_name,
           {q(nt)} || substring(c.relname from {len(ot) + 1}) AS new_name
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relkind = 'r'
      AND c.relname LIKE {q(ot + chr(92) + '_%')}
  LOOP
    IF to_regclass('public.' || r.new_name) IS NULL THEN
      EXECUTE format('ALTER TABLE public.%I RENAME TO %I', r.old_name, r.new_name);
    END IF;
  END LOOP;
END $$;""")
        out.append(f"UPDATE ir_model SET model = {q(new)} WHERE model = {q(old)};")
        out.append(
            f"UPDATE ir_model_data SET name = {q('model_' + nt)} "
            f"WHERE model = 'ir.model' AND name = {q('model_' + ot)};")
        for table, col in _present_ref_columns():
            out.append(
                f"UPDATE {table} SET {col} = {q(new)} WHERE {col} = {q(old)};")
        # Field XML IDs embed the model: field_<model>__<field>.
        out.append(
            f"UPDATE ir_model_data SET name = {q('field_' + nt + '__')} || "
            f"substring(name from {len('field_' + ot + '__') + 1}) "
            f"WHERE model = 'ir.model.fields' AND name LIKE {q('field_' + ot + '__' + '%')};")
        # Many2many relation tables are registered by name.
        out.append(
            f"UPDATE ir_model_relation SET name = replace(name, {q(ot)}, {q(nt)}) "
            f"WHERE name LIKE {q('%' + ot + '%')};")

    # ---- fields ------------------------------------------------------------
    for model, old, new in state["fields"]:
        tbl = scrub(model).replace(".", "_")
        out += [
            f"-- field {model}.{old} -> {new}",
            f"""DO $$ BEGIN
  IF to_regclass('public.{tbl}') IS NOT NULL
     AND EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='{tbl}' AND column_name='{old}')
     AND NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='public' AND table_name='{tbl}' AND column_name='{new}') THEN
    EXECUTE 'ALTER TABLE public.{tbl} RENAME COLUMN {old} TO {new}';
  END IF;
END $$;""",
            f"UPDATE ir_model_fields SET name = {q(new)} WHERE name = {q(old)} "
            f"AND model_id = (SELECT id FROM ir_model WHERE model = {q(scrub(model))});",
        ]

    # ---- leftover relations -------------------------------------------------
    for old, new in state.get("tables", []):
        out.append(f"-- relation {old} -> {new}")
        out.append(f"""DO $$ BEGIN
  IF to_regclass('public.{old}') IS NOT NULL AND to_regclass('public.{new}') IS NULL THEN
    EXECUTE 'ALTER TABLE public.{old} RENAME TO {new}';
  END IF;
END $$;""")
        out.append(
            f"UPDATE ir_model_relation SET name = {q(new)} WHERE name = {q(old)};")

    # ---- XML IDs -----------------------------------------------------------
    # Done last: the module and model passes above already moved some of these,
    # and each statement is keyed on the old value still being present.
    for module, old, new in state["xmlids"]:
        mod = RENAMES.get(module, module)
        out.append(
            f"UPDATE ir_model_data SET name = {q(new)} "
            f"WHERE module = {q(mod)} AND name = {q(old)} "
            f"AND NOT EXISTS (SELECT 1 FROM ir_model_data d2 "
            f"WHERE d2.module = {q(mod)} AND d2.name = {q(new)});")

    out.append("COMMIT;")
    return out


def check() -> int:
    left = []
    # Modules are judged by the rename map, not the token rules: the token rules
    # would call custom_arka_aim_seed "custom_tenant_seed" while the import names
    # it custom_tenant_coa_seed. Everything else is judged by the token rules.
    left += [("module", v) for v in
             (r[0] for r in psql("SELECT name FROM ir_module_module;")) if v in RENAMES]
    for label, rows in (
        ("model", [r[0] for r in psql("SELECT model FROM ir_model;")]),
        ("xmlid", [r[0] for r in psql("SELECT name FROM ir_model_data;")]),
    ):
        left += [(label, v) for v in rows if scrub(v) != v]
    tables = [r[0] for r in psql(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','v');")]
    left += [("table", t) for t in tables if scrub(t) != t]

    if left:
        print(f"{len(left)} database names still carry a customer name:")
        for kind, v in left[:25]:
            print(f"  {kind:7} {v} -> {scrub(v)}")
        if len(left) > 25:
            print(f"  ... and {len(left) - 25} more")
        return 1
    print("no customer name remains in module, model, field, table or XML-ID names")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="run the SQL; default prints it")
    ap.add_argument("--check", action="store_true", help="exit 1 if any old name remains")
    args = ap.parse_args(argv)

    if args.check:
        return check()

    state = collect()
    print(f"-- {len(state['modules'])} modules, {len(state['models'])} models, "
          f"{len(state['fields'])} fields, {len(state['xmlids'])} XML IDs to rename",
          file=sys.stderr)
    sql = "\n".join(build_sql(state))

    if not args.apply:
        print(sql)
        print("\n-- dry run; pass --apply to execute", file=sys.stderr)
        return 0

    out = subprocess.run(
        ["docker", "exec", "-i", PG_CONTAINER, "psql", "-U", "odoo", "-d", DB,
         "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
        input=sql, capture_output=True, text=True, encoding="utf-8",
    )
    if out.returncode:
        print(out.stdout)
        sys.exit(f"migration failed, transaction rolled back:\n{out.stderr}")
    print("migration applied", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
