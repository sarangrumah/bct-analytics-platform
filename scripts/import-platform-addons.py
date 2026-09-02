#!/usr/bin/env python3
"""
import-platform-addons.py — copy addons from the odoo-platform suite into this repo.

Authorised by docs/adr/0002-addon-import.md. That ADR, not this script, is the
record of what was decided and why; the constants below implement it.

The import is deliberately wave-based. 38 of the 154 source modules add columns to
tables this deployment replicates, and analytics/cdc/bct_cdc/policy.py hard-fails on
an unclassified column. A wave is not finished when the files land -- it is finished
when the classification seed has been regenerated and the loader runs clean.

Usage:
  python scripts/import-platform-addons.py --source E:/Projects/Odoo/platform/addons \
      --wave 0                      # dry run: prints the plan, writes nothing
  python scripts/import-platform-addons.py --source ... --wave 0 --apply
  python scripts/import-platform-addons.py --verify-depends

Waves are slices of the dependency graph by topological layer, so every wave is
dependency-closed on the waves before it.
"""

from __future__ import annotations

import argparse
import ast
import csv
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEST = REPO / "addons"
CATALOG = REPO / "docs" / "module-catalog.csv"

# ADR 0002 section 5 -- not imported at all.
SKIP = {
    "base_rest": "needs OCA `component`, never vendored; installable=False; unreferenced",
    "partner_firstname": "unreferenced; the only AGPL-3 module in the suite",
    "custom_stock_delivery_report_fix": "retired by its own manifest; breaks fresh installs on 19",
    "custom_vertical_example": "scaffold template that advertises itself as an application",
    # ADR 0002 section 3 -- zero dependents, and it defines pdp.masked.mixin a second time.
    "custom_pdp_masking": "collides with this repo's masking; nothing depends on it",
}

# ADR 0002 sections 3 and 4 -- renamed on the way in.
RENAMES = {
    # Collision with this repo's own module. The two are complementary: theirs is a
    # taxonomy of classification codes, ours is the per-column CDC policy.
    "custom_pdp_core": "custom_pdp_taxonomy",
    # Customer identity must not enter the tree.
    "l10n_erajaya": "l10n_id_coa_10d",
    "custom_arka_aim_numbering": "custom_doc_numbering",
    "custom_arka_fx_header": "custom_account_fx_header",
    "custom_levis_sales_dashboard": "custom_retail_sales_dashboard",
    "custom_ops_reports": "custom_asset_ops_reports",
    "custom_ppob_eraspace_bridge": "custom_ppob_pos_bridge",
    # The second pass, after the first import showed the client names were not
    # only in prose: they were in model names, field names and XML IDs, which is
    # a database rename rather than a text substitution. See
    # scripts/migrate-client-renames.py.
    "custom_levis_localization": "custom_retail_localization",
    "custom_levis_categ_approval": "custom_retail_categ_approval",
    "custom_levis_asset_accounts": "custom_retail_asset_accounts",
    "custom_arka_aim_asset_register": "custom_asset_register_seed",
    "custom_arka_aim_opening_balance": "custom_opening_balance_seed",
    "custom_arka_aim_seed": "custom_tenant_coa_seed",
    "custom_arka_show_date": "custom_sale_show_date",
}

