# Analytics Platform — Lead plan

Branch: `feat/analytics-platform` off `main`. Local git only; no remote until approved.

## Deviation from the master prompt — recorded, not hidden

The master prompt's section 0 declares an existing 162-addon Odoo platform as ground truth and
forbids greenfield scaffolding. The operator overrode that on 2026-08-31 and chose **greenfield from
zero** with a **4-addon domain set**. Consequences accepted by the operator:

- The 162 modules, the UU PDP module family, Coretax/e-Faktur and PPh withholding do not exist and
  will not be recreated. Only `custom_pdp_core`, `custom_pdp_masking`, `custom_operating_unit` and
  `custom_ppob` are written.
- Phases 1–5 now include *building* the platform those phases assumed already existed.
- `login-gateway` + Keycloak do not exist. Auth is a new `login-gateway` service authenticating
  against Odoo over JSON-RPC and issuing RS256 JWTs (operator choice).
- Anti-patterns 7.1 ("second Odoo compose stack") and 7.2 ("copying addons out") remain in force —
  there is exactly one Odoo stack in this repo and no addon is copied from anywhere.

## Environment baseline (Lead, verified 2026-08-31)

| Item | Value |
|---|---|
| Docker Engine | 29.4.2 |
| Docker Compose | v5.1.3 |
| CPU / RAM available to Docker | 16 vCPU / 15.25 GiB |
| Free disk on E: | 651 GiB |
| git / python / node / npm | 2.51.2 / 3.13.14 / v24.11.1 / 11.6.2 |
| gh / sops / age-keygen / make | 2.89.0 / 3.13.0 / v1.3.1 / 4.4.1 |
| jq | **absent** — scripts must not depend on it; use python3 |

### Digests pinned at baseline

| Image | Digest |
|---|---|
| `odoo:19.0` | `sha256:f99ffac95cb39a0924622ea4118481c95651d9c84187e5b30a21c2cc4419c7dd` |
| `postgres:16-alpine` | `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685` |
| `redis:7-alpine` | `sha256:ff02b58f971e7d7d156a1267e283fcbbeee91773b6aa36c49dac28ecfe28eadf` |
| `node:22-alpine` | `sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32` |
| `python:3.12-slim` | `sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217` |

## Roster — owned paths

Extended from master prompt §2.1: greenfield needs an owner for the platform itself, so **Platform**
is split into two non-overlapping agents. Every path has exactly one writer.

| Agent | Owns (exclusive write) |
|---|---|
| **Lead** | `docs/agents/**`, `docs/adr/**`, the plan, the gates |
| **Platform-Infra** | `odoo/**`, `postgres/**`, `docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.observability.yml`, `Makefile`, `scripts/**` (except `scripts/analytics/`), `.env.example`, `observability/**` (except `analytics-*`) |
| **Platform-Addons** | `addons/**` |
| **Data Warehouse** | `analytics/dbt/**`, `analytics/warehouse/**`, `docker-compose.analytics.yml`, `observability/grafana/analytics-*.json`, `observability/prometheus/analytics-*.yml` |
| **Backend** | `analytics/cdc/**`, `analytics/semantic-api/**`, `scripts/analytics/**`, `login-gateway/**` |
| **Frontend** | `insight-portal/**` |
| **Security** | `.github/workflows/**`, `security/**`, `.pre-commit-config.yaml`, `.sops.yaml`, `.gitleaks.toml`, `.semgrep/**`, `.trivyignore`, `.hadolint.yaml` |
| **QA & Docs** | `docs/**` (except `docs/agents/`, `docs/adr/`), `tests/**`, `CHANGELOG.md` |

**CI conflict rule (master prompt §2.1): Security owns `ci.yml` and `cd.yml`.** QA never edits them;
QA sends Security a diff request and Security merges it. This is restated in every brief.

## Waves — max 3 agents in parallel (§2.4)

| Wave | Agents | Gate at end |
|---|---|---|
| 1 | Platform-Infra, Platform-Addons, Security(baseline) | **GATE 1** — stack boots, 4+5 modules install clean |
| 2 | Lead writes ADR | **GATE 2** — warehouse engine + `wal_level` approved |
| 3 | Data Warehouse, Backend, QA | **GATE 3** — CDC live, reconciliation + masking tests pass |
| 4 | Frontend, Security(CD), QA | **GATE 4** — 5 views render, 403 cross-tenant proven, CD rollback demonstrated |

Hard dependency **DWH → Backend → Frontend** (§2.4). Frontend may build layout against the frozen
metric-contract fixture only, never against invented shapes.

Security and QA review at **every** gate, including phases they did not build. **Security has veto;
the Lead does not override it** (§2.4).

## Lead review duty (§2.5)

No agent claim is accepted on assertion. For every "done", the Lead re-runs that brief's Evidence
commands and pastes the output. A reported-but-unrun test is the specific failure the Lead exists
to catch.

## MANDATORY — path-limited commits (added at GATE 3 after a near-miss)

**All agents share one git index.** A plain `git commit` commits *everything* currently staged,
including files another agent staged seconds earlier. This is not hypothetical:

- Security ran `git add security/scan-targets.yml && git commit` while Backend had **25 files**
  staged under `analytics/cdc/**`. Only Security's own ruff hook failing aborted the commit. That is
  luck, not a control.
- Commit `28fe2c2c` ("feat(cdc): pgoutput CDC loader…") **did** capture three Platform-Addons files —
  `custom_pdp_masking/models/pdp_export.py`, `custom_operating_unit/hooks.py`,
  `custom_demo_seed/MODULE_KNOWLEDGE.md` — under Backend's message and outside Backend's owned paths.

**The rule, binding on every agent including the Lead:**

```
git commit -m "..." -- path/i/own          # path-limited; ignores the rest of the index
```

Verified behaviour: only the named paths are committed, and another agent's staged files remain
staged and untouched.

### Why this matters beyond attribution

The captured files happened to be syntactically complete and correctly wired. They need not have
been. An agent mid-edit can have a half-written file committed under someone else's message, where
its owner will not look for it and the committing agent does not know it exists — and a security fix
(`pdp_export.py` closes the `export_data` masking bypass) is exactly the kind of file whose apparent
completion matters.

### Lead audit, run at GATE 3

All 45 commits checked for file sets spanning more than one owner. Four hits: three benign
(the Lead's own `.gitignore`; two Platform-Infra commits publishing `04-platform.md`, which that
brief explicitly authorises) and one real — `28fe2c2c`, above. No work was lost. Re-run this audit
before the final merge.

## MANDATORY gate step — verify from a clone, never from the working tree

Added at GATE 3 after `.gitignore`'s unanchored `data/` silently excluded three install-critical
files, including the entire 724-row contract 01 classification seed. Every module's
`__manifest__.py` declared them, so **a fresh clone could not install those modules at all** — while
every test passed, because they ran against a working tree where the files exist on disk.

