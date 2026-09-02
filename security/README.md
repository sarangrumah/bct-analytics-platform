# `security/` — the security substrate

Owner: **Security agent** (`docs/agents/PLAN.md` roster). Security reviews at every gate,
including phases it did not build, and holds a veto the Lead does not override
(master prompt §2.4).

## What is here

| Path | What it is | Read it when |
|---|---|---|
| [`THREAT-MODEL.md`](THREAT-MODEL.md) | The six threats this design actually has, their controls, and the residual risk left over | Before designing anything that touches tenants, personal data, tokens or the replication slot |
| [`SOPS-ONBOARDING.md`](SOPS-ONBOARDING.md) | How to add a secret without ever writing a plaintext one | Your first day, and every time you need a new credential |
| [`CI-CONTRACT.md`](CI-CONTRACT.md) | CI job names, pass/fail semantics, and how to register an artefact for scanning | Before you build an image or a Node service; before Phase 5 CD |
| [`scan-targets.yml`](scan-targets.yml) | **The extension point.** Single source of truth for what CI scans | You want your image or Node project scanned |
| [`scan_targets.py`](scan_targets.py) | Generates the CI matrices from the registry and fails on anything unscanned | — |
| [`check_ignore_policy.py`](check_ignore_policy.py) | Fails the build when a scanner suppression has no reason, no expiry, or a stale one | You are about to suppress a finding |
| [`requirements-ci.txt`](requirements-ci.txt) | The pinned Python toolchain CI and local runs share | You need the same tools CI has |

Configuration files live at the repository root because their tools expect them there:
`.sops.yaml`, `.secrets.enc.yaml`, `.gitleaks.toml`, `.pre-commit-config.yaml`,
`.semgrep/**`, `.semgrepignore`, `.hadolint.yaml`, `.trivyignore`, `.sqlfluff`, and
`.github/workflows/ci.yml`. All are owned by the Security agent.

## The five commands

```bash
pip install -r security/requirements-ci.txt && pre-commit install   # once
pre-commit run --all-files                    # everything the `lint` CI job runs
python3 security/scan_targets.py --check      # is anything shipping unscanned?
python3 security/check_ignore_policy.py       # has any suppression expired?
semgrep scan --config .semgrep/ --error       # the blocking SAST rules
```

## Three things that are true of this repository and easy to get wrong

1. **A secret is either encrypted or outside the repo.** `changeme` is the only value
   permitted in `.env.example`, and gitleaks allowlists that exact string by name — a
   realistic-looking placeholder fails the gate where `changeme` passes.
2. **You cannot add an image or a Node project without adding it to the scans.** An
   unregistered `Dockerfile` or `package.json` fails `discover` in CI and the
   `scan-target-coverage` pre-commit hook locally. Register it in `scan-targets.yml`.
3. **Every suppression expires.** Reason, date and (for Trivy) an owner, enforced in code.
   An entry whose date has passed fails the build until a human re-reads it.

## Raising something

- A finding in another agent's path: tell them directly, with the exact fix. Do not edit
  their files, and do not widen a rule to make it go away.
- A rule that is wrong: say so. A noisy rule is a rule that gets disabled, which is worse
  than no rule — see `.pre-commit-config.yaml`, where 31 of 33 initial ruff findings were
  removed as framework idioms rather than pushed onto two agents as busywork.
- Anything that needs a change under `.github/`: send Security a diff request. That file
  has one writer for a reason (see `CI-CONTRACT.md` §1).