# Applied to the CONTENTS and PATHS of every imported module (ADR 0002 section 4).
# Order matters: the specific rules must run before the bare token, or `erajaya.com`
# becomes `id_coa_10d.com` and `ERAJAYA_ASSET_GROUP_SEED` keeps half its name.
#
# The seed brands in custom_project_portfolio are a customer's brand list sitting in
# an otherwise generic module, so they are renamed to the retail segments they stand
# for. Four test files resolve those XML IDs; the same table rewrites them, which is
# why the ids are listed here rather than hand-edited in the data file.
CONTENT_SUBS: list[tuple[str, str]] = [
    # Mail hosts and repositories.
    (r"mail\.erajaya\.com", "mail.example.invalid"),
    (r"[\w.]*@erajaya\.com", "retail.data@example.invalid"),
    (r"\berajaya\.com\b", "example.invalid"),
    (r"erajaya-platform", "example-org"),
    # Legal entities.
    (r"Erajaya\s+Swasembada", 'Example Group'),
    (r"Erajaya\s+Group", 'Example Group'),
    (r"PT\.?\s+Erajaya\s+\w+", 'the group parent company'),
    # Prose. Almost every surviving mention is about the chart of accounts, so a
    # blanket "the group" would read as "the the group chart"; name the chart instead.
    (r"Erajaya\s+Chart\s+of\s+Accounts", '10-Digit Chart of Accounts'),
    (r"an\s+Erajaya[-\s]chart", 'a 10-digit chart'),
    (r"the\s+Erajaya[-\s]chart", 'the 10-digit chart'),
    (r"Erajaya[-\s]chart", '10-digit chart'),
    (r"the\s+Erajaya\s+cost", 'the 10-digit chart cost'),
    (r"Erajaya\s+cost", '10-digit chart cost'),
    (r"the\s+Erajaya\s+revaluation", 'the 10-digit chart revaluation'),
    (r"Erajaya\s+revaluation", '10-digit chart revaluation'),
    (r"Erajaya\s+asset\s+group", '10-digit chart asset group'),
    (r"Erajaya\s+localization", '10-digit chart localization'),
    (r"Erajaya\s+10-digit", '10-digit'),
    (r"any\s+new\s+Erajaya\s+company", 'any new company'),
    (r"EFN\s*\(Erajaya\s*F&amp;B\)", 'F&amp;B'),
    (r"Erajaya[-\s]brand", 'brand'),
    (r"Erajaya\s+Product\s+Owner", 'Product Owner'),
    (r"Erajaya[-\s]style", 'intra-group'),
    (r"Erajaya\s+group\s+pattern", 'intra-group pattern'),
    (r"\"name\": \"Erajaya\"", '"name": "Indonesia 10-Digit"'),
    # This comment named two customers and described the legal_entity fields the
    # rule above removes, so it goes with them.
    (r"<!-- Brand seed\.[\s\S]*?-->",
     "<!-- Brand seed: generic retail segments, not one customer's brand list.\n"
     "             `legal_entity` is left unset; each tenant fills in its own. -->"),
    (r"ERAJAYA_", ''),
    (r"\bErajaya\b", 'the group'),
    (r"(?<![A-Za-z])erajaya(?![A-Za-z])", "id_coa_10d"),
    # ---- The other customer names ------------------------------------------
    # Ordered longest-first. These land in model names (`levis.cogs.run`), field
    # names (`eraspace_txn_id`) and XML IDs, so the same table drives both the
    # source rewrite and scripts/migrate-client-renames.py, which moves an
    # existing database to match.
    #
    # `aim` is only ever rewritten next to `arka` or as an identifier segment.
    # A bare lowercase `aim` is left alone: it is an ordinary English word and a
    # blind rule would rewrite "we aim to" in someone's docstring.
    # CamelCase class names. The letter-bounded rules below cannot see these:
    # `LevisCategReclass` has a letter straight after the name, so the lookahead
    # that protects ordinary prose also blocks the class. Match on the following
    # capital instead, which is what makes it CamelCase in the first place.
    # `aimarka` is the two names fused, and it only ever appears in tenant
    # database names (erp_dev_aimarka, uat_aimarka, rnd_aimarka).
    (r"(?i)aimarka", "tenant"),
    (r"ERAFONE", "MOBILE"),
    (r"ArkaAim", "Tenant"),
    (r"ARKAAIM", "TENANT"),
    (r"VasPmo(?=[A-Z])", "Pmo"),
    (r"Vaspmo(?=[A-Z])", "Pmo"),
    (r"Levis(?=[A-Z])", "Retail"),
    (r"Eraspace(?=[A-Z])", "Pos"),
    (r"Erafone(?=[A-Z])", "Mobile"),
    # UPPERCASE identifier forms come first. `AIM_COMPANY` is a Python constant,
    # and the prose rule below turns it into `the tenant_COMPANY`, which is a
    # SyntaxError. An underscore is not a letter, so the letter-boundary lookahead
    # does not protect it -- the identifier context has to be matched explicitly.
    (r"(?<![A-Za-z])ARKA_", "TENANT_"),
    (r"_ARKA(?![A-Za-z])", "_TENANT"),
    (r"(?<![A-Za-z])AIM_", "TENANT_"),
    (r"_AIM(?![A-Za-z])", "_TENANT"),
    (r"(?<![A-Za-z])LEVIS_", "RETAIL_"),
    (r"_LEVIS(?![A-Za-z])", "_RETAIL"),
    (r"(?<![A-Za-z])ERASPACE_", "POS_"),
    (r"_ERASPACE(?![A-Za-z])", "_POS"),
    (r"(?<![A-Za-z])VASPMO_", "PMO_"),
    (r"_VASPMO(?![A-Za-z])", "_PMO"),
    (r"ARKA[-\s]AIM", "the tenant"),
    (r"arka[-_]aim", "tenant"),
    (r"arkaaim", "tenant"),
    (r"(?<![A-Za-z])ARKA(?![A-Za-z])", "the tenant"),
    (r"(?<![A-Za-z])arka(?![A-Za-z])", "tenant"),
    (r"(?<![A-Za-z])AIM(?![A-Za-z])", "the tenant"),
    (r"(?<![A-Za-z])_aim(?![A-Za-z])", "_tenant"),
    (r"Levi's", "the apparel brand"),
    (r"(?<![A-Za-z])LEVIS(?![A-Za-z])", "RETAIL"),
    (r"(?<![A-Za-z])Levis(?![A-Za-z])", "Retail"),
    (r"(?<![A-Za-z])levis(?![A-Za-z])", "retail"),
    (r"(?<![A-Za-z])ERASPACE(?![A-Za-z])", "POS"),
    (r"(?<![A-Za-z])Eraspace(?![A-Za-z])", "POS"),
    (r"(?<![A-Za-z])eraspace(?![A-Za-z])", "pos"),
    (r"(?<![A-Za-z])Erafone(?![A-Za-z])", "Mobile Retail"),
    (r"(?<![A-Za-z])erafone(?![A-Za-z])", "mobile"),
    (r"(?<![A-Za-z])VasPmo(?![A-Za-z])", "Pmo"),
    (r"(?<![A-Za-z])VASPMO(?![A-Za-z])", "PMO"),
    (r"(?<![A-Za-z])vaspmo(?![A-Za-z])", "pmo"),
    # Test-fixture hygiene. These are HMAC secrets and a login used only inside
    # tests/, but scripts/scan-secrets.py cannot tell a fixture from a leaked
    # credential by looking at the assignment, and it is right not to try. Making
    # the values self-describing keeps the scanner strict instead of teaching it
    # to skip test files, where a real credential could later be pasted.
    (r'"s3cr3t-very-long-key"', '"dummy-hmac-key-for-tests"'),
    (r'"test-secret-please-change"', '"dummy-webhook-secret"'),
    (r'"eraspace-secret"', '"dummy-pos-bridge-secret"'),
    (r'"va-test-secret-BCA"', '"dummy-va-callback-secret"'),
    # Written to match the value AFTER the CamelCase rules above have run: they
    # turn VasPmoTest into PmoTest first. Matching the original here would look
    # right and never fire.
    (r'"PmoTest!2026"', '"dummy-portal-password"'),
    # Brand-vertical seed: customer brands -> the retail segment each one is.
    (r"vertical_levis", "vertical_apparel"),
    (r"vertical_gtw", "vertical_womenswear"),
    (r"vertical_eraspace", "vertical_electronics"),
    (r"vertical_arkaaim", "vertical_aerial"),
    (r"vertical_erafone", "vertical_mobile"),
    (r"vertical_urban", "vertical_lifestyle"),
    (r"vertical_jds", "vertical_warehouse"),
    (r"<field name=\"name\">Levi's</field>", '<field name="name">Apparel Retail</field>'),
    (r"<field name=\"name\">Gentlewoman</field>", '<field name="name">Womenswear</field>'),
    (r"<field name=\"name\">Eraspace</field>", '<field name="name">Electronics Retail</field>'),
    (r"<field name=\"name\">ARKA AIM</field>", '<field name="name">Aerial Services</field>'),
    (r"<field name=\"name\">Erafone</field>", '<field name="name">Mobile Retail</field>'),
    (r"<field name=\"name\">Urban Republic</field>", '<field name="name">Lifestyle Retail</field>'),
    (r"<field name=\"name\">JDS — Warehouse</field>", '<field name="name">Warehouse &amp; Distribution</field>'),
    (r"<field name=\"code\">LEVIS</field>", '<field name="code">APPAREL</field>'),
    (r"<field name=\"code\">GTW</field>", '<field name="code">WOMENSWEAR</field>'),
    (r"<field name=\"code\">ERASPACE</field>", '<field name="code">ELECTRONICS</field>'),
    (r"<field name=\"code\">ARKAAIM</field>", '<field name="code">AERIAL</field>'),
    (r"<field name=\"code\">ERAFONE</field>", '<field name="code">MOBILE</field>'),
    (r"<field name=\"code\">URBAN</field>", '<field name="code">LIFESTYLE</field>'),
    (r"<field name=\"code\">JDS</field>", '<field name="code">WAREHOUSE</field>'),
    # Prose cleanup, LAST. Substituting a client name for a common noun leaves
    # "the ARKA-AIM tenant" reading "the the tenant tenant". Collapsing the
    # duplicates afterwards is simpler, and far less brittle, than trying to make
    # every substitution above agree with the article and noun around it.
    (r"\bthe the\b", "the"),
    (r"\bThe the\b", "The"),
    (r"\btenant tenant\b", "tenant"),
    (r"\bthe tenant drone\b", "the drone"),
    (r"\bthe tenant Inventory\b", "the Inventory"),
    # legal_entity named a real company; an empty cell is honest, a guess is not.
    (r"[ \t]*<field name=\"legal_entity\">[^<]*</field>\n", ""),
]

