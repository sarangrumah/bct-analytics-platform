# CI contract — job names, pass/fail semantics, and how to get your artefact scanned

Owner: Security agent, sole owner of `.github/workflows/**` (master prompt §2.1).
Consumers: **Phase 5 CD** (gates on these names), **QA & Docs** (adds test jobs through
Security), **DWH / Backend / Frontend** (register images and Node projects).

---

## 1. The ownership rule, restated

`ci.yml` — and `cd.yml` when Phase 5 creates it — have exactly one writer: the Security
agent. **QA does not edit them.** QA sends Security a diff request for its test jobs and
Security merges it. Same for DWH's `dbt-ci` body, Backend's service jobs, and anyone
else's.

This is not process for its own sake. CI is the only thing in the repository whose job is
to say no. A file that every agent can edit is a file where the job that would have failed
can be removed by whoever is blocked by it — usually with a good reason, at the worst
moment, in a commit nobody reads.

**There is no `cd.yml` and there must not be one before Phase 5.** Not even a stub: a stub
is a deployment path that nobody reviewed, sitting in the repository one `on:` edit away
from running.

---

## 2. Jobs and their pass/fail semantics

Runner: `ubuntu-24.04`, pinned. Workflow permissions: `contents: read`.

| Job | Fails when | Never fails on |
|---|---|---|
| `discover scan targets` | a Dockerfile or `package.json` exists that no scan job covers; a target registered `present` whose path is missing; the registry cannot be parsed | a registered target that has not landed yet (reports SKIP) |
| `lint (pre-commit)` | any pre-commit hook fails: CRLF, trailing whitespace, missing EOF newline, unparseable YAML/JSON/TOML/XML, a private key, an oversized file, a ruff-check finding, a hadolint finding, a gitleaks staged finding, an expired scanner suppression, an unregistered scan target | formatting drift (`ruff-format` is `stages: [manual]` in wave 1 — see §6) |
| `sast (semgrep)` | a finding from `.semgrep/**`, the repository's own rules, including the contract rules | a Semgrep **registry** finding — those run advisory and are uploaded, never blocking |
| `sca-python (pip-audit + SBOM)` | a known vulnerability in `security/requirements-ci.txt`, in the **installed environment** (which catches transitive versions a manifest cannot express), or in any tracked `requirements*.txt` | the absence of project requirements (reports SKIP in the job summary) |
| `sca-node (<name>)` | `npm audit` at `--audit-level=high` finds a high/critical, or OSV-Scanner finds a vulnerability | a registered project whose directory does not exist yet (reports SKIP) |
| `secrets (gitleaks, full history)` | a secret in the working tree **or anywhere in git history** (`--log-opts="--all --full-history"`) | values allowlisted by name in `.gitleaks.toml`, each with a documented reason |
| `hadolint (every Dockerfile)` | any Dockerfile produces a warning or worse | info-level findings; line-scoped `# hadolint ignore=` with a reason |
| `container-scan (<image>)` | a **fixable** CRITICAL/HIGH vulnerability, an embedded secret, or a misconfiguration in a built image | unfixed base-image CVEs (collected in the advisory report and uploaded); an image that does not exist yet (reports SKIP) |
| `fs-scan (trivy filesystem + config)` | a fixable CRITICAL/HIGH in a dependency manifest, a secret, or an IaC misconfiguration in compose/Dockerfiles | LOW/MEDIUM config findings (advisory report) |
| `dbt-ci` | — **DISABLED**, `if: false`. See §5 | — |
| **`ci-gate`** | **any** job above reported `failure` or `cancelled`, or any job other than `dbt-ci` reported `skipped` | `dbt-ci` being skipped — the one permitted skip, and it is named in code |

### What Phase 5 CD gates on

**One required status check: `ci-gate`.**

It `needs:` every other job and runs with `if: always()`, so it observes their results
rather than inheriting them. Consequences that matter for CD:

- Adding a job to `ci.yml` tightens the gate automatically. Nobody has to remember to
  update a branch-protection list.
- A job that is deleted, renamed or fails to start surfaces as a missing dependency, not
  as a silent pass.
- A **skipped** job fails the gate. A job that did not run is not a job that passed — that
  distinction is where "green build" lies most often. `dbt-ci` is the single exception and
  it is exempted by name in the gate's own code, with the reason next to it.

CD should require `ci-gate` and nothing else. Requiring individual job names would break
every time the matrix changes shape, which it does whenever an image is registered.

