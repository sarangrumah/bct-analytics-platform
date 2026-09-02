# CD contract — what is proven, what is not, and what to re-run when a remote exists

Owner: Security agent, sole writer of `.github/workflows/**` (master prompt §2.1).
Companion to `security/CI-CONTRACT.md`, which covers CI.

> **`cd.yml` has never executed.** There is no git remote by operator decision, so no run
> of it exists anywhere. This document exists so that nobody mistakes a reviewed workflow
> for a tested one. Section 2 is the honest split.

---

## 1. What CD does, in order

```
tag v*  ──▶ require-ci-green ──▶ discover ──▶ build-sign-attest ──▶ deploy
             (ci-gate for            (registry)   (GHCR + cosign      (Environment:
              THIS commit)                         + SLSA)             production)
```

The deploy step itself runs `security/deploy/deploy.sh` on the host, piped over ssh from
the tagged commit, so the deploy logic that runs is always the version that was reviewed:

```
1 record running digest → 2 backup → 3 cosign verify-attestation → 4 swap by digest
→ 5 idempotent migration → 6 health gate → 7 on failure: restore digest, exit non-zero
```

Nothing is swapped until the backup **and** the signature both pass. A failed deploy that
rolled back cleanly still exits non-zero — a rolled-back deploy is a failed deploy, and a
CD run that reports success after rolling back is how a bad release gets recorded as
shipped.

---

## 2. Verified by EXECUTION vs verified by REVIEW ONLY

This is the section the Lead asked for. Read it before trusting any criterion below.

### 2.1 Verified by execution — these were run, and made to fail first

| Criterion | How it was made to go red | Result |
|---|---|---|
| 8 — rollback demonstrated | Deployed a digest whose healthcheck cannot pass (`alpine:3.20` against a check that greps for `3.19`) | exit 5; `ROLLING BACK to sha256:6baf4358…`; previous digest running and re-verified healthy |
| 7 — backup failure aborts before any swap | `BACKUP_CMD=false` | exit 3; running digest **unchanged** |
| 5 — unsigned image refused | `VERIFY_CMD=false`, and separately a real `cosign verify` against an unsigned image | exit 4; running digest **unchanged** |
| 5 — empty verifier fails closed | `VERIFY_CMD=''` | exit 4, refuses to deploy rather than skipping verification |
| deploy-by-digest | Passed the tag `latest` instead of a digest | exit 2 |
| migration failure is not silent | `MIGRATE_CMD=false` | exit 5, rolled back |
| 9 — re-runnable | Re-deployed the running digest | exit 0, "already running" |
| 4 — real signing | `cosign sign` + `cosign verify` against a real local registry | verify passes on signed, **fails on unsigned** |
| 4 — attestation | `cosign attest --type slsaprovenance` + `verify-attestation` | round-trips; and **fails when only a signature exists**, so the two checks are provably not interchangeable |
| 10 — YAML parses | `yaml.safe_load` over both workflows | both parse |
| 1 — every pin is a REAL, resolvable SHA | Fed the checker an invented SHA (`000…0`) and, separately, a real SHA with a **wrong** version comment | Both **REJECTED**. All 12 unique pins then resolved against the live GitHub API: the commit exists **and** the tag in the trailing comment dereferences to exactly that SHA. 0 bad. "An invented SHA is worse than an unpinned action" is now tested, not asserted — a 40-character hex string is only *shaped* like a commit |
| 3 — scan matrix covers every image | Diffed the generated matrix against the Dockerfiles a **clone** contains, asserting both sets non-empty first | **6 of 6**, symmetric difference empty. `sca-node` likewise covers the one Node service that exists (`insight-portal`); `login-gateway` is FastAPI/Python and is covered by `sca-python`, not `sca-node` |
| 3 — the coverage gate can itself fail | Emptied the swept population, and separately registered a fixture `present` that is not on disk | Both **FAIL**. Until 2026-08-31 the gate concluded "nothing unregistered" from an empty difference, which is also what a broken sweep returns |

Reproduce all of it:

```bash
bash security/deploy/test-rollback.sh          # 19/19
COSIGN=/path/to/cosign bash security/deploy/test-signing.sh   # 9/9
```

Both harnesses include a **control** that must pass. That is not decoration: during
development the signing harness reported its negative test as PASS while the control was
failing — everything was failing, so "unsigned image rejected" was true for the wrong
reason. Without the control that run looked green.

### 2.2 Verified by REVIEW ONLY — not tested, and must not be reported as tested

| Item | Why it cannot be executed here | What would prove it |
|---|---|---|
| The whole of `cd.yml` | No remote; GitHub Actions cannot run | One tagged release end to end |
| **Keyless** cosign signing | Needs an OIDC token only an Actions runner can mint. The harness uses a local key pair: same cosign verbs, same gate semantics, **different identity source** | `cosign verify --certificate-identity-regexp …` against a real GHCR image |
| SLSA provenance via `actions/attest-build-provenance` | Requires the Actions attestation API | `cosign verify-attestation` on the pushed digest |
| `require-ci-green` | Needs a real check-run for a real commit | Tag a commit whose CI is red; CD must refuse |
| GHCR push and digest resolution | No registry to push to | The `deploy` job's digest step resolving a real tag |
| GitHub Environment + required reviewers | A repo setting, not a file. **A missing reviewer rule auto-approves silently** | Settings screenshot + a run that waits for approval |
| ssh transport, `StrictHostKeyChecking`, known_hosts | No deploy host | A deploy to a real VPS |
| `scripts/tenant-backup.sh` as the real backup | Not exercised against production data | A deploy on the host |
| `scripts/migrate-modules.sh`, `scripts/post-deploy-health.sh` | **These do not exist yet** — see §5 | Their authors landing them |
| Rollback over ssh against the production compose | The mechanism is proven locally; the transport is not | A forced failure on the host |

