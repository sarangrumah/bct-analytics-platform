"""Verify from a clone of the branch, never from the working tree.

This is PLAN.md's standing rule, and it exists because of a defect that was invisible to everything
else the project runs: an unanchored ``data/`` pattern in ``.gitignore`` silently excluded three
install-critical files, including the entire 724-row classification seed. Every module's
``__manifest__.py`` declared them, so a fresh clone could not install those modules at all -- while
every test passed, because the tests ran against a working tree where the files exist on disk.

``git status`` shows nothing. The working tree keeps working. CI on a warm checkout is fine. The
bug appears only on a clean clone, which is exactly what "brings up a clean stack from a fresh
clone" promises.

So: clone the branch into a temporary directory and assert, **against the clone**, that every file
any manifest or dbt project declares is actually present. The clone is removed afterwards.
"""

from __future__ import annotations

import ast
import pathlib
import shutil
import subprocess
import tempfile

import pytest

from helpers import env

pytestmark = []

BRANCH = "feat/analytics-platform"


@pytest.fixture(scope="module")
def clone():
    target = pathlib.Path(tempfile.mkdtemp(prefix="bct-clone-"))
    destination = target / "repo"
    result = subprocess.run(
        ["git", "clone", "--branch", BRANCH, "--single-branch",
         str(env.repo_root()), str(destination)],
        capture_output=True, text=True, timeout=600,
    )
    if result.returncode != 0:
        shutil.rmtree(target, ignore_errors=True)
        pytest.fail("git clone of %s failed:\n%s" % (BRANCH, result.stderr))
    yield destination
    shutil.rmtree(target, ignore_errors=True)


def _manifest_data_files(manifest_path: pathlib.Path):
    """Read `data`, `demo` and `assets` file lists out of a manifest without importing it."""
    text = manifest_path.read_text(encoding="utf-8")
    try:
        manifest = ast.literal_eval(text)
    except (ValueError, SyntaxError) as exc:
        pytest.fail("cannot parse %s: %s" % (manifest_path, exc))
    declared = []
    for key in ("data", "demo", "qweb"):
        declared.extend(manifest.get(key) or [])
    for bundle in (manifest.get("assets") or {}).values():
        declared.extend(bundle if isinstance(bundle, list) else [])
    return manifest, declared


def test_the_clone_contains_every_file_the_manifests_declare(clone, evidence):
    addons = clone / "addons"
    assert addons.is_dir(), "the clone has no addons/ directory at all"

    report, missing = [], []
    modules = sorted(p for p in addons.iterdir() if (p / "__manifest__.py").exists())
    assert modules, "no module with a __manifest__.py in the clone"

    for module in modules:
        manifest, declared = _manifest_data_files(module / "__manifest__.py")
        absent = [f for f in declared
                  if not f.startswith(("/", "http")) and not (module / f).exists()]
        report.append("%-26s %2d declared, %d missing" % (module.name, len(declared), len(absent)))
        for f in absent:
            missing.append("%s declares %s, which is NOT in the clone" % (module.name, f))
    evidence.add("modules in the clone of %s" % BRANCH, "\n".join(report))
    assert not missing, "\n".join(missing)


def test_the_clone_contains_the_analytics_and_warehouse_inputs(clone, evidence):
    """Files nothing imports but everything depends on: SQL init, dbt project, alert rules."""
    required = [
        "analytics/warehouse/init/sql/20-schemas-roles.sql",
        "analytics/warehouse/init/sql/30-metadata.sql",
        "analytics/warehouse/init/sql/40-grants.sql",
        "analytics/dbt/dbt_project.yml",
        "analytics/dbt/profiles.yml",
        "observability/prometheus/rules/platform.rules.yml",
        "compose/odoo.yml",
        "compose/insight.yml",
        "compose/platform.yml",
        "Makefile",
        ".env.example",
        "tests/run.sh",
        "tests/prometheus/slot_lag_alerts_test.yml",
    ]
    present, absent = [], []
    for path in required:
        (present if (clone / path).exists() else absent).append(path)
    evidence.add(
        "present in the clone", "\n".join(present) or "(none)"
    )
    evidence.add("MISSING from the clone", "\n".join(absent) or "none")
    assert not absent, (
        "these files exist in the working tree but are not in a fresh clone, so a clean checkout "
        "cannot bring the stack up: %r" % absent
    )