This bug class is invisible to everything else we run: `git status` shows nothing, the working tree
keeps working, CI on a warm checkout is fine. It surfaces only on a clean clone — which is exactly
what the definition of done promises ("`make up-dev` and `make up-analytics` bring up a clean stack
from a fresh clone, verified on a machine with no prior state").

**Standing rule: gate evidence for anything installable is produced from `git clone` of the branch
into a temporary directory, not from the working tree.** Verified working: clone, install all five
modules into a brand-new database, assert declared data files present, then remove clone, container
and database.

It is the same failure shape as a contract amendment not reaching its producer, and as an isolation
test pointed at a superuser: **the thing that was verified was not the thing that ships.** Three
separate instances of that shape in one session is a pattern, not a coincidence — prefer evidence
gathered from the artefact a user would actually get.

### Related hazards in a shared tree — all three now documented in `security/CI-CONTRACT.md` §8
1. **Shared git index** — a plain `git commit` captures another agent's staged files. Use
   `git commit -- path/i/own`.
2. **Stash window** — only `git commit` (the hook path) stashes the working tree; `pre-commit run`
   by hand does not. Recovery: pre-commit writes each stash to `~/.cache/pre-commit/patch<ts>-<pid>`
   as an ordinary git diff and never deletes it, so a lost edit is recoverable with `git apply`.
3. **Unstable evidence during active waves** — a red result may be a genuine finding in a sibling's
   in-flight work rather than a regression. Re-check before asserting it.

## The dominant defect pattern in this build — a check that cannot fail

Six independent instances, found by five different agents. None was a coding error in the usual
sense; every one was a **verification that returned the right-looking answer for the wrong reason**,
and in every case the surrounding work was correct. This is the pattern to design against.

| # | The check | Why it could not fail | Found by |
|---|---|---|---|
| 1 | Addon "installs cleanly" evidence | Run against a working tree where `.gitignore` had silently excluded three manifest-declared files; a fresh clone could not install at all | Platform-Addons |
| 2 | Contract 01's barcode amendment | Written into the contract while the producer CSV and the live table still said `personal`; the loader reads the table | Security |
| 3 | `git check-ignore` exit code | Exits 0 on a **negation** match too, so a guard asserting "`.env` is ignored" passes even when a negation made it committable | Backend → Security |
| 4 | `rolsuper` compared as `'f'` | Through `\|\|` a boolean renders `true`/`false`, never psql's `t`/`f`, so the comparison never matched and passed forever | Platform-Infra |
| 5 | Alerting believed healthy | `promtool` passes and Prometheus reports `health: ok` without either saying whether a selector matches any series. The real defect turned out to be different and worse: **alertmanager, loki, promtail and node-exporter were not running at all**, so every rule fired into nothing. A cold start rebuilds the base stack via `make up-dev`, which never touches the observability overlay | QA, Platform-Infra |
| 6 | `make install-modules` / `make up-dev` | Reported success while all five modules stayed `uninstalled`; and `.env.example` shipped `ODOO_INIT_MODULES=base,web`, so a fresh clone gets no domain model and `up-analytics` fails hard | Platform-Infra, QA |
| 7 | Lead's replication-slot check | Recorded separately below — the Lead's own instance of this pattern | Platform-Infra |
| 8 | `bct_warehouse_reconciliation_failed` believed live | The exporter scoped its `latest` CTE with `ORDER BY run_started_at DESC LIMIT 1`, and `make dbt-run` excludes tests, so the newest invocation carries **zero test rows** and the whole reconciliation series vanishes. The perturbation proof passed only because it ran `dbt-test` immediately before reading the metric — an order production never uses. Under the real loop (build often, test rarely) the alert is dark while Prometheus reports `health: ok` | QA, verified by Lead |
| 9 | `dim_product_cost.sql` in the working tree | The model existed on disk and dbt built it; it was never `git add`ed, so a fresh clone builds 34 models and silently omits the cost dimension. Identical shape to instance 1 | QA |
| 10 | "Dev password is set" | `BCT_DEV_USER_PASSWORD` exists **only** in the untracked local `.env`. No Makefile target, script, or seed model ever applies it. Login worked all session because an agent set it by hand in a live shell. After the documented `make up-dev`, `authenticate('bct','admin','admin')` returns uid `2` — Odoo's **default** password — while `.env` advertises a strong one that nothing consumes | Lead, from Frontend's report |

### What actually catches this class

Not more tests. **Restoring the broken condition and confirming the check goes red**, before trusting
that it is green for the right reason:

- DWH's reconciliation **perturbation proof** — corrupt a figure, watch the pipeline fail with a
  non-zero exit and a named row, restore, watch it pass.
- Backend's **mutation test** on T-1 — flip `is_local` true→false, watch three tests fail, revert.
- QA's third alert test asserting **samples, not names** — `/api/v1/label/__name__/values` still
  lists names from an earlier window, so a name-presence check reports healthy on exactly this bug.
- QA leaving `test_the_unassigned_ou_branch_is_actually_exercised` **RED as "not covered"** rather
  than filing a note. A red test outlives a paragraph in a report.
- Security's identity-first assertion: every isolation test states `rolsuper=f, rolbypassrls=f` for
  the role under test, because pointed at a superuser it would pass while proving nothing.

### Standing rule

**A check that has never been observed to fail is not yet known to work.** Before any gate accepts a
green result, the author states how they made it go red. If they cannot, the criterion is recorded as
**not verified** — never as passing.

### Instance 7 — the Lead, on the very pattern he had just catalogued

Verifying QA's Finding 4, the Lead ran `curl … | grep -c 'pg_replication_slot'`, got `0`, and
reported four load-bearing ADR alerts as permanently inactive. **The measurement was taken at a
moment when zero replication slots existed**, because the cold start had just destroyed them, and
per-slot series only exist while slots do. Platform-Infra disproved it by creating a slot and
querying Prometheus directly:

```
pg_replication_slots_pg_wal_lsn_diff {slot_name="bct_slot_bct"} = 56
pg_replication_slots_active == 0     -> 1 series (fires)
```

postgres_exporter v0.16 emits **both** the built-in `pg_replication_slot_slot_*` family and the
legacy `pg_replication_slots_*` names the rules already use. No rule expression needed changing and
no `PG_EXPORTER_EXTEND_QUERY_PATH` was needed — a rename would have broken working rules.

The lesson is not that the check was wrong but that **it was run without establishing its
precondition**, which is the same defect as every row in the table above. A `grep -c` returning 0
means "no match", never "the thing is broken" — the two are only the same if you have separately
established that a match *should* exist. This entry stays because the Lead is not exempt from the
rule, and because an error corrected in the record is worth more than one quietly dropped.


### Instances 8–10 — the unifying shape, stated by QA

QA's summary of instances 8 and 9 is the sharpest framing this build has produced:

> a thing that exists locally but not in the clone, and a thing that exists in the database but not
> in the metric — both look fine from where the author is standing.

Instance 10 is the third member of that set and the most dangerous, because it is a **security**
defect wearing the costume of a fixed one. The operator explicitly chose "set a local dev password".
That decision was carried out — in a shell, against a running container, once. It never became a
line of repo. The result is worse than having skipped it:

- a fresh clone gets `admin`/`admin`, Odoo's default;
- `.env` contains a 20-character random string that **looks** like the decision was implemented;
- every piece of evidence that authentication works was collected against the hand-made state.

`.env.example` does not even declare the variable, so a fresh clone has no way to learn it exists.

**Generalised rule, now binding on every agent:** if a step had to be performed by hand for your
evidence to pass, that step is part of the deliverable and is not done until it is in a file the
clone gets. State such steps explicitly in your report under the heading **"performed by hand"** —
an unrecorded manual step is indistinguishable from a fabricated result at the gate.

### Lead re-verification of instances 8 and 9 (§2.5), 2026-08-31

**Instance 9 (untracked model) — VERIFIED.** `find` 35 `.sql` models, `git ls-files` 35, equal;
`dim_product_cost.sql` tracked; `git status --untracked-files=all analytics/` clean.

**Instance 8 (reconciliation metric) — VERIFIED, and note how.** DWH's code is correct but its
summary sentence is not: it reported "both `latest` CTEs now scope to the most recent invocation
that CONTAINED tests". Only one does. `bct_warehouse_dbt_test` (queries.yml:52) carries the
`WHERE resource_type = 'test'` filter and the reconciliation series; `bct_warehouse_dbt_run`
(queries.yml:106) deliberately has **no** filter, because it answers "since anything ran". Adding
the filter there would restore exactly the conflation this finding was about. **The prose is the
hazard, not the code** — a future reader "fixing" the inconsistency would reintroduce the bug.

Live scrape confirmed 7 `bct_warehouse_reconciliation_failed` series and
`count(...) = 7` in Prometheus. **That observation alone proves nothing**, because at the time of
measurement the newest invocation was test-bearing — the one state in which the OLD code also
works. The discriminating condition had to be constructed. Done read-only, without running dbt and
without disturbing two working agents, by evaluating both scopings against a horizon just after a
`dbt-run`:

```
OLD scoping (ORDER BY run_started_at DESC LIMIT 1)  ->   0 series   <- alert dark
NEW scoping (newest TEST-BEARING invocation)        -> 287 test rows
```

**Method note worth keeping.** Reproducing a broken condition does not always require breaking
something. Where the defect is in *which rows a query selects*, the broken state can be recreated
with a `WHERE` clause over data that is already there. That is cheaper than a perturbation, it is
non-destructive, and it can be run while other agents hold the stack.

**A Lead error avoided, recorded because instance 7 was not.** Before the above, the Lead curled
`127.0.0.1` on four guessed ports for the exporter, got nothing, and was one step from reporting
"reconciliation series: 0" as a defect. The exporter publishes **no host port** — `9187/tcp`,
unmapped, scraped by Prometheus over the docker network. The measurement was incapable of returning
anything else. Same shape as instance 7: a probe run without establishing that a positive result was
even possible. The precondition check (`docker ps` ports, then `count(*)` on
`warehouse.dbt_run_result` = 1853 rows / 16 invocations) is what caught it.

### The rule, sharpened by Data Warehouse — the empty-result tell

DWH closed Findings 6 and 7 with the best formulation this build has produced, and it supersedes the
looser wording above:

> Instance 8 (config on disk, not in the process), instance 9 (model on disk, not in the clone) and
> Finding 7 (results in the table, not in the metric) are all "the author's view includes something
> the consumer's does not". The reason none of the three errored is that every one of them is a
> **missing row**, and a query returning nothing looks identical to a query returning nothing to
> worry about.

**Binding rule.** Distrust any check whose passing state is an **empty result**, unless it also
asserts the subject set was non-empty. Concretely, every such check must assert two things, not one:

1. the bad condition is absent, **and**
2. the population it searched was not empty.

QA retrofitted exactly this onto its own suite (minimum subject-set size, printed). Lead's aborted
exporter probe is the same failure from the other side: `curl` returned nothing because the exporter
publishes no host port, and "no output" was indistinguishable from "no problem".

This single rule would have caught instances 1, 5, 8, 9 and the Lead's instance 7.

### Lead verification of DWH's final state (§2.5), 2026-08-31 — all claims hold

```
seed landed          sale_order_line 311 | ppob_transaction 360 | account_move_line 431 | demo users 2
recon_daily          1636 checks | 1636 passed | 0 failed
marts                17 tables, 17 rls_enabled, 17 rls_forced
dim_product_cost     tracked; the 1.46x measurement written into the model at lines 18-30
mart_revenue_daily   bct 777 rows | bct_t2 777 rows
```

**`bct_t2` now holds real rows, which is the precondition the isolation tests were missing.** Until
this moment a cross-tenant 403 and the RLS test would both have passed by having nothing to leak —
the empty-result tell again. Both Frontend and QA have been told to assert the row count first and
the isolation second.

One Lead miss worth recording: the verification query looked for `marts.recon_daily`, which does not
exist — the table is `warehouse.recon_daily`. That is a wrong-schema error in the checker, not a
missing table, and it briefly looked like a discrepancy in DWH's report. Checked before reporting.

### Open gap from DWH's "performed by hand" declaration

Item 2 of DWH's declaration is a finding in its own right, of the instance-10 family: the ordering
**Backend backfills -> DWH resyncs `load-fixture --tenant bct_t2` -> DWH builds** is currently
"coordination by message rather than by a target". `load-fixture` is inside `make up-analytics`, but
the *ordering* relative to Backend's backfill exists only in this conversation. A fresh clone cannot
reproduce it. Owner: Platform-Infra (Makefile), after the credential work. Recorded so it does not
evaporate when this session ends — which is precisely how instance 10 came to exist.

### Instance 11 — the alerting gate had never run a single one of its own checks

Found by QA, reproduced by the Lead against a **healthy** Prometheus:

```
$ curl -s http://127.0.0.1:39090/-/healthy
Prometheus Server is Healthy.            <- text/plain; charset=utf-8, by design
$ make check-alerting
check-alerting: SKIP - Prometheus not reachable at ... (JSONDecodeError).
  NOT a pass: slot-lag alerting is unverified while it is down.
RC=0
```

`scripts/check-alerting.py:85` probes `get("/-/healthy")`; `get()` JSON-decodes every response;
`/-/healthy` is plain text; the bare `except Exception` at line 86 catches the `JSONDecodeError`;
line 90 returns 0. **Every check below line 85 is unreachable code** — scrape targets up, an active
Alertmanager, alert rules resolving to series. None has ever executed.

Two properties make this worse than a broken script, and both generalise:

1. **It exits 0 while printing "NOT a pass".** Every automated consumer — CI, `make`, QA's own test
   — reads success; only a human reading stdout reads failure. A gate whose failure mode is
   invisible to everything that consumes it is not a gate. **Rule: a skip must never share an exit
   code with a pass.**
2. **It defeated the test written to catch instance 5.** QA's cold-start test asserted
   `check-alerting` returned 0. It did, having verified nothing, so the test went green — and its
   `wait_for` never waited, because the first call already "passed". Fixed in `8b7285a` to require
   exit 0 **and** the absence of `SKIP`. QA found and reported this against its own work.

Line 113 carries a second instance of the residue problem: `/api/v1/label/__name__/values` returns
names Prometheus has **ever** seen, not names with current samples, so a series that stopped being
emitted still reads as present. This is the same endpoint semantics behind the Lead's instance 7 and
QA's retracted Finding 4. **Three separate agents have now been fooled by it.** Assert on samples,
never on name presence.

### Gate status after QA's final report — recorded honestly, not aspirationally

PASS, with evidence re-run: live sync incl. delete (create 0.18s / update 0.24s / delete 0.21s
against a 60s budget, reaching `dim_partner` as `is_current` t->f) · cross-tenant 403 byte-identical
incl. for a non-existent tenant · reconciliation 15/15 tables, debit=credit 439,850,000.00 over 431
lines · masking, digest re-derived by hand and 5 `secret` columns absent as columns · RLS, 359
`bct_t2` rows invisible to a `bct`-scoped session · freshness 2.3s -> frozen 35s -> 2.3s ·
idempotency zero difference.

**Not done, with named owners — no item marked green on an assertion that could not fail:**

| Item | Status | Owner |
|---|---|---|
| Cold start from a fresh clone | **FAIL twice** — Finding 5, `ODOO_INIT_MODULES=base,web` | Platform-Infra |
| Alerting live after cold start | **NOT PROVEN** — instance 11 | Platform-Infra |
| Dev credential | **RED by design** — instance 10 | Platform-Infra |
| Slot-lag alert fires live | **PARTIAL** — `promtool` green incl. negatives; live firing not induced (512 MiB WAL, shared host) | accepted as declared |
| Five views render | **not covered** — portal not yet live | Frontend |
| CD rollback demonstrated | **not covered** — no git remote | Security |

**Lead ruling on the third cold start: deferred, deliberately.** Running it now would burn the
seeded stack to re-prove three findings already known red. Order: Platform-Infra lands the three
fixes -> Frontend takes its live evidence on the seeded stack -> one final cold start proves all of
it at once. Recorded here so the deferral is a decision with a reason, not an omission.

**DSAR erasure propagation into the warehouse is NOT automated. It is a manual runbook.** Stated
plainly in `docs/pdp-compliance.md` as §3.2 requires, rather than implied.

### Instance 10 CLOSED — verified by the Lead, negative assertion first

```
admin / admin (Odoo default)               -> false     <- the default is now refused
admin / $BCT_DEV_USER_PASSWORD             -> 2
demo.ou1@contoh.invalid / $BCT_DEV_USER..  -> 5
demo.ou2@contoh.invalid / $BCT_DEV_USER..  -> 6
```

`make seed-demo` also closes QA's "no make target seeds demo data" gap, and `check-dev-passwords`
exits 1 against an uninitialised database — the property `check-alerting` lacked. Note the order of
the assertions: the negative is first, because a check that only tries the good password passes on a
stack that accepts both.

### Instance 12 — a fix that cannot reach the population it was written for

Finding 5 was fixed in `.env.example:139` and remains broken in every existing `.env`, including the
operator's:

```
.env.example:139   ODOO_INIT_MODULES=custom_pdp_core,...,custom_demo_seed   <- fixed
.env:122           ODOO_INIT_MODULES=base,web                               <- still broken
```

The remedy is `make dev-bootstrap`, which merges `.env.example` into an existing `.env`. Trace it —
`scripts/gen-env-secrets.py:159-161`:

```python
if example_value != PLACEHOLDER:
    # Not a secret. Prefer whatever .env already says so hand edits to
    # ports and tunables survive a re-run.
    value = existing.get(key, example_value)
```

For every non-secret key the **existing** value wins. `ODOO_INIT_MODULES` is not a new key — it is
present with the wrong value — so bootstrap preserves `base,web` indefinitely. **The tool built to
repair the environment is the thing that protects the defect.**

The preservation rule is correct; clobbering a hand-tuned port would be worse. The defect is that
the divergence is **silent**, which is instances 8/9/10/11 again: the author's view (`.env.example`
is right) and the consumer's view (`.env` is still wrong) differ, and nothing reports it.

**Generalised rule.** Any mechanism that reconciles two copies of state — config, fixtures, schema,
a lockfile — must **report what it chose not to change**. Silent preservation is indistinguishable
from silent breakage. This is the reconciliation form of the empty-result rule: "no output" from a
merge does not mean "nothing diverged".

Consequence for the deferred third cold start: on this host it reproduces Finding 5 regardless of
what `.env.example` says, until either a repair path lands or the operator edits one line.

## PAUSED BY THE OPERATOR — resume point, 2026-08-31

Work halted at the operator's request. Two agents were stopped mid-task; nothing committed was lost.