### 2.3 What to re-run the day a remote exists

In this order. Stop at the first failure.

1. `git push` the branch and let CI run. `ci-gate` must be green.
2. Configure the `production` Environment: required reviewers, and the secrets
   `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`, `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_PATH`.
   **Verify the reviewer requirement by watching a run pause for approval.**
3. Tag a commit whose CI is **red**. CD must refuse at `require-ci-green` (criterion 6).
   This is the negative test for that criterion and has never been run.
4. Tag a good commit. Confirm GHCR receives the image, and that the in-job
   `cosign verify` / `verify-attestation` steps pass against the keyless identity.
5. **Negative test the deploy gate**: push an image to GHCR *without* signing it, then
   run the deploy job against that digest. It must fail at step 3 with nothing swapped.
6. **Re-demonstrate the rollback on the real host**: deploy a digest whose healthcheck
   fails, confirm the previous digest is restored and the job still reports failure.
7. Run the migration step twice; the second run must be a no-op (criterion 9).

Until step 3 and step 5 have run against a real remote, criteria 5 and 6 are
**partially verified**: the enforcement logic is proven, the GitHub-side plumbing is not.

---

## 3. Job names and pass/fail semantics

| Job | Fails when |
|---|---|
| `require CI green` | No `ci-gate` check-run for this commit, or it is not `success`. `ci-gate` aggregates every CI job, so this transitively requires all of them |
| `discover deployable images` | An unregistered Dockerfile exists (`scan_targets.py --check`) |
| `build+sign (<image>)` | Build, push, sign, attest, or the **in-job self-verification** fails. `fail-fast: true` — a half-built set is never deployed |
| `deploy to production` | Any of: digest cannot be resolved; a required secret is empty; backup fails; verification fails; health gate fails (even if the rollback succeeds) |

`deploy` runs only after the Environment's required reviewers approve.

### Exit codes from `security/deploy/deploy.sh`

| Code | Meaning | State of the host |
|---|---|---|
| 0 | Deployed and healthy | New digest running |
| 2 | A tag was passed instead of a digest | Unchanged |
| 3 | Pre-deploy backup failed | **Unchanged — nothing swapped** |
| 4 | Signature/attestation verification failed, or the verifier was empty | **Unchanged — nothing swapped** |
| 5 | Health gate failed; rolled back to the previous digest | Previous digest running |
| 6 | Rollback itself failed | **Needs manual intervention**; restore from the pre-deploy backup |

---

## 4. Configuration that lives outside this repository

These cannot be enforced by a file, so they are listed here and must be checked by hand.
Every one of them fails **open** if forgotten — which is why they are called out.

- **`production` Environment → required reviewers.** Without it, `environment:` provides
  no approval gate and the deploy proceeds unattended.
- **Environment secrets**, never repository secrets: repo-level secrets are readable by
  every workflow, including one added in a pull request.
- **GHCR package visibility** — private unless the operator decides otherwise.
- **Branch protection** requiring `ci-gate` on the default branch.
- **`DEPLOY_KNOWN_HOSTS` must be populated.** The workflow refuses to run without it
  rather than falling back to `StrictHostKeyChecking=no`.

---

## 5. Owed by other agents — CD cannot be fully green without these

Requested through the Lead; none is Security's to write.

| Path | Owner | Why CD needs it |
|---|---|---|
| `scripts/migrate-modules.sh <tenant>` | Platform-Infra | Idempotent Odoo module update (criterion 9). Must be a no-op on the second run |
| `scripts/post-deploy-health.sh <tenant>` | Platform-Infra + DWH | The app-level health gate: HTTP health, dbt reconciliation, and `make check-alerting` — a deploy into an environment with dead alerting is a deploy nobody can observe |
| `scripts/analytics/dbt-ci-fixture.sh` | Data-Warehouse + QA | Seeds `raw_*` so `dbt build` can run in CI. Until it exists, `dbt-ci` tier 3 is **skipped and reported**, never passed |

`deploy.sh` fails closed on all three: an empty `VERIFY_CMD` aborts, and a missing
migration or health script makes the command fail, which triggers the rollback.

---

## 6. How each gate was made to go red

The standing rule from `PLAN.md`: *a check that has never been observed to fail is not yet
known to work.* Every row in §2.1 names the perturbation used. The two that mattered most
were found by these harnesses **in my own work**:

- `deploy.sh` set `HEALTH_RESULT=1` on migration failure and never read it — a migration
  failure that did nothing. Found by writing the test that asserts it rolls back.
- `test-rollback.sh` used `${VERIFY_CMD:-true}`, which substitutes for *empty* as well as
  unset, so the empty-verifier case silently became `true`. The test reported PASS while
  exercising nothing — the dominant defect pattern, inside the harness written to catch it.

Neither would have been found by a green run.