def test_no_secret_material_is_in_the_clone(clone, evidence):
    """The signing key and `.env` must be absent from a clone; `.env.example` must be present."""
    must_be_absent = [
        ".env",
        "login-gateway/secrets/jwt-private.pem",
        "login-gateway/secrets/jwt-next-private.pem",
    ]
    leaked = [p for p in must_be_absent if (clone / p).exists()]
    evidence.add(
        "secret-bearing paths in the clone",
        "\n".join("%s  %s" % ("PRESENT" if (clone / p).exists() else "absent", p)
                  for p in must_be_absent),
    )
    assert not leaked, "committed secret material: %r" % leaked
    assert (clone / ".env.example").exists(), ".env.example is missing from the clone"


def test_dbt_models_referenced_by_the_project_are_in_the_clone(clone, evidence):
    """A model directory that exists locally but is gitignored would fail `dbt build` on a clone."""
    models = clone / "analytics" / "dbt" / "models"
    local_models = env.repo_root() / "analytics" / "dbt" / "models"
    if not local_models.exists():
        pytest.skip("analytics/dbt/models does not exist in the working tree either (NOT RUN)")
    local = sorted(p.relative_to(local_models).as_posix()
                   for p in local_models.rglob("*") if p.is_file())
    cloned = sorted(p.relative_to(models).as_posix()
                    for p in models.rglob("*") if p.is_file()) if models.exists() else []
    absent = [f for f in local if f not in cloned]
    evidence.add(
        "dbt model files: working tree vs clone",
        "working tree %d files\nclone        %d files\nmissing from clone: %s"
        % (len(local), len(cloned), absent or "none"),
    )
    assert not absent, (
        "%d dbt file(s) exist locally but are not tracked, so `dbt build` on a fresh clone would "
        "not see them: %r" % (len(absent), absent[:20])
    )


NEWLINE = chr(10)