---

## 3. Registering a new image or Node project — the extension point

**You do not edit `ci.yml`.** The `container-scan`, `sca-node` and coverage matrices are
generated at run time from a registry file:

> ### `security/scan-targets.yml`

Send the Lead one entry. For an image:

```yaml
  - name: my-service                  # unique; becomes the scan job's name
    dockerfile: my-service/Dockerfile # path from the repo root
    context: my-service               # docker build context
    owner: Backend                    # who is accountable for its findings
    wave: 3                           # PLAN.md wave
    status: pending                   # pending until it exists, then present
```

For a Node project (npm audit + OSV-Scanner + CycloneDX SBOM):

```yaml
  - name: my-service
    path: my-service                  # directory containing package.json
    owner: Backend
    wave: 3
    status: pending
```

Verify before you hand it over — this is the same check CI runs:

```bash
python3 security/scan_targets.py --check     # exits 1 on anything unscanned
python3 security/scan_targets.py --list      # what will be scanned, what will be skipped
```

### Why a registry instead of a literal matrix

Five agents need their artefacts scanned; one agent may edit the workflow. A registry
turns "please add my image to CI" from a workflow edit into a five-line data change that
the Lead can review in seconds — and, more importantly, makes *not* registering an image a
build failure rather than an oversight.

### The guarantee it provides

| Situation | What happens |
|---|---|
| Image registered, Dockerfile exists | scanned |
| Image registered, not built yet | job runs, prints an explicit **SKIPPED** row with owner and wave to the job summary. Never absent — absent and clean look identical in a job list |
| Image lands but the registry still says `pending` | **scanned anyway**, plus a loud drift warning to flip the status. Landing an image never buys a scan-free window |
| Registry says `present` but the path is gone | hard fail |
| **A Dockerfile or `package.json` appears that is in no entry** | **hard fail** — master prompt §5.2, no new image ships unscanned |

The last row is the one that matters. Without it a registry is documentation; with it, the
repository cannot grow an unscanned artefact.

---

## 4. Suppressing a finding

Every scanner suppression must carry a reason and an expiry, and this is enforced by
`security/check_ignore_policy.py`, which runs as a pre-commit hook and as a CI step:

| File | Required per entry |
|---|---|
| `.trivyignore` | `reason:`, `expires: YYYY-MM-DD` (future, ≤ ~1 year), `owner:` |
| `.hadolint.yaml` (`ignored:`) | `reason:`, `expires: YYYY-MM-DD` |
| `.gitleaks.toml` (allowlist descriptions) | `reason:`, `expires:` — `never` allowed only because a gitleaks allowlist describes correct usage (SOPS ciphertext *is* high-entropy) rather than a deferred fix |

An expiry in the past **fails the build**. That is the design: the entry does not vanish
quietly and does not live forever; a human re-reads it and decides again.

Preferences, strongest first:

1. **Fix it.**
2. **Line-scoped suppression with the reason next to the code** — `# hadolint ignore=DL3025`,
   `# nosemgrep: <rule-id> - <reason>`, `# noqa: <code>`.
3. **Rule-scoped carve-out** in the rule itself (`paths.exclude`, gitleaks `targetRules`).
4. **Global ignore with reason + expiry.** Last resort. It switches a rule off for every
   artefact this project will ever build, including ones that do not exist yet.

Dry-run what a future date breaks:

```bash
python3 security/check_ignore_policy.py --today 2027-03-01
```

---

## 5. `dbt-ci` is disabled, and why that is the honest choice

`analytics/dbt/` does not exist. There is no `profiles.yml`, no warehouse to connect to and
no models to compile, so `dbt deps`, `dbt build` and `dbt test` would each fail on a
missing directory.

Both dishonest options were available and rejected:

- `|| true` on every step — a green dbt gate for a project that does not exist. Phase 5
  would then require a status check that has never verified anything.
- Omitting the job — loses the record that dbt CI is owed, and by whom.

`if: false` renders it as **Skipped** on every run: visible, impossible to mistake for a
pass, impossible to forget. Its placeholder step exits 1, so enabling it without writing a
real body fails loudly.

**To enable (Data Warehouse agent, Phase 3):** send Security a diff that deletes `if: false`
and replaces the placeholder with a real `dbt deps && dbt build --target ci && dbt test`,
and flip `analytics/dbt` to `status: present` in `security/scan-targets.yml`.

---

## 6. Known deviations in wave 1, stated rather than hidden