# Duplicate payloads removed on import (ADR 0002 section 3, catalogue P0 findings).
# Each entry drops data files that another module already provides and adds the
# dependency that provides them, so the data exists exactly once.
DEDUPE: dict[str, dict] = {
    "custom_arka_aim_seed": {
        "reason": (
            "its 548-account chart and 40 taxes duplicate l10n_id_coa_10d: 534 codes "
            "are byte-identical and the other 14 are the bank/cash accounts Odoo "
            "creates from the code prefixes. The tax file is a strict subset too "
            "(40 of 78). Only the fiscal positions and the post-init wiring are its own."
        ),
        "drop_data": [
            "data/account.account.csv",
            "data/account.tax.csv",
            "data/account.tax.group.csv",
        ],
        "add_depends": ["l10n_id_coa_10d"],
    },
}


def apply_dedupe(dest: Path, name: str) -> list[str]:
    """Drop duplicated data files and add the dependency that supplies them."""
    spec = DEDUPE.get(name)
    if not spec:
        return []
    notes = []
    manifest = dest / "__manifest__.py"
    text = manifest.read_text(encoding="utf-8")
    for rel in spec["drop_data"]:
        target = dest / rel
        if target.exists():
            target.unlink()
        # Remove the manifest entry too, or the install fails on a missing file.
        text = re.sub(rf'^\s*["\']{re.escape(rel)}["\'],?\s*\n', "", text, flags=re.M)
        notes.append(f"dropped {rel}")
    for dep in spec["add_depends"]:
        if f'"{dep}"' not in text and f"'{dep}'" not in text:
            text = re.sub(r'("depends"\s*:\s*\[)', rf'\1\n        "{dep}",', text, count=1)
            notes.append(f"added depends on {dep}")
    manifest.write_text(text, encoding="utf-8", newline="")
    return notes


