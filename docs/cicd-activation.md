# Turning CI/CD on

**Ownership note.** `.github/workflows/ci.yml` (and `cd.yml`, when it exists) belong to the Security
agent; this document belongs to QA. That split is deliberate — master prompt §2.1 — and it means
**nothing in this file may be applied by editing a workflow yourself.** A change to a workflow is a
diff request to Security. What follows is the activation procedure and the current, verified state
of the pipeline, not a set of edits to make.

Everything marked **[SECURITY OWES]** is a section Security drafts and sends here through the Lead.
It is left visible rather than omitted, so a reader can tell the difference between "not applicable"
and "not yet written".

---

## 1. What exists today

`.github/workflows/ci.yml` — 11 jobs, runners pinned to `ubuntu-24.04` (never `ubuntu-latest`: a
runner image change is a change, and should not arrive unannounced).

| Job | What it does |
|---|---|
| `discover` | parses `security/scan-targets.yml` into the matrices the scan jobs consume |
| `lint` | `pre-commit run --all-files` |
| `sast` | semgrep against `.semgrep/` |
| `sca-python` | `pip-audit` + SBOM per Python component |
| `sca-node` | the same per Node project, matrixed |
| `secrets` | gitleaks over **full history**, not just the diff |
| `hadolint` | every Dockerfile |
| `container-scan` | trivy per image, matrixed off `discover` |
| `fs-scan` | trivy filesystem + config |
| `dbt-ci` | **currently disabled**, see §3 |
| `ci-gate` | the required status check that aggregates the rest |

There is **no `cd.yml`**. Continuous deployment is not implemented, and §5 is therefore a plan
rather than a description.

---

## 2. Activation, in order

### 2.1 Repository settings — one-time, done in the GitHub UI

1. **Branch protection on `main`**: require `ci-gate` to pass; require a pull request; disallow force
   pushes and deletion.
2. **Actions permissions**: default `GITHUB_TOKEN` to read-only; grant `packages: write` and
   `id-token: write` only to the jobs that publish.
3. **Environments**: create `production` with a required reviewer. A CD workflow that can deploy
   without a human approving is a CD workflow that will.

### 2.2 Secrets

| Secret | Used by | Notes |
|---|---|---|
| `SOPS_AGE_KEY` | any job decrypting `.secrets.enc.yaml` | see `security/SOPS-ONBOARDING.md` |
| `GHCR_TOKEN` | image publish | only if not using `GITHUB_TOKEN` |
| deploy key / SSH | CD | **[SECURITY OWES]** — the production access path is Security's to specify |

**No secret is needed for CI as it stands.** Every current job runs on the source tree. Adding a
secret to a job that did not need one is a change worth noticing, not a routine step.

### 2.3 Local dry run before pushing anything

```bash
pre-commit install
pre-commit run --all-files      # the same hooks the `lint` job runs
make scan-secret                # .env.example must still be all `changeme`
make check-gitignore            # no addon data file or dbt seed silently ignored
bash tests/run.sh -m "not slow and not coldstart"
```

`make check-gitignore` exists because an unanchored `data/` pattern once hid three install-critical
files while every working-tree test passed. It is cheap and it catches a class of bug that nothing
else in the pipeline sees.

---

## 3. The integration suite in CI — a diff request, not an edit

`tests/` is runnable in CI, but it needs a live stack, which the current jobs do not start. The job
below is what QA would like added. **Send it to Security; do not apply it.**

```yaml
  integration:
    name: integration (tests/)
    runs-on: ubuntu-24.04
    needs: [lint]
    timeout-minutes: 45
    steps:
      - uses: actions/checkout@v4
      - name: Bring up the stack
        run: |
          cp .env.example .env
          python3 scripts/gen-env-secrets.py --in-place
          make up-dev
          make up-analytics
      - name: Start the CDC loader
        run: |
          bash scripts/analytics/cdc-provision.sh
          bash scripts/analytics/cdc-run.sh --detach
      - name: Build the marts
        run: make dbt-run
      - name: Run the suite
        run: bash tests/run.sh -m "not coldstart" -ra
      - name: Teardown
        if: always()
        run: docker compose -p odoo19-bct down -v --remove-orphans
```

Three things about it are deliberate and should survive review:

- **`-m "not coldstart"`.** The cold-start tests do a `down -v`. On a throwaway CI runner that is
  harmless and arguably desirable, but it must be an explicit choice rather than something that
  happens because a marker was forgotten. If Security wants it in CI, add a second job with
  `RUN_COLDSTART=1` and `-m coldstart`.
- **`-ra`.** Skips are printed with their reasons. A suite where "component not built yet" is
  invisible is a suite that reports green for the wrong reason.
- **`if: always()` on teardown.** A failed run that leaves containers behind poisons the next one,
  and the second failure is much harder to read than the first.

`dbt-ci` is disabled in `ci.yml` with a comment saying it will fail until Phase 3 exists. Phase 3
now exists and `dbt build` is green locally (PASS=316, WARN=0, ERROR=0, SKIP=0), so enabling it is
also a diff request to Security.

---

## 4. Reading a CI failure

| Job fails | Usually means | First thing to try |
|---|---|---|
| `lint` | a pre-commit hook, most often line endings or ruff | `pre-commit run --all-files` locally; it fixes most in place |
| `secrets` | gitleaks found something **in history** | do not just delete the line — the history still has it; rotate the credential first |
| `container-scan` | a CVE in a base image | bump the digest; an entry in `.trivyignore` needs a reason *and* an expiry date, which a pre-commit hook enforces |
| `sca-*` | a dependency advisory | pin forward; do not suppress |
| `discover` | `security/scan-targets.yml` does not match reality | a new image or Node project was added without registering it — that is what this job is for |
| `ci-gate` | one of the above | read the job it names, not this one |

---

## 5. Continuous deployment — **[SECURITY OWES]**

`cd.yml` does not exist. This section is a placeholder with the shape agreed in the phase-5 brief, so
that its absence is visible rather than silent. Security drafts the content; QA publishes it here.

Expected to cover, at minimum:

- image build, digest pinning, and signing (cosign), with provenance attestation;
- the promotion path from `main` to the production VPS, and who approves it;
- **rollback, demonstrated rather than asserted** — the phase-5 acceptance criterion is a
  demonstrated rollback, not a documented one;
- migration handling: Odoo module upgrades and warehouse DDL are not the same risk and should not
  share a step;
- what happens to the replication slot during a deploy. A deploy that stops the CDC consumer for
  longer than the WAL budget allows will drop the slot and force a re-seed; the 2 GiB cap and the
  alerting in `docs/runbooks/analytics-pipeline.md` §3 are the numbers that bound it.

Until this section is written by its owner, **there is no automated deployment**, and
`docs/prod-deploy-checklist.md` is the manual procedure.