| Deviation | Reason | When it closes |
|---|---|---|
| `ruff-format` is `stages: [manual]`, not blocking | It would rewrite 21 files owned by agents writing them right now — a write outside Security's owned paths and a merge-conflict generator | GATE 1: one dedicated `style(repo): format with ruff` commit, then delete the line |
| Semgrep registry rules are advisory | A floating upstream ruleset can turn the merge path red with no commit behind it | Stays advisory; project rules are the gate |
| `ignore-unfixed: true` on the blocking Trivy scans | A base-image CVE with no upstream patch cannot be actioned here; blocking on it makes `.trivyignore` the only route to green, which teaches suppression | Reviewed at GATE 5 with the base-image bump |
| SARIF is uploaded as an artifact, not to code scanning | GitHub code scanning needs Advanced Security, which this repository does not have | If/when the repository gains a remote with GHAS |
| No cosign / SLSA provenance | Phase 5 | Phase 5 |
| `--require-hashes` not used for pip | Needs a full transitive lockfile this project does not generate yet | Phase 5, with the other build-integrity work |

---

## 7. Reproducing CI locally

```bash
pip install -r security/requirements-ci.txt
pre-commit install
pre-commit run --all-files                     # == the `lint` job
semgrep scan --config .semgrep/ --error        # == the `sast` job (blocking half)
python3 security/scan_targets.py --check       # == the `discover` coverage gate
python3 security/check_ignore_policy.py        # == the ignore-policy audit
gitleaks git . --config .gitleaks.toml --redact --log-opts="--all --full-history"
```

If a local run and CI ever disagree, the configuration is wrong and it is Security's bug —
report it rather than working around it. With one known exception:

### Why OSV-Scanner gates Node but not Python

Both are dependency scanners; they are pointed at different kinds of artefact on purpose.

`package-lock.json` records the **exact resolved version** of every transitive dependency,
so OSV-Scanner's static answer is the truth about what will be installed. A
`requirements.txt` records **ranges**, so a static resolver has to guess — and it guesses
the floor.

Measured on 2026-08-31: OSV-Scanner over `security/requirements-ci.txt` reported seven
advisories, up to CVSS 8.6, against `python-multipart 0.0.9` — the floor of a transitive
`>=0.0.9` constraint from semgrep. pip actually installs `0.0.32`, which is above every
fixed version in those advisories. `pip-audit` over the resolved environment reports no
vulnerabilities, and it is right.

So `sca-python` runs `pip-audit` twice — once over the manifest, once over the installed
environment — and does not run OSV-Scanner. `sca-node` runs `npm audit` **and**
OSV-Scanner, because there the lockfile makes both meaningful.

This is worth stating because the obvious "improvement" is to add OSV-Scanner to
`sca-python` for symmetry. That would fail the build over a version that is never
installed, and a gate that cries wolf is a gate people learn to re-run until it passes.

### `gitleaks dir .` locally will flag your `.env`, and it is right to

`gitleaks dir .` (equivalently `gitleaks detect --source . --no-git`) walks the
**filesystem**, and gitleaks has no flag to make it honour `.gitignore`. After
`make dev-bootstrap` your working directory contains a real `.env` full of real generated
dev credentials, so a local run reports findings in it. That is a true positive about your
disk and a true negative about the repository: `.env` is gitignored and untracked, and
nothing in it is in git.

A CI runner never sees this — `.env` is not checked out, so `gitleaks dir .` in the
`secrets` job scans a tree where the file does not exist.

**Do not** silence it by adding `.env` to a gitleaks allowlist. `.env` is the single most
important thing to catch if it is ever force-added, and a path exclusion would remove
exactly that protection. To reproduce the CI result locally, scan only what git can see:

```bash
# everything that could reach the repository: tracked + untracked-but-not-ignored
CLEAN="$(mktemp -d)"
git ls-files -co --exclude-standard -z \
  | while IFS= read -r -d '' f; do mkdir -p "$CLEAN/$(dirname "$f")"; cp "$f" "$CLEAN/$f"; done
gitleaks dir "$CLEAN" --config .gitleaks.toml --redact --no-banner
```

If *that* reports a finding, you have a real problem. Verified 2026-08-31: 136 files, no
leaks; full history across 21 commits, no leaks.


---

## 8. Working in a shared tree — two hazards the tooling cannot fix for you