# Waves are dependency-closed slices of the topological layer graph, NOT tiers.
# Tier is a packaging concept: addons/core/ holds modules at layers 0 through 4
# (custom_bast sits in core/ but depends on compliance/custom_pdp_audit). Importing
# by tier leaves dangling dependencies; importing by layer never does.
WAVES: dict[int, tuple[str, range]] = {
    0: ("foundation -- custom_core and the modules that need nothing else", range(0, 2)),
    1: ("the compliance and adapter layer that most of the suite sits on", range(2, 4)),
    2: ("the bulk of ee_gap -- accounting, WMS, HR, marketing", range(4, 5)),
    3: ("modules built on that bulk", range(5, 6)),
    4: ("the top of the graph -- control plane, verticals, tenant modules", range(6, 99)),
}


def load_catalog() -> dict[str, dict]:
    if not CATALOG.exists():
        sys.exit(f"catalogue not found: {CATALOG}\nRun tools/module_inventory.py first.")
    with CATALOG.open(encoding="utf-8", newline="") as fh:
        return {r["module"]: r for r in csv.DictReader(fh)}


def wave_members(wave: int, catalog: dict[str, dict]) -> list[dict]:
    if wave not in WAVES:
        sys.exit(f"unknown wave {wave}; valid: {sorted(WAVES)}")
    layers = WAVES[wave][1]
    return sorted((r for r in catalog.values() if int(r["layer"]) in layers),
                  key=lambda r: (int(r["layer"]), r["module"]))