def test_every_directly_invoked_script_is_executable_in_the_clone(clone, evidence):
    """A script invoked as a command must carry the exec bit **in git**, not just on this disk.

    Windows has no exec bit and `core.filemode=false` hides its absence, so a script that works here
    can be `Permission denied` on the first Linux clone that runs it. Platform-Infra hit exactly
    that: `scripts/up-dev.sh` executes `scripts/init-db.sh` directly, inside `make up-dev`.

    Two things this deliberately does NOT do, because the obvious versions are wrong.

    It does not assert "has a shebang, therefore 100755". That would flag `postgres/init/00-init.sh`
    and `analytics/warehouse/init/00-bootstrap.sh`, which the Postgres entrypoint *sources* when they
    are not executable -- working exactly as intended -- and `scripts/lib/common.sh`, which is only
    ever sourced.

    And it does not grep every tracked file for the path. My first version did, and reported ten
    "defects" that were prose: a `pytest.skip` message, a sentence in a contract, a comment in a SQL
    test. An assertion firing on the wrong subject is the failure this suite exists to catch. So a
    match counts only in **command position** -- first token of a line or of a `&&` / `;` / `|`
    clause -- inside a shell script, Makefile or workflow, and never after a quote.
    """
    import re
    import subprocess

    listing = subprocess.run(
        ["git", "ls-files", "-s"], capture_output=True, text=True, cwd=str(clone)
    ).stdout
    modes = {}
    for row in listing.splitlines():
        mode, _, _, path = row.split(maxsplit=3)
        modes[path] = mode

    scripts = {}
    for path, mode in modes.items():
        if not path.endswith((".sh", ".py")):
            continue
        try:
            head = (clone / path).read_text(encoding="utf-8", errors="replace").split(NEWLINE, 1)[0]
        except OSError:
            continue
        if head.startswith("#!"):
            scripts[path] = mode

    sources = []
    for path in modes:
        if not (path.endswith((".sh", ".yml", ".yaml")) or path.split("/")[-1] == "Makefile"):
            continue
        try:
            sources.append((path, (clone / path).read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue

    invoked, report = {}, []
    for script, mode in sorted(scripts.items()):
        # Command position, then the prefixes a call site legitimately puts in front of a
        # path: an opening quote, a `$REPO_ROOT/`-style variable, `./`. The real defect
        # Platform-Infra found is written `"$REPO_ROOT/scripts/init-db.sh"` -- quoted AND
        # variable-prefixed -- so a pattern rejecting either would have missed the one case
        # that mattered, and this test would have passed while proving nothing.
        pattern = re.compile(
            r"""(?:^|(?<=&&)|(?<=;)|(?<=[|])|(?<=[(])|(?<=`))"""
            r"""[ 	]*@?["']?(?:\$\{?[A-Za-z_][A-Za-z0-9_]*\}?/)?(?:[.]/)?"""
            + re.escape(script) + r"""(?![\w./-])"""
        )
        sites = []
        for path, text in sources:
            for raw in text.splitlines():
                line = raw.rstrip()
                if line.lstrip("\t ").startswith("#"):
                    continue
                match = pattern.search(line)
                if not match:
                    continue
                sites.append("%s: %s" % (path, line.lstrip("\t ")[:88]))
                break
            if sites:
                break
        if sites:
            invoked[script] = (mode, sites)
        report.append("%s  %-56s %s" % (mode, script, "invoked as a command" if sites else ""))

    evidence.add("tracked scripts with a shebang, and their git mode", NEWLINE.join(report))
    broken = {s: v for s, v in invoked.items() if v[0] != "100755"}
    evidence.add(
        "invoked as a command but NOT executable in git",
        NEWLINE.join("%s  %s -- %s" % (v[0], s, v[1][0]) for s, v in broken.items()) or "none",
    )
    assert not broken, (
        "%d script(s) are invoked as a command yet are not executable in a fresh clone, so they "
        "would be 'Permission denied' on Linux: %r" % (len(broken), sorted(broken))
    )


def test_the_shared_index_holds_no_pending_mode_revert(evidence):
    """An index entry whose mode differs from HEAD is a REVERT waiting for the next commit.

    This is a shared-index hazard of the same family as the ones in PLAN.md, and it was found the
    hard way. A mode-only change committed via the plumbing route -- private index, `write-tree`,
    `commit-tree`, `update-ref` -- deliberately never touches the shared index, which is what makes
    it safe on a tree several agents are writing to. The consequence is that afterwards HEAD says
    100755 while the shared index still says 100644, and **the next ordinary commit by anyone
    silently reverts the mode**. Nothing warns: `git status` shows the file as unmodified, because
    on Windows `core.fileMode=false` means the working tree's mode is ignored on both sides.

    The follow-up that closes it is `git update-index --chmod=+x <path>`, which syncs the one entry.
    This test is the tripwire that says whether it was done.

    Deliberately NOT run against the clone fixture: a clone has a fresh index built from HEAD, so it
    can never exhibit this. The subject is *this* working repository's index, which is the shared one.
    """
    import subprocess

    from helpers import env

    root = str(env.repo_root())
    index = {}
    for row in subprocess.run(
        ["git", "ls-files", "-s"], capture_output=True, text=True, cwd=root
    ).stdout.splitlines():
        mode, _, _, path = row.split(maxsplit=3)
        index[path] = mode

    head = {}
    for row in subprocess.run(
        ["git", "ls-tree", "-r", "HEAD"], capture_output=True, text=True, cwd=root
    ).stdout.splitlines():
        meta, path = row.split("\t", 1)
        mode, _, _ = meta.split(maxsplit=2)
        head[path] = mode

    divergent = [
        (path, head[path], index[path])
        for path in sorted(set(head) & set(index))
        if head[path] != index[path]
    ]
    # DIRECTION MATTERS, and the first version of this test ignored it -- then fired on five files
    # Security had legitimately staged mid-commit. That is PLAN.md's "unstable evidence during
    # active waves": a red result that is another agent's in-flight work, not a regression.
    #
    #   HEAD 100644 -> index 100755 : someone is ADDING the bit. In-flight, fine, informational.
    #   HEAD 100755 -> index 100644 : the bit EXISTS and the index would take it away. That is the
    #                                 silent revert, and it is the only direction worth failing on.
    reverting = [row for row in divergent if row[1] == "100755" and row[2] == "100644"]
    adding = [row for row in divergent if row not in reverting]

    evidence.add(
        "index entries whose mode differs from HEAD",
        NEWLINE.join("%s  HEAD=%s  index=%s" % row for row in divergent) or "none",
    )
    evidence.add(
        "in flight (adding a bit HEAD lacks) -- reported, not failed",
        NEWLINE.join("%s  %s -> %s" % row for row in adding) or "none",
    )
    assert not reverting, (
        "%d file(s) are executable in HEAD but 100644 in the shared index. The next ordinary commit "
        "by ANY agent will silently strip the bit, and nothing warns: `git status` shows them "
        "unmodified because core.fileMode=false ignores the working tree's mode on both sides. "
        "Fix with `git update-index --chmod=+x <path>`: %r"
        % (len(reverting), [(path, h, i) for path, h, i in reverting])
    )