**Committed and safe.** Platform-Infra landed `efa6f65` ("make the dev credential, the module set
and the alerting gate real"). QA and Data Warehouse both finished and committed everything they own.

**Uncommitted, on disk only — Frontend.** 31 paths under `insight-portal/` (12 modified, 19
untracked, including `Dockerfile`, `docker-compose.portal.yml`, `src/app/api/`, `src/app/t/`,
`scripts/`, `public/`). The work is in the working tree and intact. It is NOT committed, and the
Lead deliberately did not commit it — it is Frontend's path and the Lead has not reviewed it.
**Resuming Frontend is therefore the first thing to do, before any command that touches the tree.**

**Open, with owners:**

| Item | Owner | State |
|---|---|---|
| Instance 12 — `.env` drift is silent; `dev-bootstrap` preserves `ODOO_INIT_MODULES=base,web` | Platform-Infra | routed, not started |
| Instance 11 — `check-alerting` fixed in `efa6f65`; the fix has NOT been observed to fail | Platform-Infra | needs the red proof |
| Script file modes landed 100644 — Platform-Infra was checking whether that breaks a Linux clone | Platform-Infra | mid-investigation, unresolved |
| Five views render from real data; p95; 403; 375px evidence | Frontend | portal builds; live evidence not taken |
| `account.account` classification + a storable product with no `standard_price` | Platform-Addons | never dispatched |
| Phase 5 SBOM / signing verification | Security | never resumed |
| Third cold start (proves Finding 5, alerting, credential together) | QA | deliberately deferred by the Lead; awaits the three fixes |

**Operator action outstanding:** this host's `.env:122` still reads `ODOO_INIT_MODULES=base,web`.
Until instance 12's repair path lands, a cold start here reproduces Finding 5 regardless of
`.env.example`. One line, or wait for the repair target.

**Do not** mark any deferred item green on resume without re-running its evidence. Twelve instances
in this build say the green would be for the wrong reason.

## RESUMED — instances 13 and 14

### Instance 13 — the Alertmanager check could not fail, found by obeying the standing rule

Platform-Infra fixed instance 11, then tried to make each repaired check go red as required. Check 2
would not. With `odoo19-bct-alertmanager` **stopped**, `/api/v1/alertmanagers` kept reporting
`active=1 dropped=0` for 90 seconds and the gate printed *"Alertmanager reachable"*, rc 0 — while
`:39093/-/ready` refused the connection.

The cause: that endpoint reports the **configured** target from `static_configs`, not a live one,
and Alertmanager is not itself a scrape target. **A firing alert would have gone nowhere with every
gate green.** Now probed directly: stopped -> FAIL rc 1, started -> rc 0.

This one is worth more than the bug. It was found only because the author was required to make a
*passing* check fail, on a fix that was already working. Instance 11's fix would have shipped
green and still had a dark Alertmanager underneath it. **The standing rule paid for itself here.**

`check-alerting`'s first-ever real run also revealed it had been treating the label names
`slot_name`, `wal_status`, `on_breach` and `source_table` as metric names.

### Instance 14 — a fix that arms a trap instead of springing it

Every script in the repo was committed mode `100644`, because this host is Windows with
`core.filemode=false`. `scripts/up-dev.sh:59` executes `"$REPO_ROOT/scripts/init-db.sh"` directly,
so **`make up-dev` was "Permission denied" on a Linux fresh clone** — invisible here, since the
Makefile invokes most scripts as `bash x.sh`. Platform-Infra fixed 15 scripts to `100755` and
correctly stayed out of `scripts/analytics/` (Backend's path), which remains `100644` for five files.

The Lead then found where that condition becomes load-bearing. `.github/workflows/ci.yml:822`:

```bash
FIXTURE=scripts/analytics/dbt-ci-fixture.sh
if [ ! -x "$FIXTURE" ]; then ... "does not exist" ... exit 0; fi
"$FIXTURE"
```

Today this skips honestly — the file genuinely does not exist. **The defect is latent and fires on
delivery.** When DWH/QA commit the fixture from this host it lands `100644`, `[ ! -x ]` is still
true, tier 3 still skips, and the summary asserts the file "does not exist" when it does. CI stays
green and `dbt build` never runs against a fixture everyone believes is active.

**The generalised rule.** A guard must not conflate *absent* with *present but unusable*. Those are
different states with different remedies, and collapsing them produces a message that is actively
false in the second case. Test for existence, invoke mode-independently (`bash "$FIXTURE"`, which is
what every other caller in this repo already does — `tests/helpers/loader.py:26`), and give
"exists but not executable" its own non-green outcome. Routed to Security, who owns `ci.yml`.

### Open, carried forward

- **`SEMANTIC_API_JWKS_URL` drift**, found by Platform-Infra and not planted by it: `.env` says
  `http://odoo19-bct-login-gateway:8080/...`, `.env.example` says `http://login-gateway:8080/...`.
  **Backend's to adjudicate** — one of them is wrong and the drift report will now surface it.
- **An `alertmanager` scrape job** would let check 1 cover its liveness for free. Platform-Infra
  declined to take it: it edits `observability/prometheus/scrape.d` and needs a reload while QA and
  Frontend hold the stack. Recorded as a deliberate deferral, not an oversight.
- **`scripts/analytics/*.sh` are still `100644`** — Backend's path, same exec-bit condition.

### §6 rollback — DEMONSTRATED, re-run by the Lead

The brief requires the rollback demonstrated, not documented. It now is, and the Lead re-ran it
rather than accepting the report:

```
bash security/deploy/test-rollback.sh
  3. pre-deploy backup failure aborts before anything is swapped   PASS exit 3, digest UNCHANGED
  4. unsigned / unverifiable image is refused                      PASS exit 4, never ran
  5. empty verifier fails closed                                   PASS exit 4
  6. deploying a tag instead of a digest is rejected               PASS exit 2
  7. migration failure triggers rollback, not a silent pass        PASS exit 5, rolled back to sha256:6baf4358...
  8. re-deploying the running digest is a no-op that still passes  PASS exit 0
  PASS=19  FAIL=0   ROLLBACK_SELFTEST_OK
```

Safe to run: isolated compose project `bct-deploy-selftest`, every `down` scoped to it. The 23
foreign containers and `odoo19-bct` were untouched — checked before running, not after.

**Scope of the claim, stated precisely.** The deploy/rollback *mechanism* is demonstrated against
real containers and real digests. The *workflow orchestration* in `cd.yml` has never executed and
cannot until a remote exists. Security's own header says so — "THIS FILE HAS NEVER EXECUTED... Do
not read a green CI badge as evidence that this workflow works" — which is the standard this build
asks for, applied by the agent to its own deliverable.

Test 5 deserves note: **"empty verifier fails closed (the 'check that cannot fail' case)"**. Security
wrote a test case named after this build's defect catalogue. The catalogue became design input
rather than a post-mortem.

### Exec bit — closed, with two instructive false positives

Platform-Infra checked QA's 21 shebang-but-not-executable files empirically rather than by
reasoning, and two are correct at `100644` for **different** reasons:

| File | Why `100644` is right |
|---|---|
| `odoo/docker-entrypoint.sh` | `odoo/Dockerfile:65` is `COPY --chmod=0555`. The bit is supplied downstream; a `git chmod` would be **inert**. Verified independently by the Lead. |
| `postgres/init/00-init.sh` | The Postgres entrypoint **sources** non-executable init scripts. Its absence is **load-bearing** — marking it executable changes how it runs. |

One where the bit is supplied later, one where its absence *is* the mechanism. QA's test excludes
both for the right reason (command position, not shebang presence) rather than by luck.

**QA's sharper finding was about its own test.** Its tightened pattern missed
`"$REPO_ROOT/scripts/init-db.sh"` — the very defect that motivated it — because the path was quoted
*and* variable-prefixed. It found this by running the test against the known defect. **A test that
cannot detect its own motivating case is a check that cannot fail, one level up.** QA wrote it wrong
twice and reported both failures rather than only the working version.

**A shared-index hazard nobody had documented.** Committing a mode-only change via the private-index
route leaves the *shared* index still saying `100644` while HEAD says `100755` — a pending **revert**
that the next agent's commit would silently apply. Fix: `git update-index --chmod=+x <file>` after
the commit. Both agents hit it; QA correctly backed out rather than pressing on.

### Routing decisions carried forward

- **`scripts/analytics/*` exec bit** — Backend's path, and QA's test finds none of them in command
  position today. **Latent, not active.** The one place it would bite is `ci.yml`'s `-x` guard,
  which Security is fixing. Not worth waking Backend for.
- **An `alertmanager` scrape job** would let check 1 cover its liveness for free. Deferred by the
  Lead, not forgotten: it edits `observability/prometheus/scrape.d` and needs a reload while three
  agents hold the stack. The direct probe already closes the hole.

### Instance 15 — a CI step that created nothing and reported success

Found by Security while auditing guards of the `-x` shape, and it is worse than the guard it was
sent to fix. `dbt-ci`'s "Create the warehouse role and schemas" step ran the real init SQL from
`analytics/warehouse/init/sql` **without the six psql `-v` variables** that `warehouse-apply.sh`
supplies, and ended in a trailing `|| true`. Measured on a throwaway `postgres:16-alpine`:

```
without the six -v vars:  5 of 6 files ERROR "syntax error at or near \":\""  -> swallowed -> step GREEN
with them:                6 of 6 OK, 5 schemas, 3 NOSUPERUSER NOBYPASSRLS roles
```

**`raw`, `staging`, `marts` and `snapshots` were never created in CI.** Every dbt job downstream was
running against a database that did not have the schemas the step claimed to have made.

The aggravating detail: the step's own comment asserted it used *"the same init SQL the real
warehouse uses, so CI exercises the real roles"*. It exercised none of it. Security also deleted a
hand-rolled `CREATE ROLE` that did `GRANT ALL ON DATABASE`, which `40-grants.sql` does not — **CI
was testing a role production never has.**

Verified by the Lead: `ci.yml:836-838` now passes `wh_user`/`wh_password`, `loader_user`/
`loader_password`, `rls_user`/`rls_password`.

**`|| true` on a step whose failure is the thing you are testing is the compact form of this whole
catalogue.** Three more of the same family in the same audit: `sca-python` exited 0 on an empty
`project-reqs.txt` with the stale reason "no project requirements files yet" (`git ls-files` finds
7); `hadolint` exited 0 on zero Dockerfiles, calling it "reported rather than passed quietly" — but
exit 0 *is* a pass to every consumer; and `scan_targets.py` concluded every "unregistered" check
from an empty difference with no non-empty subject assertion. `cd.yml`: none, both its guards are
the correct shape.

### The `-x` guard, fixed and proven where the defect can exist

This host **cannot represent the broken state** — `chmod 644` does not stick on Windows, which is
precisely why the defect was invisible locally. Security extracted both guards verbatim from the
YAML (old from `HEAD`, new from the tree) and ran them in a Linux container:

| filesystem state | OLD guard | NEW guard |
|---|---|---|
| fixture absent | skip, exit 0 | skip, exit 0 (unchanged) |
| **present, mode 644** | **SKIP, "does not exist", exit 0, dbt never ran** | **RUNS** |
| present, mode 755 | runs | runs (unchanged) |
| path is a directory | exit 126, **empty summary** | exit 1, names the reason |

Note the method: **when the local environment cannot express the failure, move the test to one that
can** — rather than reasoning about it, which is what let the condition survive this long.

### A Lead error, corrected by Security

My Phase 5 brief instructed Security to extend `sca-node` to cover **`login-gateway`**.
`login-gateway/` contains `requirements.txt` and no `package.json` — it is FastAPI/Python. Security
declined the instruction, covered it under `sca-python`, and said so plainly instead of either
following a wrong brief or silently skipping it. Verified by the Lead. **An agent correcting the
Lead's brief with evidence is the behaviour this roster is supposed to produce.**

### Security's near-miss — instance 7's shape, caught in time

Its first `cosign` download failed the published checksum and it was one step from reporting a
supply-chain compromise. The precondition check — file size and type — showed a **truncated
transfer**, 97 MB against 199 MB; the re-download matched exactly. **A checksum mismatch means
"these bytes differ", never "upstream was compromised"**, and the two coincide only once you have
established the transfer completed. Same error as the Lead's `grep -c` returning 0.

### `has_unit_cost` — the clearest statement of the empty-result rule this build has produced

DWH's fixture request was granted, the branch is now exercised, and the numbers explain **why the
column exists** rather than merely satisfying a checkbox. Re-run by the Lead:

```
mart_stock_position, tenant bct
  rows_total                 28
  sum() actually used        27      <- one row skipped
  silently skipped            1
  sum(stock_valuation)   130 190 629 000    <- reads as a finished number
  units unaccounted for     250
```

`sum()` skips NULL **without comment**. Without `has_unit_cost`, a consumer reports that total as
complete while 250 units of real stock sit outside it. Nothing errors, nothing is empty, and the
figure looks finished — which is the whole family in one aggregate. The model's NOT VERIFIED note is
now replaced by these numbers.

The fixture also proved DWH's own analysis right: the two costless products in the original seed were
non-storable (Tips, Down Payment), so they never reached a position row. That correlation was
**structural, not accidental** — more seed volume would never have exercised the branch.

### `account_type` landed — two joins where the obvious choice is wrong

Verified by the Lead: `column_policy` 698 -> **714** (`account_account` exactly 16, as the corrected
gate predicted), `raw` 15 -> 16 tables, `dbt-run` PASS=40, `dbt-test` PASS=292, 17/17 marts
`rls_forced`, recon 0 failed. The fact splits correctly by filter.

Both join decisions are worth keeping, because the default would have been silently wrong:

- **LEFT, not inner, to `stg_account_account`.** `account_id` is nullable on a journal item — section
  and note lines carry none — and an inner join would drop them from the fact entirely, **silently
  changing the set over which `debit == credit` is asserted.** That invariant is the one thing the
  table exists to guarantee. 0 imbalanced days confirmed after the join.
- **`is_profit_and_loss` is NULL, not false, where there is no account.** A section line is neither
  P&L nor balance sheet; `false` would file it under balance sheet by omission — the
  coalesce-unknown-to-zero shape.

The build also caught an assumption DWH would otherwise have shipped: **`account.account` has no
`company_id` in Odoo 19.** The chart of accounts is shared and the per-company code lives in the
`code_store` map. DWH had selected `company_id` by analogy with every other Odoo table, and the model
failed on it — immediately and by name, which is the right kind of failure.

**A Lead measurement note.** My verification of the fact table returned exactly double DWH's figures
(622 vs 311 rows, 879.7M vs 439.85M). Not a discrepancy: DWH scoped to tenant `bct` and I did not,
and `bct_t2` carries a mirror. Checked before reporting, because "the numbers do not match" is the
kind of claim that costs another agent an hour.

### New gap, self-declared and routed — `raw.account_account` is fixture-fed

DWH could not get the table through CDC because Backend's publication does not carry it, so it used
`load-fixture` and **said so** rather than letting `account_type` look fully wired. Confirmed by the
Lead:

```sql
select _lsn::text, count(*) from raw.account_account group by 1;
  0/0 | 104        -- every row at the lowest-precedence sentinel DWH itself defined in contract 05
```

`account_type` is correct today and **will go stale**. Routed to Backend, together with the stale
seven-metric fixture set Frontend is currently building against, and the `SEMANTIC_API_JWKS_URL`
drift. The proof required is a changed `account.account` row arriving with a real LSN — not a
publication that merely lists the table.

### Instance 16 — a check that skipped its subject before it could find it missing

Backend found the root cause of the fixture-fed `account_account`, and it is the purest specimen yet.
`assert_publication_excludes_secrets` (`analytics/cdc/bct_cdc/runner.py:86`) opens its loop with:

```python
if not secrets: continue
```

`account_account` has 16 columns and **zero** `secret`-class ones, so it was skipped **before**
reaching the `columns is None` branch that would have reported it absent from the publication. The
check asked *"did any secret leak?"*, got *"none"*, and had no way to distinguish that from *"this
table is not published at all."*

**What hid it:** the backfill is a plain `SELECT` and does not go through the publication. So the
table was **fully populated and permanently frozen at the same time** — the most convincing possible
appearance of working.

**Generalised rule: a check that `continue`s past a subject cannot report that subject missing.**
Absence of the thing you filter on and absence of the subject itself are different states. This is
the loop-level form of the empty-result rule.

Verified by the Lead after the fix: `pg_publication_tables` now carries `account_account`;
`raw.account_account` holds 108 rows, 5 distinct `_lsn`, **4 CDC-fed**.

**Backend's red proof established its own precondition, which is the discipline propagating.** Before
claiming "nothing arrived", it showed the consumer was demonstrably alive — an Odoo write at WAL
`0/1202D278`, slot `confirmed_flush_lsn` advancing *past* it to `0/1202D2B0`, and still zero rows
landed. Without that control, "nothing arrived" is indistinguishable from a dead consumer, which is
exactly the Lead's instance-7 error. It also kept the red proof permanently as
`test_the_secret_check_alone_cannot_see_a_missing_table`, which runs the **old** check over the
**broken** input and asserts silence.

It further refused a vacuous masking pass: "0 unmasked notes in raw" proved nothing because the
source held zero non-null notes. It set a probe string through Odoo, watched the row land with `note`
NULL and the probe absent, then reverted.

### A Lead error — I relayed an unverified claim as established fact

Frontend reported that the metric fixtures were "still the seven-metric set". **I passed that to
Backend as fact without checking it.** Backend checked: they were **ten**, and had been since
`578623b`, with the working tree matching HEAD byte for byte. Verified by the Lead — the directory
holds 12 files today and held 11 at that commit.

Re-running an agent's evidence is §2.5 and it applies to claims I *forward*, not only to claims I
accept. Routing an unverified report costs the receiving agent real time.

**The finding underneath is better than the correction.** Nothing in this repo could have told anyone
which was true. Frontend's `contract-shape.test.ts` parses every fixture **present** through the
app's own guards — correct design — but `readdirSync` cannot see a file that is not there. **Seven
correct fixtures for a ten-metric registry is a green run.** Completeness of a generated set can only
be asserted on the producing side, which is the only side that knows what the set should be; Backend
added that test, guarded by an explicit "registry >= 10 metrics" assertion first, because every set
comparison below it is satisfied perfectly by an empty registry.

Two more of the same family in that work: `make metric-fixtures` with no token silently wrote
**offline synthetic shapes over live transcripts** — same envelope, same filenames, exit 0 — and now
refuses (exit 2); and `stock_valuation` is declared **with `has_unit_cost` as a dimension**, because
`SUM()` skips NULL and the total otherwise reads as finished.

Backend declined to declare `pnl_`/`balance_sheet_` metrics although now possible: *"a second metric
name for the same measure over a filtered set is a view, not a metric."* Correct, and the restraint
is worth as much as the additions.

### `bash -n` is a syntax check, not a correctness check

Backend nearly shipped the JWKS fix broken by putting a comment **inside** a backslash-continued
`docker run`. `bash -n` passed on **both** the broken and the working version, because the construct
is syntactically fine. Only running the script found it. Recorded because `bash -n` appears in this
repo's tooling and reads like a safety net it is not.

### A conflict that would be absorbed as a no-op — DWH, investigating its own suspected fault

DWH assumed the duplicate `res_partner` rows were its own fixture landing twice, **checked before
answering, and was wrong**:

```
id | _op |   _lsn    | copies | 101 seconds apart, identical payloads
46 | U   | 0/A313AC0 |   2    |
```

Real LSNs, not `0/0` — at-least-once redelivery consistent with a consumer restart resuming from a
`confirmed_flush_lsn` that had not advanced past those changes. All 16 raw tables swept; `res_partner`
is the only one affected. It does **not** reach the marts: `dim_partner` holds 48 current rows with
zero `partner_key`s having more than one current version, and the 48-vs-47 gap that first looked like
an SCD2 duplicate is the unknown member, not a defect.

**The reason it is harmless is narrower than "duplicates are deduplicated", and that is the finding.**
It is harmless *because the payloads are identical*. A redelivery carrying a **different** payload at
the same `_lsn` would be resolved silently by the `_ingested_at desc, _row_id desc` tiebreak — the
correct answer, but **a real conflict absorbed as if it were a no-op**. Backend's metric is the only
thing that would ever say so, which is why it stays.

**Ruling on the 104 fixture rows: leave them.** `_lsn 0/0` is contract 05 §D's lowest-precedence
snapshot layer and the backfill resume landing 0 rows is that rule working, not failing. Digests are
byte-identical, reconciliation is green, and clearing `raw` while Frontend is measuring is risk for no
correctness gain. One caveat recorded rather than acted on: **a fixture-derived base cannot represent
a row deleted between the fixture load and CDC start** — no tombstone would exist. That window was
minutes here with no deletes. The general rule stays "the base snapshot comes from the backfill",
worth doing properly at the next quiet window.

### Open, routed, and deliberately not done today

- **Four reserved-but-undefined make targets** (`up-gateway`, `up-semantic`, `cdc-start`,
  `cdc-status`, Makefile:298). The whole Backend service tier is script-invoked in an order recorded
  nowhere. Instance 10's shape; routed to Platform-Infra.
- **`scripts/analytics/*.sh` at mode `100644`** (six files). Latent — every caller uses `bash x.sh` —
  and becomes load-bearing the moment a make target invokes one directly on Linux. Backend declined
  it deliberately while agents hold the tree, and gave the recipe including the shared-index resync.
- **`is_profit_and_loss = NULL` unexercised.** Real in the model (LEFT join, section lines) but this
  seed has `account_id IS NULL` count 0. Stated in the metric description so that a NULL-free result
  is not read as proof NULL cannot occur.

### Instance 17 — a defect with nothing to notice

Frontend's observation, adopted by Platform-Infra, and it is a sharper specimen than instance 10.

Instance 10 at least had a **tell**: `.env` carried a 20-character random password that *advertised* a
decision nothing implemented. The JWKS host defect had no tell at all. `.env` held the **correct**
value, so on this machine everything worked, every day, for everyone — while `.env.example` shipped a
host that has never been resolvable, and a fresh clone would reject **every valid login** and report
it as a client-side 401.

> Their `.env` masked the defect the way a working tree masked the `.gitignore` exclusion earlier in
> this build, and the 401 would have landed on whoever ran the clone-verification gate rather than
> whoever caused it.

**The generalisation: a local file that is more correct than the tracked one hides the tracked one's
defect completely.** There is no line to spot, no stale comment, no advertised claim — simply nothing
to notice. Instances 1, 10, 12 and 17 are all this shape, and it is why "verify from a clone, never
from the working tree" is a MANDATORY gate step in this plan rather than a nicety.

### Instance 10's shape closed in the Makefile

The four reserved-but-undefined targets are defined (`6998a66`, `f3086d1`), and **the missing artefact
was the order**, which now lives in the targets rather than in a comment:

```
up-dev -> up-analytics -> up-gateway -> up-semantic -> cdc-start

gateway BEFORE semantic-api   the API fetches JWKS from it; reversed, it 401s every VALID login
provision BEFORE run          publication first, slot second - WAL retention and ADR 0001's 2 GB
                              cap start counting the instant a slot exists
```

`up-gateway` runs `gen-jwt-keys.sh` first, and Platform-Infra checked rather than assumed that it
refuses to overwrite: `keep jwt` / `keep jwt-next`, rc 0, against the live keys. That removes the last
manual step between a fresh clone and a working gateway.

**The exec-bit question is decided: `bash`, not `chmod`.** Every recipe invokes
`bash scripts/analytics/x.sh`. The reasoning is the stronger one: **this repo has already proven it
cannot reliably record that bit** — `core.fileMode=false` defeats both `git commit -- path` and
`-c core.fileMode=true`, and the plumbing route leaves a pending revert in an index three agents
share. Adding a target that invoked a script directly would have converted a latent problem into a
load-bearing one on Linux. `bash` costs nothing and cannot rot. `scripts/analytics/` untouched.

### A container trap, and the right instrument for it

`cdc-run.sh` uses `docker run --rm`, so `docker stop` **deletes** the container and `docker start`
then fails with "no such container". `cdc-status` named the remedy but not the reason, which is
exactly what makes an operator reach for `docker start`. Recorded at the recipe.

Platform-Infra's instrument choice is the rule again: **verify with `docker inspect`, not `docker ps`
— the latter cannot distinguish "stopped" from "never existed".** Same family as
`/api/v1/label/__name__/values` returning names ever seen, and as the Lead's `grep -c` returning 0.

### Coordination note

Platform-Infra held ~6 minutes at Frontend's request rather than restarting a service mid-p95-run: a
restart poisons all 300 samples, between runs it costs nothing. Two agents sequencing their own work
through a shared resource without the Lead arbitrating is the roster working as intended.

### Instance 18 — false prose is a defect, and a permanently-firing error is worse than none

Backend traced DWH's duplicate `res_partner` rows to its own loader and fixed it (`f413537`). The
mechanism is a design decision working correctly with a gap the loader should have closed: `flush()`
commits the warehouse transaction and only **then** sends feedback — deliberately, because confirming
an LSN the warehouse has not stored lets Postgres drop that WAL and the rows are lost from both ends.
The unavoidable price is a window: die between the commit and the feedback and Postgres redelivers
changes already landed. **Logical replication is at-least-once; exactly-once *landing* was the
loader's job and it was not doing it.**

The stream now floors itself at `landed_max_lsn()`, read back out of **the data** rather than from
`pipeline_state` — following the rule the loader already stated elsewhere: *a progress table can
disagree with the rows it describes; the data cannot disagree with itself.*

**Made red by recreating DWH's incident rather than reasoning about it** — six writes,
`docker kill --signal=KILL` mid-stream, restart. Caught first attempt, with the skipped change named
and its LSN printed. Its guard tests are the empty-result rule applied to its own fix:
`test_the_floor_is_zero_for_an_empty_landing_zone` exists because **a floor that dropped everything
would satisfy every redelivery test perfectly while silently emptying the pipeline.**

**But the expensive half was the prose, and Backend says so plainly.** Three places in its code
asserted this duplicate had *"no legitimate cause."* It has a well-understood one. That sentence is
why DWH had to go hunting for a fixture artefact in its own snapshots before it could rule itself out
— **a false comment consumed another agent's time**. And the condition logged `ERROR` every 4.7
minutes forever with no remediation, **which is how people learn to ignore errors.**
`insert_rows`' docstring claimed idempotency came from "never re-reading a range": true of the
backfill, false of the stream — the path that actually produced the rows.

All three sites now name the mechanism, say why the marts are unaffected (same WAL record ->
identical payload -> `raw_latest` rank 1 absorbs it), and state that the figure **does not
self-clear**: a constant value is history, only growth after a stable restart is a fault.

**Two rules from this, both binding:**

1. **A comment asserting that a condition cannot legitimately occur is a load-bearing claim.** When
   it is wrong it does not merely mislead — it sends the next agent to search their own work for a
   cause that was never there. Prose is not free.
2. **An alert that fires forever with no remediation is worse than no alert**, because it trains its
   audience to ignore the channel. If a condition is expected, say so and say what growth would mean.

Backend left DWH's two rows in place: *"deleting them would be a write into another agent's data to
make a number look nicer."* Correct.

**New gap, routed to DWH and deliberately deferred:** there is still no unique index on
`(_tenant_id, id, _op, _lsn)`, so exactly-once landing rests on the loader rather than the storage
layer. `raw` DDL is DWH's. **Held until Frontend finishes measuring** — the two agents currently in
the tree have both sequenced around that run, and the Lead will not be the one to break it.

## Phase 4 delivered — §6 items closed, with the red proofs

Frontend finished: 8 commits, 88 files, **every commit path-limited to `insight-portal/`** — audited
by the Lead with `git show --name-only` per commit, all clean. Portal live (`307` to `/login`
unauthenticated, `healthz=200`), 10 evidence artefacts present.

| §6 item | Result | How it was made red |
|---|---|---|
| Five views from real data | PASS, all 11 declared metrics consumed, none undeclared | — |
| Cross-tenant 403 | PASS, 21 assertions | Inverted the middleware comparison: **the two portal tests failed while the semantic-API test kept passing**, proving the guards are independent and the portal test is not riding on the API's |
| p95 < 2 s, 12 months | **39 ms cached / 213 ms uncached**, worst view | Both figures reported, "because only one is honest alone" |
| Freshness not a client clock | PASS 12/12 | Asserts the frozen value is **non-blank** (an empty field also stops advancing) and that it **resumes** (a hardcoded string also never advances) |
| No browser path to the database | PASS | 81 requests over 6 page loads, all to `127.0.0.1:33000` |
| 375 px | PASS | Measured content width **equals** viewport width on all 10 screenshots |
| Keyboard | PASS, 15 checks × 5 views | Real Tab keystrokes over CDP reading `document.activeElement`, not a `querySelectorAll` of what *looks* focusable |
| Export masking | PASS | Asserted against the 32-hex `partner_key` read out of the warehouse by psql, not "differs from plaintext" |

Three properties are **structural rather than remembered**, which is the right way to hold them:
`query()` takes no tenant argument at all, so no URL, header, cookie or form field can reach it;
`claims.ts` maps absent `all_ou` to `false` via `=== true`; the cache keys on the verified session.

**Frontend's own near-miss, worth keeping.** Its first freshness run reported "the timestamp advanced
while frozen" and it was one step from filing a portal defect. The loader had been **recreated inside
its window** — `cdc-run.sh` uses `docker run --rm`, so a `docker stop` deletes it and a re-run
silently un-freezes the test. The proof now asserts the loader is **absent** at both ends.

### A real server defect found by measuring, routed to Backend

Cache disabled, the ten-panel PPOB view issued ten concurrent queries against
`ThreadedConnectionPool(maxconn=8)`. It exhausted and produced **133 upstream 500s in a 300-request
run**. Critically this is **not** the T-1 scope guard: `bct_semantic_pool_guard_trips` stayed `0` and
the documented `503 scope_guard` never appeared — an **undocumented 500** instead. Contract 06 §2 does
not describe the failure mode.

Frontend capped itself at four in flight, so its failed-panel count is 0. **That is a mitigation in
one client, not a fix** — any other caller still gets 500s, and the dashboard currently works because
one consumer agreed to be polite. Routed to Backend with the requirement to choose queue-or-shed and
prove the chosen response by exceeding the limit deliberately.

### Frontend's NOT VERIFIED, carried forward honestly

The container running **in the compose stack** (measured against the standalone host server); a
**fresh-clone build** (all evidence is from the working tree — and given instance 1, that distinction
is exactly the one this build keeps punishing); `is_profit_and_loss = NULL` against live data; silent
token refresh; type-checking of `tests/**`; and CSP, which is Security's.

### An agent correcting a peer's specification — the partial unique index

Backend asked DWH for `UNIQUE (_tenant_id, id, _op, _lsn)` on the raw tables, to move exactly-once
landing from the loader into the storage layer. **DWH found the literal request would break a
documented ADR requirement and built the correct form instead** (`2af19f9`).

A plain unique constraint also forbids **re-running a snapshot**. Every fixture and backfill row
carries `_lsn '0/0'`, and a second full snapshot legitimately re-appends the same keys — ADR 0001
requires the pipeline be re-seedable from snapshot. DWH measured before choosing rather than
reasoning about it:

```
raw.res_partner duplicate groups at _lsn =  '0/0' : 47   <- snapshot re-runs, CORRECT
raw.res_partner duplicate groups at _lsn <> '0/0' :  2   <- genuine redelivery, the incident
```

`WHERE _lsn <> '0/0'::pg_lsn` separates exactly those two populations. Verified by the Lead: **16 of
16 raw tables carry the index, all 16 partial; real-LSN duplicates now 0; the 47 snapshot duplicates
intact.** Both halves are the point — a plain index would have shown 0 and 0, and the second zero
would have been a regression wearing the costume of a fix.

Proven in both directions: a CDC change at a fresh LSN inserts, the same change again raises
`duplicate key value violates unique constraint`, and a re-run snapshot row at `'0/0'` still inserts.

**"Tolerant creation, intolerant reporting"** is the design worth copying. A pre-existing duplicate
makes index creation **warn** rather than fail, so a routine `make up-analytics` does not break over
rows that landed before the control existed — but `verify` now reports any unprotected raw table and
**exits non-zero**. That converts a log line into a gate, and it named `res_partner` on every run
until the cause was cleared.

DWH then removed Backend's two rows, and the change of justification is the interesting part.
Backend had declined to delete them — *"a write into another agent's data to make a number look
nicer"* — which was correct **at the time**. Once the index existed, those rows were the only thing
preventing a structural guarantee on that table. Byte-identity confirmed first
(`distinct_payloads = 1` for both groups), only the later copy of each pair removed, and `dim_partner`
unchanged at 48 current rows — which is the proof that nothing but redundancy went.

**Two agents reached opposite correct conclusions about the same rows because the surrounding
guarantees changed.** Neither was wrong.

DWH's closing note, unprompted and agreeing with Backend's own: *"Backend's most expensive defect was
prose, not code — three comments asserting the duplication had 'no legitimate cause' sent me searching
my own snapshots for a fixture artefact before I could rule it out. The code was fine. A comment can
be the defect."*

### The pool fix — a branch that had never executed, found by applying the standing rule to itself

Backend reproduced Frontend's finding before changing anything: `200 x 248, 500 x 52` with
`{"error":"query_failed","detail":"PoolError"}`, and `bct_semantic_pool_guard_trips` at `0`
throughout, confirming it was never T-1.

**Decision: queue, then shed, in that order — neither alone is defensible.** Queue-only turns a
saturated service into a hung one. Shed-only turns a 15 ms burst into user-visible failure; ten
panels against sixteen connections is a burst, not overload. The implementation detail matters:
psycopg2's `getconn` does **not** block, it raises the instant `used == maxconn`, so the wait is a
`BoundedSemaphore` held across the whole checkout — which makes `PoolError` **structurally
unreachable rather than merely caught.**

Sizing was derived against the database, not the symptom: `max_connections` 40 − 3 reserved = 37,
less dbt ~8, exporter ~3, CDC 3 (measured), operator ~4, margin ~3 = **16**. The arithmetic lives in
`Warehouse.__init__` so it can be re-derived. Backend was explicit that this is not the fix:
*"raising the ceiling moves the cliff from ten panels to seventeen."*

**Then the part worth keeping.** After the fix, 240 requests at **40 concurrent** — 2.5× the new
ceiling — returned `200 x 240, 144 queued, 0 shed`. Backend read that correctly: **the shed branch
had never once executed**, so by the standing rule it was not known to work. It forced the branch on
a throwaway instance under a documented configuration (`maxconn=2`, timeout 1 ms, 60 concurrent):
`503 x 49, 200 x 11, 500 x 0`, `Retry-After: 1`.

A green load test that never reaches the failure path is not evidence about the failure path. Backend
applied the rule to its own fix without being asked.

**One thing the Lead's brief did not name, found by Backend:** `read_freshness` is a **second** pool
checkout per request and sat outside the handler. Left alone, the fix would have been half-done —
the query succeeds, freshness cannot get a connection, and the caller gets an unhandled 500 for a
request whose data was already in hand.

Verified by the Lead: `bct_semantic_pool_max_connections 16.0`, `waits_total` and `shed_total` live,
contract 06 §2 carrying the `503` row and the queue-then-shed rule.

### Two more probes that could not have returned the right answer

- **Backend's `Retry-After: None`.** `dict(e.headers)` destroys the case-insensitivity of an
  `email.Message`; the wire carries lowercase `retry-after` and the header was there all along. It
  was one step from reporting the header missing, and re-measured **before** reporting rather than
  after. Same family as instance 7 and the Lead's exporter-port probe — now five occurrences across
  four agents.
- **`bash -n` again.** Backend broke `semantic-run.sh` twice with comments inside a
  backslash-continued `docker run` — the second time *after* documenting that exact mistake in its
  own previous commit message. `bash -n` passes on both forms. ~1 minute of downtime, self-reported:
  *"an unrecorded self-inflicted outage is worse than a recorded one."*

That second one is the more useful entry. Knowing about a trap, having just written it down, did not
prevent repeating it — which is an argument for a **check**, not for more documentation.

### Arithmetic that lived in a commit message is now a gate

Backend sized its pool against `max_connections` and wrote the arithmetic into a docstring. DWH —
named in it as the largest other consumer — measured its own actual usage, then observed that the
whole calculation was in the wrong place.

**Measured first, which corrected one input:**

```
peak by role during a full dbt build:  warehouse (dbt) 5 | warehouse_loader 3 | warehouse_rls 2
peak total concurrent: 10
```

dbt peaks at **5**, not the 8 Backend budgeted — `DBT_THREADS` is 4, so it is threads+1, and one of
those 5 was DWH's own sampler. Backend's figure was conservative in DWH's favour, so nothing needed
changing. **DWH checked before proposing a correction, and then did not propose one.**

**The real finding:** *"Nobody owns `max_connections`; every consumer sizes its pool against it in
isolation — which is exactly the number that is right the day it is written and wrong three months
later when one consumer is retuned by someone who never saw the others."*

`verify` now computes the budget from the **live** `max_connections` and fails when claims exceed it:

```
OK  connection budget: 31 claimed of 37 usable (6 spare)
```

**Proven able to fail**, re-run with `DBT_THREADS=12`:

```
CONNECTION BUDGET OVERSUBSCRIBED: 39 claimed vs 37 usable (40 - 3 reserved)
     16  semantic-api pool (Backend)
     13  dbt (DBT_THREADS + 1)
      3  CDC loader
      3  postgres_exporter
      4  ad-hoc psql headroom
exit 5
```

Backend's 16 is a **named line** in that output, so raising `DBT_THREADS` tells you whose budget you
are spending rather than merely that you are over.

**The failure mode DWH named is the build's signature shape, caught before it happened:** raising
`DBT_THREADS` is a one-word edit in `.env` with **no local symptom**, and the damage lands on someone
else's service. Exhaustion would surface as a 503 from semantic-api and a failed dbt thread, and
**neither message names the connection budget as the cause** — a real fault whose visible symptom
points elsewhere.

This is the first entry in this catalogue written **before** the defect occurred rather than after.
It is also the answer to the question Backend's `bash -n` repeat raised: knowing about a trap does
not prevent it — **a check does.**

### Instance 19 — `/healthz` returned 200 with its database destroyed

Found by Backend while QA's cold start was tearing the stack down.

```
GET  /healthz   -> 200  {"status":"ok","metrics":11}
POST /v1/query  -> 500  {"error":"query_failed","detail":"OperationalError"}
```

`/healthz` counted registry metrics loaded from a YAML file **at import**, so it answered "ok"
whenever the *process* was alive — never whether the service could answer a single question.

**What made it acute rather than academic:** `login-gateway` and `semantic-api` are started by
`docker run --rm` from `scripts/analytics/`, **not by compose**, so a compose teardown does not stop
them. Two stale containers from a previous session were left running against a destroyed warehouse,
**advertising themselves healthy on ports 38200 and 38120 — the ports the cold start needs.** A
reader would have taken a green from last session's container, not from anything the clone started.
Backend removed both before QA's run.

Now probes `SELECT 1` through the pool, and distinguishes `degraded` (saturated) from `down`
(unreachable) on actionability.

**The failed first proof is the instructive part.** Pointing the service at a nonexistent host makes
it **exit at startup**, because `ThreadedConnectionPool` opens `minconn` eagerly. **The service
cannot start without a warehouse; it can only lose one.** So the proof had to be a green->red
*transition* against a real database that was then removed, not a bad DSN. A whole class of "prove it
fails" attempts is unavailable when the failure mode is unreachable from a cold start.

### Instance 20 — an observation that was true when taken, restated later as present tense

Backend's final report escalated "no make target starts the Backend services" from latent to acute
and sent it to QA **mid-cold-start**. All four targets existed by then — `Makefile:334, 344, 349,
356`, landed in `6998a66`, working tree clean. Backend had grepped before that commit and carried the
result forward.

Caught by the Lead because it **contradicted something the Lead had verified an hour earlier**. QA
would otherwise have recorded a gap that does not exist, or hand-run five commands instead of using
the targets.

**This is instance 18 from the other side, and by the same agent.** Backend had written that its most
expensive defect was prose rather than code — three comments asserting a condition had "no legitimate
cause", which sent DWH searching its own snapshots. Here the claim was true when formed and false
when restated. **Verify a claim at the moment you make it, not at the moment you first formed it**,
and re-check anything you are escalating in severity: escalation is exactly when a stale fact does
the most damage.

The corollary for the Lead: cross-checking an agent's claim against something already verified is
cheap and catches this class. The contradiction was the tell, not the content.

### The preventive gate had the defect it was built to prevent

Backend found that DWH's new connection-budget check reads `DBT_THREADS` live but **hardcodes**
`semantic-api pool: 16`, while `SEMANTIC_API_POOL_MAX` is a documented operator knob — and Backend's
own shed log tells operators that knob is the remedy. Set it to 32 and the check reports *"OK, 31
claimed of 37 usable, 6 spare"* while the real claim is 47.

DWH's own words applied to DWH's own work: *"the number is right the day it is written and wrong
three months later when one consumer is retuned by someone who never saw the others."* **A hardcoded
16 in the checker is that same frozen number, one level up.** Routed with the requirement to prove it
red by raising the knob above the budget.

### Instance 21 — the suggested fix would have been strictly worse than the bug

Backend found DWH's budget check hardcoded `16`, and proposed the obvious one-line fix:
`os.environ.get("SEMANTIC_API_POOL_MAX", "16")`. The Lead relayed it. **DWH checked before editing
and found it would not have worked, while looking exactly as though it had:**

```
inside the dbt container:  SEMANTIC_API_POOL_MAX=[<absent>]   DBT_THREADS=[4]
```

`verify` runs inside the dbt container, which never received that variable. `os.environ.get` there
returns the `16` default **forever**, while presenting as a live reading. In DWH's words: *"strictly
worse than the literal it replaces, because a literal is at least honest about being one."*

It passed the variable through `docker-compose.analytics.yml` and **confirmed the container could see
it before touching the Python.**

**The real fix was printing provenance**, which reframes the defect correctly: the failure was never
the hardcoded number, it was that an asymmetric check did not *look* asymmetric.

```
   16  semantic-api pool      [env SEMANTIC_API_POOL_MAX]
    5  dbt (threads + 1)      [env DBT_THREADS]
    3  CDC loader             [literal - not configurable (runner.py 229/413/444)]
    3  postgres_exporter      [literal - fixed in docker-compose.analytics.yml]
    4  ad-hoc psql headroom   [literal - policy allowance, not a setting]
```

The CDC loader's 3 stays a literal on Backend's evidence — but is now visibly **a claim of record
rather than passing for a live reading.** Every number in a computed check should say where it came
from; a mixture of live and frozen values that renders identically is a lie of formatting.

Made red at Backend's exact scenario: `SEMANTIC_API_POOL_MAX=32` -> `47 claimed vs 37 usable`,
exit 5. **47 is the figure Backend predicted from reading the code** — an independent peer's
prediction used as the check on the fix, which is better evidence than the author's own arithmetic
agreeing with itself.

DWH's own summary of the shape: *"the check was correct on default values, which is the same trap as
a test that has only ever run against data where it cannot fail."*

**Three agents in sequence on one number:** Backend froze an estimate in a docstring; DWH froze it
again in a checker; Backend caught DWH; DWH caught Backend's proposed fix. Nobody was careless. The
number simply has no owner, which is the finding DWH stated at the outset and then demonstrated
twice by accident.

### Instance 20, corrected by its author — the timeline was worse than the Lead's framing

The Lead wrote that Backend "measured before the commit and carried the observation forward". Backend
re-ran the grep rather than accepting that, and produced the real timeline:

```
6998a66  the four targets land          18:24
46a4360  Backend's message to QA        18:48
```

**The claim was false for 24 minutes before it was sent**, and Backend made three commits in that
window without once re-checking a claim it was about to **escalate in severity**. And the five hand
commands it gave QA were not merely redundant — they are **inferior** to the targets: `cdc-start`
already sequences provision-then-run, and `up-gateway` already runs `gen-jwt-keys.sh`, which refuses
to overwrite an existing key. It told QA to do by hand, in a worse shape, something the Makefile
already does correctly.

**Backend's rule, and it is the best formulation of this class in the catalogue:**

> A claim in a report is a **measurement at the moment of the report**, not a memory — and "acute" is
> a word that should force a re-measurement, not follow from one.

Recorded with the author's correction rather than the Lead's softer version, because an agent
sharpening a finding against itself is worth more than the Lead's charity.

### Open diff request, deliberately held — `up-semantic` has no make prerequisite

Backend, verified at the moment of reporting rather than remembered:

```
up-semantic: ## Start the semantic API (run up-gateway FIRST - it fetches JWKS from it)
	@bash $(GATEWAY_SCRIPTS)/semantic-run.sh --detach
```

The dependency is **prose in the help text, not a prerequisite**. `PyJWKClient` does not fetch at
construction (`auth.py:57`), so `make up-semantic` alone starts cleanly and then **rejects every
token** — the service comes up looking healthy and fails only on first query. Instance 19's shape
again: alive is not the same as able to answer.

Low priority precisely because Backend's earlier auth fix made that failure legible — the log now
names the URL, the exception and the remedy instead of reporting a bad token.

**Held by the Lead, not forgotten.** The Makefile is Platform-Infra's, and QA is mid-cold-start
verifying those exact targets. Changing them now would make QA's evidence a measurement against a
moving target. Route after QA lands.

### Instance 22 — the catalogue was read, and the mistake was made anyway

Backend closed its exec-bit item as latent. Verified by the Lead: all five Makefile invocations are
`bash`-prefixed (`339, 340, 345, 352, 353`) plus `$(PYTHON)` for the `.py`, none mode-dependent.

**Its first attempt at that check reported `bash-invoked=0` for all five**, and it nearly reported the
exec bit closed on that basis. The regex searched for the literal `scripts/analytics/`; the Makefile
invokes them through `$(GATEWAY_SCRIPTS)/` (line 331). **A check that returned zero because it could
not see its subject, read as zero problems.**

This is QA's documented mistake reproduced exactly — PLAN already records that QA's tightened pattern
missed `"$REPO_ROOT/scripts/init-db.sh"`, the defect that motivated it, because the path was quoted
and variable-prefixed. Backend hit the **identical variable-prefix blind spot on the identical class
of file, having read that entry earlier the same day.**

Backend on how it caught it: *"I knew from having READ the target bodies twenty minutes earlier that
`gen-jwt-keys.sh` is invoked there. That is not a method — it is luck backed by a good memory, and
the next person will not have it."*

**This is the build's clearest argument that documentation does not prevent recurrence.** Twice now
the same agent has repeated a trap immediately after recording it — `bash -n`, and this. What works
is the mechanical rule applied without recall: **before believing a zero, assert the subject set was
non-empty.** A rule fires whether or not you remember the story behind it.

### Instance 23 — a provenance column that could not say "I do not know"

DWH added the provenance column so budget claims would be auditable. **One commit later, Backend's
remark prompted it to re-read its own entries, and one was wrong:**

```
3  postgres_exporter  [literal - fixed in docker-compose.analytics.yml]
```

That file fixes **nothing** about the connection count — `--disable-default-metrics` and the custom
query path change *what* the exporter queries, not *how many connections it opens*. Nothing pins the
number anywhere. **It was an allowance DWH chose, dressed as a structural constant with a file
citation — inside the very column added to make such claims auditable.**

Now labelled `UNVERIFIED`, with why it cannot be measured from here: the exporter connects as
`warehouse_rls`, the same role semantic-api uses, so `pg_stat_activity` cannot separate them by
`usename`. Isolating it needs a distinct `application_name` or its own role — **worth doing, not
done, stated rather than implied.**

**The design lesson sharpens Backend's taxonomy into four.** "Live reading / structural constant /
policy allowance" silently assumes the author knows which one they wrote. The necessary fourth
category is **UNVERIFIED**, distinct from deliberately fixed:

> A provenance column only helps if it can say *I do not know*; otherwise every entry gets pressured
> into looking like one of the three respectable kinds.

That generalises well past this file. Any schema of justifications without an explicit unknown bucket
manufactures false confidence, because the author must pick *something* and every available option
asserts more than they have.

### Approximating a gate manufactures false findings

DWH ran a bare `ruff check --select=E9,F,B,S` over its own file, got an `S608` hit, and nearly acted
on it. **The project's pre-commit hook ignores `S608` by explicit config**, with a documented reason:
Odoo model code composes SQL by design. Its invocation was enforcing rules the project does not.

Its own summary: *"the second time this session I have checked my work against a stricter standard
than the one governing it, which manufactures plausible false findings. The hook is the authority; I
should invoke it rather than approximate it."*

Worth stating as a rule: **run the gate, do not reconstruct it.** A reconstruction differs from the
real gate in exactly the places someone deliberately configured, and those differences arrive
disguised as findings.

### The closing of the budget thread — four rounds on one number, each correct and each incomplete

DWH's provenance correction landed on Backend's files too: Backend carried the same unverified `~3`
for `postgres_exporter` in `Warehouse.__init__` and in contract 06 §2, and had not checked it either.
It verified both of DWH's claims before copying the correction rather than taking them on trust
(`ac57344`), and both hold.

**The part Backend would have missed without DWH's role observation, and reported against itself:**
it had earlier read `warehouse_rls = 2` out of `pg_stat_activity` and reported it as its pool's
usage. The exporter connects as **the same role**, so that reading *could not have excluded it*. The
figure was right; the attribution was not separable — *"the same error as reading a grep's `0` as 'no
problem' without establishing the search could see its subject."* It restated the docstring rather
than leaving the earlier reading standing.

**The sequence is the argument, and it is the best summary of this whole build:**

1. Backend finds the hardcoded `16` in DWH's check — correct.
2. Backend proposes reading it from the environment — **worse than the bug**; the variable never
   reaches the dbt container, so it would have returned the default forever while presenting as live.
3. DWH catches that and adds a provenance column — correct.
4. DWH's own provenance column contains a mislabelled entry **one line below** the one it just fixed.
5. Correcting *that* exposes the same unchecked figure sitting in Backend's two files.

**Four rounds on one number, each round correct and each incomplete.** Nobody was careless. That is
DWH's opening claim — a number nobody owns — demonstrated rather than argued, and a better case for
putting the check in `verify` than either agent made at the time.

It is also why the closing state of that number is `UNVERIFIED` rather than a figure. The honest
answer was available at every round and nobody reached for it until the fourth, because three
respectable-looking categories were on offer and "I do not know" was not one of them.

### "Measurable" is not "measured" — the label that stayed UNVERIFIED

DWH closed the root cause Backend surfaced: **the exporter was the only consumer without an
`application_name`.** dbt sets `dbt`, `warehouse_ctl` sets `warehouse_ctl`, the exporter's DSN set
nothing — so it was the one connection nobody could name, which is precisely why Backend's
`warehouse_rls = 2` reading could not exclude it. Fixed in `c5094db`, verified through
`docker compose config` rather than by editing and hoping.

**And it left the label at `UNVERIFIED` anyway.** What changed is that the measurement is now
*possible*, not that it has been *taken*:

> Marking it verified because the means exists would be the same overstatement this column was added
> to expose — one commit after I corrected exactly that.

It now reads `measurable via application_name, not yet measured` and carries the query that closes
it. Not run, because QA owns the stack and it is not worth a connection during a cold-start
measurement.

That distinction is worth holding onto generally: **building the instrument is not the same as taking
the reading**, and the gap between them is exactly where an honest label decays into a confident one.

### Two closing formulations, both better than the Lead's

**On the shared number**, DWH: *"a number nobody owns doesn't get fixed by one careful person; it
gets fixed by being checkable."* Four rounds of careful people is the evidence.

**On approximating a gate**, DWH sharpened the rule with an edge the Lead missed: reconstructing a
**stricter** authority produces **false findings**; reconstructing a **looser** one produces **false
confidence**. *Both feel like diligence.* The second is the dangerous direction and the one nobody
notices, because its output is silence.

### Instance 24 — a true sentence made false by someone else's improvement

Both of Backend's copies of the connection budget said the exporter's share **"cannot be measured
from here"**, because it connected as `warehouse_rls` and `pg_stat_activity` could not separate it
from Backend's pool by `usename`. **True when written. False forty minutes later**, once DWH's
`c5094db` gave the exporter an `application_name`.

Backend verified the compose change before editing, then corrected both copies (`ebe35eb`).

**This is a distinct class from instance 20, and the remedies differ:**

| | Instance 20 | Instance 24 |
|---|---|---|
| Cause | You formed an observation, the world moved, you restated it as present tense | **A peer improved something, and a true sentence in your repository silently became false** |
| Signal | None, but re-measuring before asserting catches it | **None at all** — nothing in your tree changed, no test failed, no reviewer would look |
| Remedy | Re-measure at the moment of the claim | **Only catchable by someone telling you** |

The damage is specific and quiet: *a reader would have taken a documented impossibility on trust and
never attempted the measurement.* A claim that something **cannot** be done is uniquely dangerous,
because it forecloses the attempt rather than merely misdescribing it — and it is the kind of
sentence that ages badly precisely when a colleague fixes the underlying limitation.

Both copies now read **MEASURABLE BUT NOT MEASURED**, keeping DWH's distinction: the label stays
`UNVERIFIED` because the means existing is not the reading being taken.

**DWH's closing formulation of the authority rule**, which supersedes the Lead's: *"The hook is
neither stricter nor looser — it is the standard."* Reconstructing a stricter one yields false
findings; a looser one yields false confidence, and the second is worse because it produces no output
to argue with — the same asymmetry as an empty result.

### The Makefile targets are being exercised by the cold start

Observed by Backend from container start times, without probing them — *"that reading is QA's to
take"*:

```
odoo19-bct-login-gateway   11:57:12
odoo19-bct-semantic-api    11:57:16
odoo19-bct-cdc             11:57:20
```

That is `up-gateway` -> `up-semantic` -> `cdc-start`, in the documented order — **the cold start is
using the Makefile targets, not the hand commands Backend wrongly supplied.** So Platform-Infra's
NOT VERIFIED on the container-starting halves, `/healthz`'s new warehouse probe, and the CDC
publication-coverage refusal are all live inside QA's run rather than only in their authors' own.

### Instance 25 — an audit design that required `application_name` of nobody

DWH applied Backend's instance-24 hazard to its own tree. It found no stale sentences — and something
worse. **Its entire attributability design rests on a field it never required:**

- `warehouse.access_audit.application_name` is populated from `current_setting('application_name')`,
  so any consumer that does not set it records **NULL** in the one column naming *which service read
  the data*.
- `log_line_prefix` is `%m [%p] %q%u@%d/%a`, and `warehouse_rls` runs `log_statement='all'`
  **specifically** so a read stays attributable when the caller never calls `log_access()`. Without
  `application_name`, `%a` is empty and that fallback records "someone holding `warehouse_rls`" —
  every serving consumer at once.
- `warehouse_rls` is **deliberately shared** between semantic-api and the exporter, so `usename`
  cannot separate them. `application_name` is the only thing that can.

Three consumers set it — the three DWH wrote (dbt, `warehouse_ctl`, the exporter). The three it did
not write set nothing. **The column existed and the function ran, which is exactly what made it look
like it worked.**

This is the same fact that made Backend's `warehouse_rls = 2` unattributable, **one level up**: there
it cost one figure's attribution; here it costs the PDP audit trail its ability to name a service.

Published as contract 05 §A.6 with the required value per consumer; three routed to Backend.

**And DWH wrote the gap into the clause rather than letting a `MUST` imply coverage.** The test that
would catch a regression — `access_audit.application_name IS NOT NULL` over a real serving period —
**is not written**, and the contract says so:

> A `MUST` with no test behind it is the same false confidence as a provenance label that cannot say
> "I do not know".

### The two hazards, and why one is not a diligence problem

DWH's closing distinction, which settles the pair:

- *"I asserted something stale"* — caught by **re-measuring before you assert**. A diligence
  obligation.
- *"A peer made my true sentence false"* — **cannot be caught that way at all.** Nothing in your tree
  changed, no test failed, no reviewer would look. It is caught only by the peer telling you, which
  makes it a **communication obligation.**

The asymmetry ran both ways in this exchange: DWH would not have found its `application_name` hole
without Backend's message, and Backend would not have found its stale sentence without DWH's fix.
**Neither was looking for it, and neither could have been.** That is an argument for agents reporting
what they changed to the people it touches — not for either of them being more careful.

### A Lead relay error, the second — a dropped hedge becomes a false instruction

DWH wrote that three consumers needed `application_name`, hedging the third: *"(if it connects at
all)"*. **The Lead's relay dropped the hedge** and instructed Backend to set it on "semantic-api,
login-gateway and the CDC loader".

Backend checked rather than complied. Verified by the Lead: `login-gateway/requirements.txt` carries
no `psycopg2`, `sqlalchemy` or `asyncpg`; its source has no database import, no DSN, no `connect(`;
it speaks only `ODOO_URL` over JSON-RPC. **It connects to no database at all.** The row is `N/A`, not
pending.

**Backend's reason for checking is the important part:**

> "Set `application_name` on a service with no connection" is precisely the kind of task one can
> appear to complete — I would have produced a commit, a green test, and nothing real.

That is a new member of this catalogue and it is not a check: **an instruction that can be satisfied
without being real.** Every artefact of compliance would have existed. The catalogue has been about
checks that cannot fail; this is a *task* that cannot fail, and the same discipline applies —
establish that the subject exists before acting on it.

**This is the Lead's second relay error**, and the two share a shape:

| | What was relayed | What was dropped |
|---|---|---|
| 1 | Frontend's "the fixtures are the seven-metric set" | **The verification** — they were ten, and had been for some time |
| 2 | DWH's "three consumers need this" | **The hedge** — "(if it connects at all)" |

Both times the Lead transmitted an agent's claim and, in transmitting, stripped the thing that made
it honest. §2.5 says re-run an agent's evidence; the corollary the Lead keeps missing is that
**forwarding is asserting**. A hedge is load-bearing precisely when it is inconvenient to carry.

### How Backend implemented it, and three decisions worth keeping

- **Set in code, not in the run scripts.** The semantic-api DSN reaches the service by *two* routes —
  Backend's `semantic-run.sh` and DWH's `docker-compose.analytics.yml` — so a value set in one is
  **silently absent from the other**. It now lives at the single point every route passes through.
  Instance 12's shape, avoided at design time.
- **One connection deliberately not covered, and stated rather than skipped:** the CDC
  logical-replication connection, opened directly with a `connection_factory`. It is source-side,
  A.6 governs warehouse consumers, and Backend would not alter a replication connection
  speculatively while QA holds the stack.
- **NOT VERIFIED on the wire**, because both services run from images built *before* the commit. The
  query that closes it is stated.

**On the missing test, Backend split it correctly.** The integration assertion
(`access_audit.application_name IS NOT NULL` over a real serving period) needs a live warehouse and
real traffic — QA's, still unwritten, still declared. What Backend could own without a database is
the regression guard, written on both sides, and it asserts **the exact contract string rather than
non-emptiness** — because `cdc_loader` would satisfy a truthiness check and still break the join a
reader makes against A.6's table. The `MUST` now has unit coverage on one side and a declared gap on
the other, rather than implying either.

### Instance 12 CLOSED — the repair ran on the operator's own machine

`make dev-bootstrap`, run by QA as step 0 of the final cold start. Verified by the Lead in the file
itself, not from the report:

```
.env:139          ODOO_INIT_MODULES=custom_pdp_core,...,custom_demo_seed   (was :122 base,web)
.env:298          SEMANTIC_API_JWKS_URL=http://odoo19-bct-login-gateway:8080/...
.env.example:298  SEMANTIC_API_JWKS_URL=http://odoo19-bct-login-gateway:8080/...   identical
backup            .env.bak-20260831T114159Z
```

The repair output names **what it changed, what it was, and why** — citing that the value was shipped
by `.env.example` until 2026-08-31 and that a fresh clone therefore died on
`relation "pdp_field_classification" does not exist`. All 16 secrets preserved. **`SEMANTIC_API_JWKS_URL`
did not appear in the drift report at all**, because the two files now agree — instance 17 closed by
the same run.

This is the shape the whole instance-12 finding asked for: a defect that a tool was silently
preserving is now repaired **by that same tool**, announced rather than done quietly, with the
divergence report proving the absence rather than a human confirming it.

### Platform-Infra's NOT VERIFIED closed by QA's run

The four Backend targets, which Platform-Infra would not test under Frontend's measurements:

```
up-gateway   rc=0  -> /healthz in 3s
up-semantic  rc=0  -> /healthz in 3s
cdc-start    rc=0  -> publication bct_cdc_bct, slot bct_slot_bct
```

**QA did not record Backend's stale "no make target exists" claim as a finding**, on the Lead's
correction — *"a false entry in the §6 mapping is worse than the gap it describes."*

### QA's first run: 7 passed, 2 failed, and both failures were its own

Reported as its own sequencing rather than as system defects:

- `check-alerting` ran **before** `cdc-start`, so the `analytics-cdc` scrape target did not exist.
  QA's verdict: *"a true statement about an incomplete stack, useless about alerting."* Exactly the
  precondition discipline this catalogue keeps arriving at.
- The cross-tenant assertion ran against a mart with **zero rows**, because nothing had run
  `seed-demo` or `dbt-run` yet. **Its own `bct_t2` precondition caught it** — the guard working as
  designed, on its author.

Two further fixes from that run, both instance-shaped: the slot-active check sampled **once** and
caught `active=f` a moment before the consumer attached (now waits), and per Backend's warning the
**absence** of the `docker run --rm` containers is now asserted before starting them, because a stale
container answers `/healthz` just as well as a fresh one.

**Under watch, not yet a finding:** mid-run, Odoo shows 5 partners, 0 sale orders, 0 operating units
with all five modules installed — `make seed-demo` may not be populating. QA will report it with the
target's output attached if it holds.

### The closing principle — care concentrates blind spots

DWH updated contract 05 §A.6 (`2b117a2`), verifying all three consumers **from source rather than
transcribing** — exact strings, because the clause promises specific values and the unit guards
assert them, and `cdc_loader` or `bct-cdc` would look right while breaking the join a reader makes
against the table. `login-gateway` is recorded **N/A, not pending**, and DWH noted that its own hedge
turned out to be load-bearing.

Its closing observation is the sharpest thing produced in this build, and it is the reason the roster
worked:

> I found my hole because of Backend's message; they found their stale sentence because of my fix.
> Neither of us was looking for either — and the sharper half is that **both findings were in the
> specific thing each of us had just been most careful about. Care concentrates attention, so it also
> concentrates blind spots. Only someone standing somewhere else can see them.**

That explains the whole catalogue better than any of its individual entries. Every instance here was
found in work its author had reason to be confident about:

- QA's cold-start test, written specifically to catch instance 5, was defeated by instance 5's shape.
- Backend's comment asserting a condition had no legitimate cause was in the code it understood best.
- DWH's provenance column, added to expose unaudited claims, contained one on its second line.
- The Lead's replication-slot check, run immediately after cataloguing that exact error class.
- Backend's exec-bit verification, reproducing a blind spot it had read about the same day.

**The conclusion is structural, not moral.** None of these was carelessness, and none would have been
prevented by trying harder — the same attention that produced the good work produced the blind spot
in it. What caught every one of them was **a second agent with a different vantage point**, or a
**mechanical rule that fires without recall**: assert the subject set was non-empty; state how you
made it go red; verify from a clone; run the gate rather than reconstructing it; forwarding is
asserting.

That is the argument for this roster's exclusive-ownership-plus-cross-review structure, and it is the
one thing from this build worth carrying to the next.

### The third variant — a true fact given as the wrong reason

Backend's `connect()` docstring said the replication connection was *"left alone rather than changed
speculatively while QA holds the stack."* **True — and the wrong reason.** DWH had recorded it in
§A.6 as **out of scope by construction** (source-side; §A.6 governs warehouse consumers) and asked
explicitly that nobody take it as a tidy-up.

So the comment invited exactly what the clause forbids. Backend's diagnosis:

> *"While QA holds the stack"* is a condition that **expires**; the next reader finds it expired and
> reads the sentence as permission. **A timing constraint standing in for a scope boundary does not
> stay a note — it becomes a TODO the moment the timing passes.**

And it would have landed a speculative change to a **replication connection** for a reason nobody
could reconstruct. Now states the scope reason and names the correct route if it is ever wanted: a
Platform-Infra request against contract 04.

**Three variants of the prose defect, with three different remedies:**

| Variant | Caught by |
|---|---|
| I asserted something stale | **re-measuring** before asserting |
| A peer made my true sentence false | **the peer telling me** — a communication obligation |
| A true fact given as the wrong reason | **neither** — only re-reading the sentence against the decision it justifies |

The third is the hardest, because every check passes: the fact is correct, the measurement is
current, and no peer's change invalidated it. Only the *relationship* between the reason and the
decision is wrong, and nothing mechanical inspects that.

### The corollary — where review is worth most

Backend's elaboration of DWH's principle, and it inverts how effort is normally allocated:

> I had just written the paragraph explaining why the exporter's number was unverifiable — which is
> why I did not notice DWH had made it verifiable. DWH had just built provenance labelling — which is
> why the mislabelled entry sat one line inside it. Care concentrates attention, so it concentrates
> blind spots **in the same place**: inside the work you are proudest of, where you are least likely
> to look and most likely to defend.

Two conclusions follow:

1. **Review is worth most where the author was most careful** — the opposite of the usual instinct,
   which sends reviewers to the rushed and the unfamiliar.
2. *"This was not two careful agents catching each other's sloppiness. It was two agents standing in
   different places, and the finding required the different place rather than the care."*

The second sentence is the justification for this roster's whole structure, arrived at empirically by
the agents inside it rather than asserted by the Lead at the start.

### The symmetry is the proof

DWH found the third variant in its own §A.6 clause — **the very sentence telling Backend not to make
the change** ended *"and not to be changed speculatively while the stack is held."* Same defect,
same day, in the paragraph written to prevent it. Fixed in `64241e8`, and it **quoted the old
sentence in a note rather than deleting it**: *"the failure mode is more useful to the next reader
than the correction."*

It swept its remaining owned paths — contract 05, README, `bin/`, exporter queries, every dbt model
and test, compose, alert rules, Makefile — and found no other instance.

**Four findings, and the pattern is exact:**

| Author | Wrote, with care | Therefore did not see |
|---|---|---|
| Backend | the paragraph explaining why the exporter was unverifiable | DWH making it verifiable |
| DWH | the provenance column exposing unaudited claims | its own mislabelled entry, one line inside |
| Backend | the docstring for the replication connection | its timing reason posing as a scope reason |
| DWH | the clause forbidding that change | **the identical defect in that clause** |

Every one sits inside the thing its author had just been most careful about. **Neither agent was
sloppy anywhere near those lines** — which is what makes this structural evidence rather than an
anecdote about diligence.

DWH's addition to why the third class is hardest: **a true fact feels like it has earned its place in
a sentence.** Staleness announces itself under re-measurement; a wrong reason does not, because
nothing about it is false.

**The completed set:**

| Class | Caught by |
|---|---|
| Stale assertion | re-measuring before you assert |
| Peer-invalidated | **nothing self-directed** — only the peer telling you |
| True-but-not-the-reason | **neither** — only interrogating the justification against the decision |

### The refinement that makes the third class checkable

Backend swept its own paths for the timing-reason defect and found **zero further instances** — four
candidates, all correctly cleared. But it nearly "fixed" one that was right, and the near-miss
produced the useful rule.

`db.py:171` says the exporter figure is *"Not run: QA holds the stack and this is not worth a
connection during its cold-start measurement."* **That is a timing fact used as a reason — the exact
surface pattern — and it is correct.**

The difference is **what happens when the constraint expires**:

- DWH's §A.6 clause and Backend's `connect()` docstring: expiry converted a **prohibition into
  permission** for a change nobody wants. Defect.
- `db.py:171`: a reader who finds QA finished **should run the query**, and running it is the desired
  outcome. Correct.

> Where the deferred action genuinely should happen later, a timing reason is not a substitute for
> the real one — **it is the real one.**

**So the test is not "does this sentence contain a timing fact" but "when this expires, does the
reader do the right thing?"**

That matters practically. The original question — *is this fact the reason, or a true thing standing
near it?* — requires judgment and is **most uncomfortable exactly where the fact is true**, which is
where it is needed. The reformulation is answerable without judgment. **A rule that fires on surface
form would have broken a correct comment**, which Backend notes is itself an instance of the pattern:
applying a rule by its shape rather than by what it is for.

Backend also flagged that **past-tense narration needs explicit exemption** — a description of a
defect that once occurred cannot expire into permission, because it never granted any. Its reason
generalises: *a sweep that flags correct cases trains the next person to ignore the sweep.* Same
argument as an alert that fires forever training its audience to ignore the channel.

### Standing open at hand-off, all declared rather than hidden

- `application_name` **not yet on the wire** — semantic-api and the CDC loader run from images built
  before that commit. Closing query is in contract 05 §A.6.
- The §A.6 **serving-period assertion is unwritten** — QA's, needs live traffic.
- `up-semantic` has **no make prerequisite** on `up-gateway` — held diff request for Platform-Infra,
  deliberately not routed while QA verifies those targets.
- `scripts/analytics/*` at mode `100644` — **verified latent**, every caller `bash`-prefixed.
- DWH's two long-standing gaps: **backup/restore green round trip**, and the `--into` rehearsal.

### One level up — a rule can also be a true thing standing near the reason

DWH re-judged its own five remaining timing references using Backend's mechanical test, **rather than
assuming its earlier sweep had been right for the right reason.** All five expire into *verify* or
*measure*; none converts into permission. Its note on the difference is the point:

> The single defect the original sweep found remains the only one — but I now know that **because of
> the right test**, not because the grep happened to return one row.

And on Backend's near-miss, which is the recursion worth keeping: Backend applied a rule it had just
helped derive, to its own code, and got a wrong answer — caught by asking what the rule was **for**
rather than what it **said**.

> **A rule can also be a true thing standing near the reason.**

That is the original defect class one level up, and it is why every mechanical rule in this document
is stated with its purpose attached rather than as a pattern to match. A rule applied by its surface
is indistinguishable from a rule applied correctly, right up until it breaks something that was fine.

DWH also accepted the description exemption on Backend's stronger ground: past-tense narration cannot
expire into permission because it never granted any, and **a sweep that flags correct cases costs
more than the instances it catches** — the same trade as an ERROR firing every 4.7 minutes with no
remediation. Severity tracking actionability, arrived at for the third time from a third direction.

## Cold start, interim — two results banked, one Lead verification impossible

### `make seed-demo` works, and QA refused to guess about the earlier run

Reproduced directly against the post-cold-start stack:

```
SEED ppob_transactions=360   sale_orders=120   sale_order_lines=311
     pos_orders=96           stock_moves=248   products=12
DEVPW_RESULT changed=2 unchanged=1 demo_users=2
==> done. Odoo's default 'admin' password is no longer accepted in 'bct'.
```

So the mid-run `0 sale orders` QA flagged was **not** the target being broken. **And QA declined to
say which of two explanations it was.** It had run the first cold start with `-q`, which discarded
the evidence blocks, so it cannot tell from the log whether `make seed-demo` returned non-zero inside
the run, or returned 0 having done nothing. *"I will not guess between those two."* The run in flight
uses `-s -ra` so the answer is in the transcript verbatim rather than inferred.

That is the discipline this whole catalogue argues for, applied to an ambiguity in its own evidence
rather than to someone else's code.

### A real finding — four alert rules are dark on any stack following the documented path

```
WarehouseReconciliationFailing: no current samples for bct_warehouse_reconciliation_failed
WarehouseDbtTestFailing:        no current samples for bct_warehouse_dbt_test_failures
WarehouseBuildStale:            no current samples for bct_warehouse_dbt_run_age_seconds
WarehouseTestsNotRunning:       no current samples for bct_warehouse_dbt_test_age_seconds
  -- "never seen; this rule can never fire, however green it looks"
```

`make dbt-run` is `dbt build --exclude-resource-type test`, so on a **freshly built** warehouse no
test-bearing invocation has ever existed and the exporter has nothing to scope to. **This is not
instance 8 recurring** — DWH's fix correctly selects the newest test-bearing invocation; here there
is none at all. The documented path leaves four rules dark until `make dbt-test` runs once.

QA's cold-start suite now runs both (`bee3ded`), and it correctly declined to decide whether the
Makefile should chain them: *"Platform-Infra's call, not mine to make in their file."*

The overlay itself came back correctly in that run: 5/5 scrape targets up, 1 alertmanager answering.

### The §A.6 test is written, and it skips honestly

`test_access_audit_names_the_service_that_read` measures **first**, then skips with the reason:

```
(unset)  warehouse_admin  1
(unset)  warehouse_rls    2
psql     warehouse_admin  1
SKIPPED: ... NOT YET ON THE WIRE ... Rebuild both images and re-run; this then asserts instead of skipping.
```

It confirms Backend's "not yet on the wire" **independently rather than taking it on report**, keeps
the gap visible, and converts to a real assertion the moment the images are rebuilt. A `MUST` with no
test behind it is a convention; this is the minimum that makes it a rule.

### Lead verification not possible — recorded rather than inferred

The Lead attempted to confirm the alerting finding by querying `warehouse.dbt_run_result` and could
not: **`odoo19-bct-warehouse-db` does not currently exist**, because the cold start has torn the
stack down and is mid-rebuild. The reading will be taken when the run reports. Stated as
**not verified** rather than reasoned around — which is the whole point of the exercise.

### Instance 26 — `ARGS` leaks through `MAKEFLAGS` into every nested `make`

**The Lead's own instruction triggered this.** I asked QA for verbatim evidence, so it ran
`make test-coldstart ARGS="-s -ra"`. `-s` is a **pytest** flag. Both cold starts then died at:

```
error: unknown argument: -s (try --help)
make[1]: *** [Makefile:143: seed-demo] Error 1
```

`seed-demo`'s recipe is `bash scripts/seed-demo.sh $(if $(TENANT),--db $(TENANT),) $(ARGS)`, so it
received `-s` and refused.

**QA proved the mechanism with a two-line makefile rather than reasoning about it:**

```
$ make -f leak.mk outer ARGS="-s -ra"
outer ARGS=[-s -ra]  MAKEFLAGS=[ -- ARGS=-s\ -ra]
inner ARGS=[-s -ra]        <- a wholly separate `make` process, not $(MAKE)
```

A command-line variable is exported through `MAKEFLAGS` to **every** nested `make`, including one
spawned as a separate process by a test.

**One flag produced four red tests, none of them about what they were testing:** no seed -> no CDC
rows -> no marts -> the cross-tenant test erroring on a missing relation, plus four alert rules with
no samples. A cascade that looks exactly like a broken pipeline and is a single argument in the wrong
place.

**QA's earlier refusal to guess is what made this findable.** It had declined to say whether
`seed-demo` *"returned non-zero"* or *"returned 0 having done nothing"* — *"it was the first, for a
reason outside the script."* Had it guessed the second, the investigation would have gone into the
seed generator, which was never at fault.

Fixed on QA's side in `97cc183`: every nested `make` passes an explicit `ARGS=`, which beats
`MAKEFLAGS` on the child's own command line.

**Held for Platform-Infra, not routed while QA is running.** This is not specific to the test suite:
**any** target forwarding `$(ARGS)` to a script inherits an unrelated parent invocation's `ARGS`, and
`make test ARGS='-k foo'` composes the same way. `MAKEFLAGS=` in the recipe, or `unexport ARGS`,
closes it.

### The alerting finding survives, and is independent of the leak

Confirmed by QA as separate: four warehouse rules have **no current samples** because `make dbt-run`
excludes tests, so the exporter has no test-bearing invocation to scope to on a fresh warehouse. Dark
on any stack following the documented path until `make dbt-test` runs once. Also banked from that
run before the cascade: the overlay returned correctly (5/5 targets, 1 alertmanager), and all seven
earlier tests passed **including the credential assertion in both directions** and the four Backend
pipeline targets.

### Two items now held for Platform-Infra

1. `up-semantic` has no make prerequisite on `up-gateway` — it starts clean and rejects every token.
2. `ARGS` leaking via `MAKEFLAGS` into nested `make` and thence into shell scripts.

Both deliberately not routed while QA holds the stack and is verifying those same targets.

## FINAL GATE — verified by the Lead on the cold-started stack

```
portal   307 (-> /login)   semantic 200   gateway 200   odoo 200   grafana 200
marts    bct 777 | bct_t2 777        recon 1636 checks, 0 failed
credential  admin/admin -> false     admin/$BCT_DEV_USER_PASSWORD -> uid 2
check-alerting  5/5 targets, 1 alertmanager answering, 24 rules, 21/21 metrics with CURRENT SAMPLES, RC=0
foreign containers 23, untouched     bct compose 12
```

**"Alerting live after a cold start" is PROVEN** — by the assertion that used to pass on a skip. That
gate spent this entire build unable to execute one of its own checks; it now evaluates 24 rules and
confirms every referenced metric has live samples.

### Run 3: 9 passed, 2 failed — and both failures were QA's own ordering

QA's test ran `up-analytics` **before** `seed-demo`, contradicting `docs/prod-deploy-checklist.md` §3
— which QA wrote. `up-analytics` copies whatever Odoo holds *at that moment* into `bct_t2` over FDW,
so the fixture tenant received 10 rows instead of 2,109. **Nothing errored.**

| | before | after re-running `up-analytics` on a seeded Odoo |
|---|---|---|
| rows into `bct_t2` | 10 | **2,109** |
| `dbt test` | PASS=291 **ERROR=1** | PASS=292 **ERROR=0** |
| reconciliation failures | **714**, every one `bct_t2`; `bct` clean at 0/818 | **0** |

**The 714 reconciliation failures and the empty-tenant failure were the same defect.** Fixed in
`a5e2e6b`, and the hazard is now a checklist item, because *a silently-empty second tenant makes
every isolation claim vacuous and surfaces only as a reconciliation failure attributed to the wrong
thing.*

That is the catalogue's shape one final time, in the run meant to close it: no error, a plausible
number, and a symptom pointing somewhere else.

### The full suite on the cold-started stack

```
70 passed, 4 skipped, 11 deselected in 191.76s
```

QA also reported an earlier attempt showing 5 failures — all "CDC consumer absent", caused by its own
`up-analytics` re-run killing the consumer. Restarted, green. **"Not a defect, and I am not reporting
it as one."**

### NOT PROVEN — declared, with the Lead's own decision named as the cause of the first

1. **The cold start end-to-end since the ordering correction.** There was no fourth teardown, because
   **the Lead instructed QA not to run one** — "a failure with a named cause is worth more than a
   fourth run hoping for green." That instruction stands, and its cost is this: the corrected
   ordering is proven in both directions by direct measurement, but not by a full run.
   Closing command: `BCT_COLDSTART=i-understand-this-destroys-the-bct-oltp-data ASSUME_YES=1 make test-coldstart`
2. **Backup/restore green round trip** — failure direction proven, `--into` rehearsal unimplemented.
3. **Grafana rendered in a browser.**
4. `application_name` on the wire; the §A.6 serving-period assertion.

### Three items held for Platform-Infra, none adopted by the Lead

`dbt-run`/`dbt-test` chaining · the `up-semantic` -> `up-gateway` prerequisite · the `ARGS`/`MAKEFLAGS`
leak. Each was found by an agent that correctly declined to edit another's file.

## The remote exists — CI's first execution

Repo: https://github.com/sarangrumah/bct-analytics-platform (public, operator's decision).
195 commits, PR #1 with 194. **8 jobs passed, 6 failed.** The structural result is that CI executes
and genuinely gates: `ci-gate` went red rather than reporting green over failing dependencies, and
`secrets (gitleaks, full history)` passed, matching the Lead's own local run over 193 commits.

**Security classified all six and found ZERO first-run environment problems.** It expected some.

### Instance 15 CLOSED by execution — and the Lead's consequence was wrong

The Lead diagnosed `dbt-ci`'s doubled `--profiles-dir` correctly, then concluded that tier 1 dying
meant "the init-SQL fix still has not executed." **Wrong — init-SQL runs before tier 1.** Verified by
the Lead in the run's step list:

```
success  Create the warehouse role and schemas
success  Prove dbt is NOT connecting as a superuser
failure  Tier 1 - dbt deps + parse
```

Its log shows `applying 6 init SQL file(s)`, three CREATE ROLE, five CREATE SCHEMA, and the
`count = 5` assertion passing. **Instance 15 — the CI step that created nothing and reported success
— is now verified by execution in CI, not only locally.** Right mechanism, wrong consequence, and it
would have left a closed item recorded as open.

### Instance 27 — a security rule that would have eaten the control it protects

`sast` failed on one finding: `bct-contract02-jwt-weak-algorithm` firing on
`analytics/semantic-api/tests/test_auth.py:113` — **Backend's negative security test**, which mints
an `alg: none` token precisely to prove the verifier rejects it.

> A verifier is only proven to reject `alg: none` by a test that mints one. Blocking it pressures
> Backend into deleting the test to go green — **the rule eating the control it exists to protect.**

This is a new class: a gate whose enforcement destroys the evidence the gate exists to require. The
pressure is real and quiet — the fastest way to a green pipeline is to delete the test, and nothing
would record that the coverage was lost.

Fixed with a **rule-scoped carve-out**, not a `.semgrepignore` path entry, which would have exempted
those paths from **every** rule at once.

**Security's proof of that carve-out failed its own precondition**, and it said so: the old rule did
not fire on its fixture either, because semgrep's **default** ignores exclude `tests/` and the
fixture directory had no `.semgrepignore`, while the repo ships one that deliberately does not.
Copying the real one reproduced CI. *"Instance 7 again, and I nearly shipped a carve-out whose proof
was vacuous."*

### Two more of the family, in the scanners themselves

- **`sast` printed no finding and its artefact was not JSON.** `--json --output F --text` together
  makes semgrep write the **text** report into the file, so the machine-readable artefact was a
  box-drawing table and the console said only "1 finding". Learning *what* failed required
  downloading and eyeballing an artefact. Now one scan, JSON only, findings rendered as `::error`
  annotations — and the renderer **fails when semgrep scanned zero files**, because "no findings"
  over an empty target set is the empty-result tell and a broken config produces exactly that.
- **`sca-python` aborted at manifest 1 of 6 under `set -e`**, reporting one advisory and never
  examining the other five. A six-run loop fixing one manifest at a time would have looked like
  progress.

### Three gates, three questions — keep all three

Security's ruling, and the reason it refused to reconcile them:

> `npm audit` and trivy-fs read the **declared** tree; container-scan reads **what ships**. Frontend
> was right that its mitigation makes `sharp` not ship; the gates were right to fail anyway. Both
> were correct because they were answering different questions — and the container scan then found a
> **CRITICAL** that the declared-tree argument would never have surfaced.

That CRITICAL is `tar` 7.5.11, which is not in any lockfile: it arrives inside **npm and yarn present
in the runtime image**. A Next standalone runtime needs `node`, not a package manager.

**And Security refused the dated exception on principle:** that instrument is for findings with **no
fix**. All of these have one. *"Using the exception mechanism here would spend the one control that
makes genuine exceptions credible, on three findings that are simply fixable."*

### CI after Security's fixes — 8/6 becomes 10/4, verified by the Lead

```
FIXED   dbt-ci · sast (semgrep)
REMAIN  sca-python (Backend) · fs-scan · sca-node · container-scan insight-portal (Frontend)
        ci-gate correctly red
```

Both of Security's passed **for the right reasons**, checked per-step rather than by conclusion:

- **`dbt-ci` reached tiers 2 and 3 for the first time in this build.** Tier 2 ran `dbt compile`
  against real Postgres as the **NOSUPERUSER `warehouse` role** and reported
  `Found 36 models, 2 snapshots, 291 data tests, 1 seed, 19 sources, 482 macros`. That gate had never
  executed — it was unreachable behind the doubled path, and before *that* it would have compiled
  against schemas the init-SQL step was silently failing to create. Two defects stacked, the outer
  one hiding the inner.
- Tier 3 took the **honest declared-skip** path: the fixture genuinely does not exist and
  `ci_fixtures` is `pending`, so `discover` agrees and stays green. The registry backstop is now
  armed in CI — when the fixture lands `discover` warns to flip it, and if it later disappears while
  marked `present`, that is a hard failure.

### Fail-fast is a mild member of the same family

Security's fail-fast repair to `sca-python` earned its keep on its first run. The old gate aborted at
manifest 1 of 6 and reported **one** vulnerable file; auditing all six found **two** — the same
advisory in `analytics/cdc/requirements-dev.txt` **and** `analytics/semantic-api/requirements-dev.txt`.

Under the old gate Backend would have fixed `cdc`, pushed, waited, and discovered `semantic-api` a
run later.

> **A gate that stops at the first finding is a mild form of the same family**, because the passing
> state of every remaining check is *"never evaluated"* — which is indistinguishable from *"clean"*
> to anyone reading the output.

Fail-fast is a reasonable default for a build step and a poor one for an audit: a build has one
answer, an audit has a **blast radius**, and stopping early makes that radius unknowable from any
single run. The Lead's original diff request to Backend named one file because that is all the
previous run could see; corrected to two.

### CI 11/3 — and two additions from Backend

`sca-python` green, verified by the Lead. Progression across three runs: **8/6 -> 10/4 -> 11/3.**
The three remaining are all `insight-portal`, all covered by Security's ruling.

**Backend deviated from the Lead's wording twice, surfaced both, and was right both times.**

1. **`==9.0.3`, not `>=9.0.3`.** The Lead wrote `>=` out of habit. Backend checked the convention —
   **zero ranges across every manifest in the repo**, 59 exact pins in the dbt one alone — and gave
   the stronger reason: *a range makes pip-audit's verdict depend on whatever resolves that day*,
   undermining the reproducibility the loop that just caught this depends on. An exact pin plus
   pip-audit catching the next advisory **is** the loop. Verified by the Lead; deviation upheld.

2. **It verified the bump rather than assuming it.** pytest 9 is a **major**, so a changed collection
   rule would let "passes" hide "collects fewer":
   ```
   cdc           8.3.4 collected 59  ->  9.0.3  59 passed
   semantic-api  8.3.4 collected 59  ->  9.0.3  50 passed + 9 skipped = 59
   ```
   Same totals both sides. A major version bump is exactly where a green suite can conceal tests that
   no longer run.

**A correction to the Lead's attribution, worth a rule.** The Lead called the remaining failures "in
Frontend's path". Backend checked rather than accepting it: `fs-scan` is repo-wide and its log **does**
name `analytics/cdc`, `analytics/semantic-api` and `login-gateway` — as **scanned targets, not
findings**. None of its ten Python dependencies appears in a finding row.

> **"My path appears in the failing job's log" is exactly the shape that looks like ownership and is
> not.**

The conclusion survived; the reasoning behind it did not exist until Backend did that. The failure
mode runs both ways — an agent can burn hours on a job that merely mentions its directory, or dismiss
one that genuinely implicates it.

### Four occurrences, four agents, one shape: a zero that meant "cannot see"

Backend's first read of the `sca-python` log returned zero matching lines and it nearly reported *"the
job audited nothing"*. `gh run view --log` refuses to download while the run is in progress overall —
**even for a job that has completed**. The zero meant "cannot see", not "nothing there".

That is now:

| Occurrence | The zero | What it actually meant |
|---|---|---|
| Lead, instance 7 | `grep -c pg_replication_slot` = 0 | measured when zero slots existed |
| Lead, exporter probe | `curl` returned nothing | the exporter publishes no host port |
| Backend, exec-bit sweep | `bash-invoked` = 0 | regex could not see `$(GATEWAY_SCRIPTS)/` |
| Backend, CI log | zero matching lines | logs undownloadable mid-run |

**Before believing a zero, establish that a non-zero was reachable.** Four agents have now hit this
independently, which is the argument for it being a reflex rather than a lesson.

## CI FULLY GREEN — run 33425815425, `e59afc6`

```
success  ci-gate
success  container-scan × 6   (analytics/cdc, analytics/dbt, insight-portal,
                               login-gateway, odoo, semantic-api)
success  dbt-ci · discover · fs-scan · hadolint · lint · sast · sca-node
         sca-python · secrets (gitleaks, full history)
```

**Progression across five runs: 8/6 -> 10/4 -> 11/3 -> 16/0.** Every failure was a real finding or a
real defect; **Security's audit found zero first-run environment problems** where it expected several.
The one exception was a genuine transient — a Docker Hub `i/o timeout` on the buildkit frontend,
established as such from its log rather than assumed, and cleared by a re-run.

### What the green actually cost, by owner

| Owner | Fixed |
|---|---|
| Security | `dbt-ci`'s doubled `--profiles-dir`; `sast` writing a text report into a JSON artefact; a rule that would have destroyed a negative security test; `sca-python` stopping at manifest 1 of 6 |
| Backend | `pytest` 8.3.4 -> 9.0.3 in **two** manifests (the second only visible after the fail-fast fix) |
| Frontend | `postcss`/`sharp` overrides; npm, yarn and corepack removed from the runtime image; `libcrypto3`/`libssl3` pinned to `3.5.8-r0`; a Windows-generated lockfile that no clone could build from |

### Frontend's last judgement call — removing a check rather than silencing a rule

It wrote an in-build assertion (`apk info -v | grep -qx …`), hadolint flagged **DL4006** (pipe without
`pipefail`), and the tempting fixes were a `SHELL` directive or a rule suppression. **It dropped the
assertion instead**, because `apk add` with an exact constraint **already fails the build** with
"unable to select packages" if the pin stops resolving. The grep only restated what the pin
guaranteed.

> The version is now checked in the built image, which is the artefact that ships — a stronger check
> than one inside the build, and it removed the lint finding rather than silencing it.

That is the right disposition of a redundant check: not suppressed, not worked around — **deleted,
because something else already covers it**, with the stronger check kept.

It also derived `3.5.8-r0` by querying the base image (`apk policy`) rather than reading it off the
scan output, and put that command in the Dockerfile comment — transferring the *shape* of
`odoo/Dockerfile`'s house pattern (ARG-pinned exact version, upgrade-only, resolution command
recorded) rather than its `apt` commands.

### Instance 28 — a lockfile that only one operating system could install

Frontend's first `npm install` ran on Windows. npm pruned the platform-specific optionals and wrote a
lockfile carrying only the win32 `sharp` variants. **Host install fine, `npm audit` clean, every gate
green** — and `npm ci` inside Alpine failed outright:

```
npm error Missing: @emnapi/runtime@1.11.3 from lock file
```

**A fresh clone could not have built the image, while the working tree looked healthy throughout.**
Instance 1's shape — the thing verified was not the thing that ships — and it surfaced only because
Frontend moved its verification **into the container**. The lockfile is now generated inside the
pinned `node:22-alpine` image and carries all 31 platform variants; the command is in the README so
the next person does not rediscover it.

### Still open, unchanged and declared

`cd.yml` **has never executed** — it triggers on tag, and no tag exists. Backup/restore round trip and
the `--into` rehearsal; Grafana in a browser; `application_name` on the wire; the §A.6 serving-period
assertion; a full cold start since the ordering correction; a fresh-clone `docker build` of
`insight-portal`. The three held Platform-Infra items: `dbt-run`/`dbt-test` chaining, the
`up-semantic` prerequisite, and the `ARGS`/`MAKEFLAGS` leak.