def _rename_tokens(text: str) -> str:
    """Module names appear in depends lists, `odoo.addons.<mod>` imports, XML-ID
    prefixes (`<mod>.view_x`) and asset paths (`/<mod>/static/...`). A letter-or-digit
    boundary catches all of those without touching `custom_pdp_core_extra`."""
    for old, new in RENAMES.items():
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)
    return text


def _scrub_client(text: str) -> str:
    for pat, repl in CONTENT_SUBS:
        text = re.sub(pat, repl, text)
    return text


def import_module(src: Path, name: str, tier: str, apply: bool) -> tuple[Path, int]:
    """Copy one module, applying renames to paths and contents."""
    new_name = RENAMES.get(name, name)
    dest = DEST / tier / new_name
    # The scrub runs on every module. Customer identity turned up in modules that
    # are otherwise generic -- a mail host in custom_retail_import, a legal entity in
    # custom_project_portfolio's brand seed, an ERAJAYA_ constant in a _tenants
    # module -- so restricting it to renamed modules left the name in the tree.

    if not apply:
        return dest, sum(1 for p in src.rglob("*") if p.is_file())

    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    text_suffixes = {".py", ".xml", ".csv", ".js", ".scss", ".md", ".txt", ".cfg",
                     ".po", ".pot", ".json", ".yml", ".yaml", ".sql"}
    count = 0
    for path in sorted(src.rglob("*")):
        if any(p in ("__pycache__", ".git", "node_modules") for p in path.parts):
            continue
        # Chart-template CSVs are named <model>-<template_code>.csv and Odoo resolves
        # the template by that suffix, so renaming the filename is required, not
        # cosmetic.
        rel_str = str(path.relative_to(src))
        rel_str = _scrub_client(rel_str)
        rel_str = _rename_tokens(rel_str)
        target = dest / rel_str

        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix in text_suffixes:
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                shutil.copy2(path, target)
                count += 1
                continue
            text = _scrub_client(_rename_tokens(text))
            # newline="" keeps LF as LF. Without it, Python on Windows writes
            # CRLF and every imported file trips mixed-line-ending in pre-commit.
            with target.open("w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        else:
            shutil.copy2(path, target)
        count += 1
    return dest, count


def verify_python() -> int:
    """Every imported .py must still compile.

    The scrub rewrites identifiers, not just prose, so a rule that is right for a
    sentence can be wrong for a constant: `AIM_COMPANY` became `the tenant_COMPANY`
    and only surfaced when Odoo imported the module. Compiling the tree turns that
    into a fast, local failure.
    """
    bad = []
    for py in sorted(DEST.rglob("*.py")):
        try:
            ast.parse(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            bad.append(f"{py.relative_to(DEST)}: {exc}")
    for b in bad:
        print("  " + b)
    n = sum(1 for _ in DEST.rglob("*.py"))
    print(f"{n} Python files, {len(bad)} that do not compile")
    return 1 if bad else 0


def verify_depends() -> int:
    """Every declared dependency must resolve to a module present here or to an Odoo CE
    module. A dangling dependency fails the install, so this runs before every wave."""
    present = {m.parent.name for m in DEST.rglob("__manifest__.py")}
    problems: list[str] = []
    for man in sorted(DEST.rglob("__manifest__.py")):
        try:
            data = ast.literal_eval(man.read_text(encoding="utf-8"))
        except (ValueError, SyntaxError) as exc:
            problems.append(f"{man.parent.name}: unparseable manifest ({exc})")
            continue
        for dep in data.get("depends", []):
            if dep in present:
                continue
            if dep in SKIP:
                problems.append(f"{man.parent.name} -> depends on SKIPPED {dep}")
            elif dep.startswith(("custom_", "l10n_id_coa", "auth_jwt", "queue_job")):
                problems.append(f"{man.parent.name} -> depends on MISSING {dep}")
    for p in problems:
        print("  " + p)
    print(f"{len(present)} modules present, {len(problems)} dependency problems")
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", help="odoo-platform addons root")
    ap.add_argument("--wave", type=int, help="which wave to import")
    ap.add_argument("--apply", action="store_true", help="actually write; default is a dry run")
    ap.add_argument("--verify-depends", action="store_true",
                    help="check every declared dependency resolves, then exit")
    ap.add_argument("--verify-python", action="store_true",
                    help="check every imported .py still compiles, then exit")
    args = ap.parse_args(argv)

    if args.verify_python:
        return verify_python()
    if args.verify_depends:
        return verify_depends()
    if args.wave is None or not args.source:
        ap.error("--wave and --source are required unless --verify-depends is given")

    source = Path(args.source)
    catalog = load_catalog()
    members = wave_members(args.wave, catalog)

    print(f"Wave {args.wave} -- {WAVES[args.wave][0]} (layers {WAVES[args.wave][1].start}..{min(WAVES[args.wave][1].stop - 1, 9)})")
    print("APPLYING" if args.apply else "DRY RUN (pass --apply to write)")
    print()

    imported = skipped = files = cdc_cols = 0
    for row in members:
        name, tier = row["module"], row["tier"]
        if name in SKIP:
            print(f"     skip   {name:38} {SKIP[name]}")
            skipped += 1
            continue
        src = source / tier / name
        if not src.is_dir():
            found = [p for p in source.glob(f"{tier}/**/{name}") if p.is_dir()]
            if not found:
                print(f"  MISSING {name:38} not found under {tier}/")
                continue
            src = found[0]
        dest, n = import_module(src, name, tier, args.apply)
        if args.apply:
            for note in apply_dedupe(dest, name):
                print(f"          dedupe: {note}")
        note = f"-> {RENAMES[name]} " if name in RENAMES else ""
        cdc = int(row.get("cdc_impact_cols") or 0)
        cdc_cols += cdc
        cdcnote = f"[+{cdc} replicated cols]" if cdc else ""
        print(f"  L{row['layer']} import {name:38} {tier}/ {note}{cdcnote}")
        imported += 1
        files += n

    print(f"\n{imported} modules ({files} files), {skipped} skipped.")
    if cdc_cols:
        print(f"\nThis wave adds {cdc_cols} columns to replicated tables. The CDC loader "
              f"will hard-fail\nuntil they are classified:")
        print("  python addons/custom_pdp_core/tools/generate_classification_seed.py")
        print("  make up-analytics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
