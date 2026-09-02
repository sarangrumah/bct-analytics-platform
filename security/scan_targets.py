#!/usr/bin/env python3
"""Read security/scan-targets.yml and answer three questions about it.

    python3 security/scan_targets.py --check            # is anything shipping unscanned?
    python3 security/scan_targets.py --matrix images    # GitHub Actions matrix JSON
    python3 security/scan_targets.py --matrix node      # GitHub Actions matrix JSON
    python3 security/scan_targets.py --summary          # Markdown for the job summary
    python3 security/scan_targets.py --list             # human-readable table
    python3 security/scan_targets.py --selftest         # parser cross-check against PyYAML

Why this exists
---------------
CI must scan every image and every Node project this repository builds, including ones
that do not exist yet, and it must be a one-line change to add another. Encoding that as
a literal matrix inside ci.yml means five agents need write access to a file exactly one
agent owns (master prompt 2.1). So the matrices are generated from the registry instead,
and the registry is the thing other agents ask the Lead to extend.

Zero runtime dependencies - deliberately
----------------------------------------
No PyYAML, no jq (absent on the operator host, and no tool here may need it). The registry
uses a restricted YAML subset and is parsed by the strict reader below, which raises on
anything it does not understand rather than guessing. `--selftest` proves that reader
agrees with PyYAML where PyYAML happens to be installed; it is a test, not a code path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGISTRY = os.path.join(REPO_ROOT, "security", "scan-targets.yml")

OK, WARN, FAIL = "ok", "warning", "failure"


# ----------------------------------------------------------------------------------
# A strict reader for the registry's restricted YAML subset.
#
# Supported, and nothing else:
#   key: scalar
#   key:                      -> list of mappings
#     - first: scalar
#       second: scalar
#       third: >-             -> folded block scalar
#         wrapped text
# Comments and blank lines are ignored. Anything outside this grammar is a hard error:
# a security registry that silently mis-parses is worse than one that will not load.
# ----------------------------------------------------------------------------------
class RegistryError(Exception):
    pass


def _scalar(raw: str, where: int):
    raw = raw.strip()
    if raw and raw[0] in "\"'":
        if len(raw) < 2 or raw[-1] != raw[0]:
            raise RegistryError(f"line {where}: unterminated quoted string: {raw}")
        return raw[1:-1]
    # Strip a trailing comment only when it is clearly one (preceded by whitespace).
    hash_at = raw.find(" #")
    if hash_at != -1:
        raw = raw[:hash_at].rstrip()
    if raw in ("true", "false"):
        return raw == "true"
    if raw == "null" or raw == "~":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw


def load_registry(path: str = REGISTRY) -> dict:
    with open(path, encoding="utf-8") as handle:
        lines = handle.read().splitlines()

    doc: dict = {}
    current_list: list | None = None
    current_item: dict | None = None
    folding_key: str | None = None
    folding_into: dict | None = None
    folding_parts: list[str] = []
    folding_indent = 0

    def close_fold():
        nonlocal folding_key, folding_into, folding_parts
        if folding_key is not None and folding_into is not None:
            folding_into[folding_key] = " ".join(p.strip() for p in folding_parts).strip()
        folding_key, folding_into, folding_parts = None, None, []

    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(" "))

        if folding_key is not None:
            if stripped and indent > folding_indent and not stripped.startswith("#"):
                folding_parts.append(stripped)
                continue
            close_fold()

        if not stripped or stripped.startswith("#"):
            continue

        if line.startswith(" ") is False and ":" in stripped and not stripped.startswith("- "):
            # Top level: `key: value` or `key:` introducing a list.
            key, _, rest = stripped.partition(":")
            key = key.strip()
            rest = rest.strip()
            current_item = None
            if rest == "":
                current_list = []
                doc[key] = current_list
            else:
                current_list = None
                doc[key] = _scalar(rest, number)
            continue

        if stripped.startswith("- "):
            if current_list is None:
                raise RegistryError(f"line {number}: list item outside any list: {stripped}")
            body = stripped[2:]
            if ":" not in body:
                raise RegistryError(f"line {number}: list item is not a mapping: {stripped}")
            key, _, rest = body.partition(":")
            current_item = {}
            current_list.append(current_item)
            rest = rest.strip()
            if rest in (">-", ">", "|", "|-"):
                folding_key, folding_into, folding_parts, folding_indent = key.strip(), current_item, [], indent
            else:
                current_item[key.strip()] = _scalar(rest, number)
            continue

        if current_item is not None and ":" in stripped:
            key, _, rest = stripped.partition(":")
            rest = rest.strip()
            if rest in (">-", ">", "|", "|-"):
                folding_key, folding_into, folding_parts, folding_indent = key.strip(), current_item, [], indent
            else:
                current_item[key.strip()] = _scalar(rest, number)
            continue

        raise RegistryError(f"line {number}: not understood by the strict reader: {stripped!r}")

    close_fold()
    return doc


# ----------------------------------------------------------------------------------
# Filesystem sweep
# ----------------------------------------------------------------------------------
def _exclusions(doc: dict) -> list[str]:
    return [str(e["path"]) for e in doc.get("coverage_exclusions", []) if "path" in e]


def _skip(rel: str, exclusions: list[str]) -> bool:
    parts = rel.replace("\\", "/").split("/")
    for excl in exclusions:
        excl_parts = excl.split("/")
        if excl_parts[0] in parts:
            return True
        if rel.replace("\\", "/").startswith(excl + "/") or rel.replace("\\", "/") == excl:
            return True
    return False


def _git_tracked_or_untracked() -> list[str] | None:
    """Every path a fresh clone would contain, plus untracked files not yet ignored.

    ``git ls-files --cached --others --exclude-standard`` is a POSITIVE enumeration and
    deliberately not ``git check-ignore``: check-ignore exits 0 on a NEGATION match too,
    so a guard built on its exit code passes even when a negation made a file visible
    (PLAN.md defect instance 3, found by Backend). This asks git what is in the set
    rather than asking whether one path is out of it.

    Why the set and not ``os.walk``: os.walk sees this developer's disk, which on
    2026-08-31 contained four ``insight-portal/.next/**/package.json`` files -- Next.js
    build output, gitignored, never in a clone, never built by CI. The sweep failed on
    all four. A coverage gate that fires on artefacts no consumer can see is a gate
    people learn to re-run until it passes, and the whole value of this check is that a
    failure means something.

    Untracked-but-not-ignored files stay IN the set on purpose. PLAN.md instance 9 was a
    dbt model that existed on disk and was never ``git add``ed; the mirror of that is a
    Dockerfile someone writes and forgets to add, which should still be registered.

    A gitignored Dockerfile is invisible here. That is the correct boundary, not a hole:
    CI builds from the clone, so a file no clone contains cannot ship an image.

    Returns ``None`` when git is unavailable or the repo is not a work tree, so the
    caller can fall back and say so.
    """
    try:
        out = subprocess.run(
            ["git", "-C", REPO_ROOT, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, check=True, timeout=60,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return None
    paths = [p.replace("\\", "/") for p in out.split(chr(0)) if p]
    return paths or None


def find_files(exclusions: list[str]) -> tuple[list[str], list[str], str]:
    """Return (dockerfiles, package.json files, the name of the source used)."""
    paths = _git_tracked_or_untracked()
    source = "git"
    if paths is None:
        source = "os.walk"
        paths = []
        for root, dirs, files in os.walk(REPO_ROOT):
            rel_root = os.path.relpath(root, REPO_ROOT).replace("\\", "/")
            if rel_root == ".":
                rel_root = ""
            dirs[:] = [d for d in dirs if not _skip(f"{rel_root}/{d}".lstrip("/"), exclusions)]
            paths += [f"{rel_root}/{n}".lstrip("/") for n in files]

    dockerfiles, packages = [], []
    for rel in paths:
        if _skip(rel, exclusions):
            continue
        name = rel.rsplit("/", 1)[-1].lower()
        if name == "dockerfile" or name.startswith("dockerfile."):
            dockerfiles.append(rel)
        elif name == "package.json":
            packages.append(rel)
    return sorted(dockerfiles), sorted(packages), source


def resolve(doc: dict) -> dict:
    """Attach live filesystem state to every registered target."""
    exclusions = _exclusions(doc)
    dockerfiles, packages, source = find_files(exclusions)

    images = []
    for entry in doc.get("images", []):
        path = str(entry.get("dockerfile", ""))
        exists = os.path.isfile(os.path.join(REPO_ROOT, path))
        images.append({**entry, "exists": exists, "scan": exists})

    node = []
    for entry in doc.get("node_projects", []):
        path = os.path.join(str(entry.get("path", "")), "package.json")
        exists = os.path.isfile(os.path.join(REPO_ROOT, path))
        node.append({**entry, "manifest": path.replace("\\", "/"), "exists": exists, "scan": exists})

    python = []
    for entry in doc.get("python_projects", []):
        base = os.path.join(REPO_ROOT, str(entry.get("path", "")))
        found = [
            n for n in ("requirements.txt", "requirements.in", "pyproject.toml", "constraints.txt")
            if os.path.isfile(os.path.join(base, n))
        ]
        python.append({**entry, "manifests": found, "exists": bool(found), "scan": bool(found)})

    registered_dockerfiles = {str(i.get("dockerfile", "")) for i in doc.get("images", [])}
    registered_packages = {f"{str(n.get('path',''))}/package.json" for n in doc.get("node_projects", [])}

    fixtures = []
    for entry in doc.get("ci_fixtures", []):
        path = str(entry.get("path", ""))
        full = os.path.join(REPO_ROOT, path)
        fixtures.append({
            **entry,
            "exists": os.path.exists(full),
            "is_file": os.path.isfile(full),
        })

    return {
        "images": images,
        "node": node,
        "python": python,
        "fixtures": fixtures,
        "scan_source": source,
        "found_dockerfiles": dockerfiles,
        "found_packages": packages,
        "unregistered_dockerfiles": [d for d in dockerfiles if d not in registered_dockerfiles],
        "unregistered_packages": [p for p in packages if p not in registered_packages],
    }


def check(state: dict) -> tuple[str, list[str]]:
    problems, warnings = [], []

    # THE EMPTY-RESULT ASSERTION (PLAN.md, binding rule).
    # Everything below concludes "nothing is unregistered" from an empty difference. An
    # empty difference is also what a broken sweep produces, and the two are
    # indistinguishable. So assert the population first: this repository builds images,
    # and a sweep that found none did not find a clean tree, it found nothing.
    if not state["found_dockerfiles"]:
        problems.append(
            f"EMPTY SUBJECT SET: the {state['scan_source']} sweep found no Dockerfile anywhere. "
            f"Every 'unregistered' check below searched an empty population, so all of them "
            f"passed vacuously. This is a broken enumeration, not a clean repository."
        )

    for path in state["unregistered_dockerfiles"]:
        problems.append(
            f"UNREGISTERED IMAGE: {path} builds an image that no scan job covers. "
            f"Add it to security/scan-targets.yml (master prompt 5.2: no new image ships unscanned)."
        )
    for path in state["unregistered_packages"]:
        problems.append(
            f"UNREGISTERED NODE PROJECT: {path} has dependencies that npm audit and OSV-Scanner "
            f"never see. Add it to node_projects in security/scan-targets.yml."
        )
    for image in state["images"]:
        declared, exists = image.get("status"), image["exists"]
        if declared == "present" and not exists:
            problems.append(
                f"BROKEN REGISTRATION: image '{image['name']}' is registered present but "
                f"{image.get('dockerfile')} does not exist. Fix the path, or remove the entry."
            )
        elif declared == "pending" and exists:
            warnings.append(
                f"REGISTRY DRIFT: image '{image['name']}' has landed ({image.get('dockerfile')}) but is "
                f"still marked pending. It IS being scanned; flip status to present in "
                f"security/scan-targets.yml. Owner: {image.get('owner')}."
            )
    for project in state["node"]:
        if project.get("status") == "present" and not project["exists"]:
            problems.append(
                f"BROKEN REGISTRATION: node project '{project['name']}' is registered present but "
                f"{project['manifest']} does not exist."
            )
        elif project.get("status") == "pending" and project["exists"]:
            warnings.append(
                f"REGISTRY DRIFT: node project '{project['name']}' has landed but is still marked pending. "
                f"It IS being scanned; flip status to present. Owner: {project.get('owner')}."
            )

    # python_projects were resolved but never checked until 2026-08-31 - the section
    # existed, the drift and broken-registration rules did not apply to it, so a Python
    # service that lost its requirements.txt (and with it its pip-audit coverage) was
    # reported nowhere. sca-python reads `git ls-files`, not this registry, so nothing
    # else would have noticed either.
    for project in state["python"]:
        declared, exists = project.get("status"), project["exists"]
        if declared == "present" and not exists:
            problems.append(
                f"BROKEN REGISTRATION: python project '{project['name']}' is registered present "
                f"but {project.get('path')} holds no requirements.txt / requirements.in / "
                f"pyproject.toml / constraints.txt, so pip-audit sees nothing for it."
            )
        elif declared == "pending" and exists:
            warnings.append(
                f"REGISTRY DRIFT: python project '{project['name']}' has landed "
                f"({', '.join(project['manifests'])}) but is still marked pending. Flip status "
                f"to present. Owner: {project.get('owner')}."
            )

    # ci_fixtures: the expiry control for a declared skip inside a workflow.
    #
    # A `run:` step has exactly two outcomes, so a skip cannot be given an exit code
    # distinct from a pass (PLAN.md instance 11). ci.yml's dbt-ci tier 3 legitimately
    # skips while its fixture is undelivered - but nothing made that skip EXPIRE, so a
    # fixture that landed and later moved or was deleted would return the job to a
    # silent, permanent, green skip. That control has to live outside the step. It lives
    # here, in `discover`, which every CI run executes and `ci-gate` requires.
    for fx in state["fixtures"]:
        declared = fx.get("status")
        if declared == "present" and not fx["exists"]:
            problems.append(
                f"BROKEN REGISTRATION: CI fixture '{fx['name']}' is registered present but "
                f"{fx.get('path')} does not exist. {fx.get('consumer', 'Its consumer job')} is "
                f"now skipping silently and green. Restore the file, or deregister it and accept "
                f"that the job no longer runs."
            )
        elif declared == "present" and not fx["is_file"]:
            problems.append(
                f"BROKEN REGISTRATION: CI fixture '{fx['name']}' at {fx.get('path')} exists but "
                f"is not a regular file."
            )
        elif declared == "pending" and fx["exists"]:
            warnings.append(
                f"REGISTRY DRIFT: CI fixture '{fx['name']}' has landed ({fx.get('path')}) but is "
                f"still marked pending. {fx.get('consumer', 'Its consumer job')} IS now running it; "
                f"flip status to present so a later disappearance becomes a failure instead of a "
                f"silent skip. Owner: {fx.get('owner')}."
            )

    if problems:
        return FAIL, problems + warnings
    if warnings:
        return WARN, warnings
    return OK, []


# ----------------------------------------------------------------------------------
# Output modes
# ----------------------------------------------------------------------------------
def matrix_images(state: dict) -> str:
    include = [
        {
            "name": i["name"],
            "slug": str(i["name"]).replace("/", "-"),
            "dockerfile": i.get("dockerfile", ""),
            "context": i.get("context", "."),
            "owner": i.get("owner", "unassigned"),
            "wave": i.get("wave", 0),
            "status": i.get("status", "pending"),
            "scan": bool(i["scan"]),
        }
        for i in state["images"]
    ]
    return json.dumps({"include": include}, separators=(",", ":"))


def matrix_node(state: dict) -> str:
    include = [
        {
            "name": n["name"],
            "slug": str(n["name"]).replace("/", "-"),
            "path": n.get("path", ""),
            "owner": n.get("owner", "unassigned"),
            "wave": n.get("wave", 0),
            "status": n.get("status", "pending"),
            "scan": bool(n["scan"]),
        }
        for n in state["node"]
    ]
    return json.dumps({"include": include}, separators=(",", ":"))


def summary(state: dict, verdict: str, messages: list[str]) -> str:
    out = ["## Scan coverage", "", "Source of truth: `security/scan-targets.yml`.", ""]
    out += ["| Image | Dockerfile | Owner | Wave | Registered | This run |", "|---|---|---|---|---|---|"]
    for i in state["images"]:
        action = "SCANNED" if i["scan"] else "**SKIPPED - not built yet**"
        out.append(
            f"| `{i['name']}` | `{i.get('dockerfile')}` | {i.get('owner')} | {i.get('wave')} "
            f"| {i.get('status')} | {action} |"
        )
    out += ["", "| Node project | Path | Owner | Wave | Registered | This run |", "|---|---|---|---|---|---|"]
    for n in state["node"]:
        action = "SCANNED" if n["scan"] else "**SKIPPED - not built yet**"
        out.append(
            f"| `{n['name']}` | `{n.get('path')}` | {n.get('owner')} | {n.get('wave')} "
            f"| {n.get('status')} | {action} |"
        )
    out += ["", "| Python project | Path | Owner | Wave | Registered | Manifests |", "|---|---|---|---|---|---|"]
    for y in state["python"]:
        found = ", ".join(f"`{m}`" for m in y["manifests"]) or "**none - not present yet**"
        out.append(
            f"| `{y['name']}` | `{y.get('path')}` | {y.get('owner')} | {y.get('wave')} "
            f"| {y.get('status')} | {found} |"
        )
    if state["fixtures"]:
        out += ["", "| CI fixture | Path | Consumer | Registered | On disk |", "|---|---|---|---|---|"]
        for f in state["fixtures"]:
            out.append(
                f"| `{f['name']}` | `{f.get('path')}` | {f.get('consumer')} "
                f"| {f.get('status')} | {'yes' if f['exists'] else '**no - consumer job is skipping**'} |"
            )
    out += [
        "",
        f"Enumeration source: `{state['scan_source']}` "
        "(what a fresh clone contains, plus untracked files git does not ignore - "
        "never this developer's build output).",
        f"Dockerfiles found: {len(state['found_dockerfiles'])} - "
        f"unregistered: {len(state['unregistered_dockerfiles'])}",
        f"package.json found: {len(state['found_packages'])} - "
        f"unregistered: {len(state['unregistered_packages'])}",
        "",
        f"**Coverage verdict: {verdict.upper()}**",
    ]
    if messages:
        out += [""] + [f"- {m}" for m in messages]
    out += [
        "",
        "A SKIPPED row is a target that is registered and will be scanned the moment its "
        "Dockerfile or package.json lands. It is reported here on purpose: an unscanned "
        "artefact must be visible, never merely absent.",
    ]
    return "\n".join(out)


def selftest() -> int:
    doc = load_registry()
    try:
        import yaml  # noqa: PLC0415 - test-only import, never on the runtime path
    except ImportError:
        print("SELFTEST SKIPPED: PyYAML not installed (the strict reader does not need it)")
        print(f"strict reader parsed: {len(doc.get('images', []))} images, "
              f"{len(doc.get('node_projects', []))} node projects")
        return 0
    with open(REGISTRY, encoding="utf-8") as handle:
        reference = yaml.safe_load(handle)
    mismatches = []
    for section in ("schema_version", "images", "node_projects", "python_projects",
                    "ci_fixtures", "coverage_exclusions"):
        if doc.get(section) != reference.get(section):
            mismatches.append(section)
            print(f"  MISMATCH in {section}:")
            print(f"    strict : {doc.get(section)}")
            print(f"    pyyaml : {reference.get(section)}")
    if mismatches:
        print(f"SELFTEST FAILED: {mismatches}")
        return 1
    print("SELFTEST OK: strict reader output is identical to PyYAML for every section")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="fail if anything ships unscanned")
    parser.add_argument("--matrix", choices=["images", "node"], help="emit GitHub Actions matrix JSON")
    parser.add_argument("--summary", action="store_true", help="emit Markdown job summary")
    parser.add_argument("--list", action="store_true", help="human-readable listing")
    parser.add_argument("--selftest", action="store_true", help="cross-check the strict reader against PyYAML")
    parser.add_argument("--strict-drift", action="store_true", help="treat registry drift as a failure too")
    args = parser.parse_args()

    if args.selftest:
        return selftest()

    try:
        doc = load_registry()
    except (OSError, RegistryError) as exc:
        print(f"FATAL: cannot read {REGISTRY}: {exc}", file=sys.stderr)
        return 2

    state = resolve(doc)
    verdict, messages = check(state)

    if args.matrix == "images":
        print(matrix_images(state))
        return 0
    if args.matrix == "node":
        print(matrix_node(state))
        return 0
    if args.summary:
        print(summary(state, verdict, messages))
        return 0

    if args.list or not args.check:
        for image in state["images"]:
            mark = "scan" if image["scan"] else "SKIP"
            print(f"  [{mark}] image {image['name']:<16} {image.get('dockerfile'):<40} "
                  f"owner={image.get('owner')} wave={image.get('wave')} status={image.get('status')}")
        for project in state["node"]:
            mark = "scan" if project["scan"] else "SKIP"
            print(f"  [{mark}] node  {project['name']:<16} {project['manifest']:<40} "
                  f"owner={project.get('owner')} wave={project.get('wave')} status={project.get('status')}")
        if not args.check:
            return 0

    for message in messages:
        stream = sys.stderr if verdict == FAIL else sys.stdout
        print(("ERROR: " if verdict == FAIL else "WARN:  ") + message, file=stream)

    if verdict == FAIL:
        print("SCAN_COVERAGE_FAIL", file=sys.stderr)
        return 1
    if verdict == WARN and args.strict_drift:
        print("SCAN_COVERAGE_DRIFT", file=sys.stderr)
        return 1
    print("SCAN_COVERAGE_OK" if verdict == OK else "SCAN_COVERAGE_OK_WITH_DRIFT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