Wave 1 runs several agents against **one working directory and one git index**. That is an
operator decision, not something this repo configures, and it creates two failure modes
that have each already happened once. Both are cheap to avoid and expensive to notice
late, so they are written down rather than rediscovered.

### 8.1 `git commit` commits the whole index, including someone else's staged files

`git add <my file>` followed by a plain `git commit` commits **everything currently
staged** — which, in a shared tree, includes whatever another agent staged while you were
working. Their files land in your commit, under your message, outside your owned paths.

This nearly happened during Phase 1: 25 files under `analytics/cdc/**` and
`scripts/analytics/**` were staged by another agent at the moment Security ran `git
commit`. It was caught only because a lint hook failed on *their* code and aborted the
commit — luck, not a control.

**Always commit path-limited:**

```bash
git commit -m "..." -- path/you/own          # ignores the rest of the index
```

Verify afterwards, which takes a second and is worth it:

```bash
git show --pretty="" --name-only HEAD        # only your paths should appear
```

### 8.2 The pre-commit hook stashes the whole tree while it runs

When `git commit` triggers pre-commit, it stashes **all** unstaged changes tree-wide,
runs the hooks against the staged content, and then restores the stash. If another agent
writes to a file inside that window, the restore can put the older content back and their
edit is silently gone. Platform-Infra hit this twice: a file that read clean, then read
stale thirty seconds later.

Measured behaviour, so you know which operations are safe:

| Operation | Stashes the tree? |
|---|---|
| `git commit` (hook path) | **yes** — `[INFO] Stashing unstaged files…` |
| `pre-commit run --files …` | no |
| `pre-commit run --all-files` | no |

So checking your work by hand is always safe; only committing has a window.

**Practical guidance:** commit before running anything long, keep uncommitted work short-
lived, and prefer `pre-commit run --files <your paths>` over a commit when you only want
to know whether the hooks pass.

**Recovery, if an edit does vanish.** pre-commit keeps every stash as a patch file and
does not delete it afterwards, so the change is recoverable:

```bash
ls -t ~/.cache/pre-commit/patch*        # newest first; they are ordinary git diffs
git apply ~/.cache/pre-commit/patch<timestamp>-<pid>
```

Read the patch before applying it — it contains the whole tree's unstaged state at that
moment, not just your file, so apply selectively if others were mid-edit too.

### 8.4 `git check-ignore` cannot tell you whether a file is committable

`git check-ignore` exits **0 on a negation match too**, so it answers "yes, a rule matched"
rather than "yes, this file is ignored". Demonstrated on a throwaway repo with
`*.pem` plus `!*-public.pem`:

```
$ git check-ignore -v --no-index keys/jwt-private.pem
exit=0   .gitignore:1:*.pem          keys/jwt-private.pem      <- ignored
$ git check-ignore -v --no-index keys/jwt-public.pem
exit=0   .gitignore:2:!*-public.pem  keys/jwt-public.pem       <- COMMITTABLE, same exit code
```

Only the `!` in the printed rule distinguishes them, and a script testing `$?` — or using
`-q` — never sees it. Use `git add --dry-run`, which is unambiguous:

```
$ git add --dry-run keys/jwt-private.pem   ->  exit=1  "The following paths are ignored…"
$ git add --dry-run keys/jwt-public.pem    ->  exit=0  "add 'keys/jwt-public.pem'"
```

The security-relevant direction is the one that is easy to miss. A guard written to assert
*"our `.env` really is ignored"* using `check-ignore`'s exit code **passes even if a
negation elsewhere has made it committable** — the check reports success while the
property it was written to protect is false.

That is the same failure shape as the `rolsuper` `t`/`f` rendering trap in
`security/THREAT-MODEL.md` T-1, and it is the more dangerous class of bug in any
verification step: not one that breaks, but one that answers confidently and wrongly. When
a check is load-bearing, test that it can fail — restore the broken condition and confirm
it goes red — before trusting that it is green for the right reason.

### 8.3 What this means for gate evidence

Evidence gathered while sibling agents are writing is not stable. `pre-commit run
--all-files` can report *"files were modified by this hook"* on read-only hooks such as
`check-yaml`, which is the tree changing mid-run rather than a finding, and a scan can
catch a file in a half-written state. During Phase 1 a Dockerfile appeared to reference a
missing `requirements.txt`; the file arrived between two consecutive commands, and
re-checking before reporting is the only reason a false finding was not raised.

**Re-run before you assert, and prefer a quiet moment.** A red result during active wave
work is a question, not a conclusion.
