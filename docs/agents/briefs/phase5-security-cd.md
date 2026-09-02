# Brief: Security — Phase 5 (CD, signing, and supply-chain gap closure)

## Objective
Close the four supply-chain gaps the master prompt names, and deliver a CD pipeline where an
unsigned or unverifiable image **cannot** reach a host, a deploy that fails its health gate rolls
itself back automatically, and the rollback has been demonstrated rather than merely documented.

## Read first
- `docs/agents/PLAN.md`, and your own Phase 1 output (`ci.yml`, `.sops.yaml`, threat model).
- `docs/adr/0001-analytics-warehouse.md` — the reconciliation tests that form part of the post-deploy
  health gate.
- `docs/agents/contracts/04-platform.md` — `scripts/tenant-backup.sh` conventions, service names,
  healthcheck definitions the deploy gate must exercise.
- `docs/agents/contracts/05-warehouse.md` and `06-api.md` — the images that must be in the scan and
  signing matrices.

## Ground truth
You built the Phase 1 CI baseline. By now the repo also contains `analytics/dbt`,
`analytics/semantic-api`, `analytics/cdc`, `login-gateway` and `insight-portal`, each with a
Dockerfile. **There is still no git remote** unless the operator has since created one — write the
workflows to be correct on the day one exists, and do not create one yourself.

## Scope — in — the four named gaps

### 1. JS supply chain (§5.1)
Extend `sca-node` to cover **every** Node service that now exists: `login-gateway`,
`insight-portal`, and any other. `npm audit --audit-level=high` **plus OSV-Scanner**. Generate
CycloneDX SBOMs and upload them alongside the Python SBOMs. A path that does not exist must be
**explicitly skipped and reported**, never silently passed.

### 2. Scan coverage (§5.2)
Add `insight-portal` and **every** new analytics image (`analytics/dbt`, `semantic-api`, `cdc`,
`login-gateway`) to the `container-scan` matrix. No new image ships unscanned. Merge the diff
requests other agents have sent through the Lead — **you are the only writer of `ci.yml`** (§2.1).

### 3. Real signing (§5.3)
Replace any placeholder with **actual keyless cosign signing plus an SLSA provenance attestation**,
and add a `cosign verify-attestation` step that runs **at deploy time**. A deploy of an unsigned or
unverifiable image **must fail**. Do not ship a signing job that logs success without signing — that
is precisely the gap this phase exists to close.

### 4. CD workflow — `.github/workflows/cd.yml` (§5.4)
- Trigger on tag; **require all CI jobs green**.
- Build → push to GHCR → sign → attest.
- Deploy to the VPS over SSH using a **GitHub Environment with required reviewers**.
- **Pull by digest, never by `:latest`.**
- **Pre-deploy `scripts/tenant-backup.sh` must run and succeed before anything is swapped.**
- An **idempotent, re-runnable** migration step for Odoo module updates.
- Post-deploy health gate against the existing healthchecks **plus dbt reconciliation tests**.
- **Automatic rollback to the previous digest on health-gate failure. Document AND test it** — §6
  requires the rollback demonstrated, not just written.

### 5. dbt in CI (§5.5)
`dbt build` against a seeded fixture database on every PR touching `analytics/`. Replace the Phase 1
disabled placeholder with a real job. Coordinate the fixture with QA and DWH through the Lead.

### 6. Docs (§5.6)
`docs/cicd-activation.md` and `docs/prod-deploy-checklist.md` must match reality when you are done.
**`docs/` is QA & Docs' path** — send them the content via the Lead; do not write those files
yourself. Your own `security/` directory is yours.

## Scope — out
- Application logic of any service. You scan and gate it; you do not change it.
- `docs/**` outside `security/` — QA & Docs owns it.
- Everything owned by Platform, DWH, Backend and Frontend.
- **Creating a git remote, pushing, or `gh repo create`** — still not authorised.

## Constraints
- **Every third-party action SHA-pinned** to a full 40-char commit SHA with a version comment. An
  **invented SHA is worse than an unpinned action** — if you cannot resolve a real one, say so.
- Least-privilege `permissions:`; elevate per-job only where genuinely required. Signing needs
  `id-token: write` — scope it to the signing job alone, never workflow-wide.
- Deploy secrets via GitHub Environments, never workflow-level. No secret in any file.
- `jq` is absent locally; do not depend on it in anything you must verify locally.

## Acceptance criteria — testable statements only
1. Zero unpinned `uses:` across all workflows. Every pin is a real, resolvable SHA.
2. `sca-node` covers every Node service present; a missing path is skipped **with a reported reason**.
3. `container-scan` matrix includes every image the repo builds. Prove by diffing the matrix against
   `find . -name Dockerfile`.
4. The signing job performs real keyless signing and produces a verifiable attestation.
5. `cosign verify-attestation` runs at deploy time and **the deploy fails when it fails**. Prove with
   a negative test using an unsigned image.
6. CD refuses to run when a CI job is red.
7. Pre-deploy backup failure aborts the deploy before any swap.
8. **Rollback demonstrated**: force a health-gate failure and show the previous digest restored.
   Paste the run output.
9. The migration step is re-runnable: run it twice, second run is a no-op.
10. All workflow YAML parses.

## Evidence required — paste the output of exactly these
```
grep -rnE 'uses: .*@(v[0-9]|main|master)' .github/workflows/ || echo "NO_UNPINNED_ACTIONS"
grep -rhoE 'uses: [^ ]+@[0-9a-f]{40}' .github/workflows/ | sort -u
python -c "import yaml,glob;[yaml.safe_load(open(f,encoding='utf-8')) for f in glob.glob('.github/workflows/*.yml')];print('WORKFLOW_YAML_OK')"
find . -name Dockerfile -not -path './node_modules/*' | sort
grep -n "matrix" -A25 .github/workflows/ci.yml | head -60
grep -n "cosign\|attest\|provenance" .github/workflows/*.yml
grep -n "rollback\|previous_digest\|tenant-backup" .github/workflows/cd.yml
```
Because there is no remote, CD cannot be executed here. **State plainly which criteria are verified
by execution and which are verified only by review** — do not report an untested rollback as
demonstrated. If the operator later creates a remote, say exactly what must then be re-run.

## Escalation triggers — stop and return to Lead
- You cannot resolve a genuine SHA for a required action.
- A criterion cannot be executed without a remote or a deploy target — report it as unverified with
  the reason and the exact command that would verify it.
- Another agent's design would fail your gate. **You have veto and the Lead does not override it**
  (§2.4) — raise it immediately rather than at the gate.
