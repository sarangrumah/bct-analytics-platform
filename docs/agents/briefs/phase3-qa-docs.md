# Brief: QA & Docs — Phases 3–5 (integration tests, merge order, documentation)

## Objective
Independent proof that the platform does what the other agents say it does, and documentation that
matches what was actually built rather than what was planned. You are the agent whose job is to
disbelieve the others' reports until a test you wrote passes.

## Read first
- `docs/agents/PLAN.md` — roster, waves, gates.
- All four contracts in `docs/agents/contracts/`, plus `docs/adr/0001-analytics-warehouse.md`.
- Every brief in `docs/agents/briefs/` — the acceptance criteria in each are the specification you
  are testing against.

## Ground truth
Project `odoo19-bct`. Ports: odoo `38069`, postgres `35432`, warehouse-db `35433`, login-gateway
`38120`, semantic-api `38200`, insight-portal `33000`, grafana `33001`, prometheus `39090`.
Other live stacks on this host (`odoo19-platform-*`, `odoo19-analytics-*`, `smart-warga-postgres-1`)
**must not be disturbed** — scope every compose command `-p odoo19-bct`, never `system prune`,
`volume prune`, or an unscoped `down`.

## Scope — in

### A. `tests/` — integration and e2e
Write tests that exercise the **seams between agents**, which is where unit tests do not reach:

1. **Live sync, end to end with timestamps** (§6, mandatory): create → update → delete a record in
   Odoo; assert it appears, changes, and **disappears** from the mart within the ADR's stated SLA.
   This is the single most important test in the repo; it is what distinguishes a live mart from a
   nightly dump.
2. **Idempotency**: run the load twice over the same range, diff marts, assert zero difference.
3. **Reconciliation**: warehouse totals equal Odoo totals for revenue, debit==credit, and stock
   quantity, per day per tenant.
4. **Masking**: assert a `personal`-classified field is unreadable in the warehouse, and that a
   `secret`-class column does not exist as a column at all.
5. **Tenant isolation**: a tenant-scoped DB connection returns zero rows for another tenant (RLS),
   **and** a tenant-A token requesting tenant B via the API returns **403** with the contract-02 body.
6. **Token abuse**: tampered signature, `alg: none`, and HS256-substitution tokens are all rejected.
7. **Freshness**: `meta.last_refreshed_at` tracks `warehouse.pipeline_state` and stops advancing when
   the pipeline is stopped — proving it is not a client clock.
8. **Slot-lag alert** (§6): stop the pipeline, assert slot lag grows and the Alertmanager rule fires.
9. **Backfill resumability**: kill the snapshot mid-run, restart, assert it resumes.
10. **Cold start** (§6): from a clean state, `make up-dev` and `make up-analytics` bring up the stack
    on a machine with no prior state. Verify with volumes removed **scoped to this project only**.

### B. Documentation
- `docs/architecture.md` — what was actually built, with a diagram of the data path from Odoo WAL to
  dashboard pixel.
- `docs/runbooks/analytics-pipeline.md` — **required by §6.** Must cover: what to do when the
  replication slot is dropped by the 2 GB cap; how to re-seed from snapshot; how to diagnose a failing
  reconciliation; what a firing slot-lag alert means and the exact remediation.
- `docs/cicd-activation.md` and `docs/prod-deploy-checklist.md` — **Security drafts the content and
  sends it to you via the Lead; you own the files.** (§2.1 inverse: they own the workflows, you own
  the docs.)
- `docs/pdp-compliance.md` — how UU 27/2022 obligations are met by the warehouse, and, critically,
  **whether DSAR erasure propagation is automated or a manual runbook.** §3.2 requires this stated
  explicitly rather than implied. If it is manual, say so plainly.
- `CHANGELOG.md`.

### C. Merge order and branch hygiene
You own the merge order and resolve conflicts (§2.4). Conventional commits, one logical change per
commit, nothing on `main`.

## Scope — out
- **`.github/workflows/ci.yml` and `cd.yml` — Security owns them. You do NOT edit them.** Send
  Security a diff request for any test job you want in CI, via the Lead, and Security merges it.
  This is the explicit §2.1 conflict rule.
- Source code of the services you test. If a test fails, you report it to the owning agent through
  the Lead; you do not fix their code.
- `docs/agents/**` and `docs/adr/**` — Lead owns those.
- `security/**` — Security.

## Contracts consumed
All of them. You are the agent that verifies the contracts were honoured, not merely declared.

## Constraints
- **A test that cannot run is reported as not-run.** Never mark a criterion satisfied on the basis of
  another agent's assertion — re-running their evidence is the entire point of this role.
- Tests must be runnable by `make test` and in CI against a seeded fixture.
- No test may depend on a container outside project `odoo19-bct`.
- Do not weaken a failing test to make it pass. Report the failure with the numbers.

## Acceptance criteria — testable statements only
1. Every item in §6 "Definition of done" maps to a named test in `tests/`, or is explicitly listed as
   not covered with a reason.
2. `make test` runs the suite and reports pass/fail per test.
3. The live-sync test demonstrates create/update/**delete** with real timestamps.
4. The cross-tenant 403 test and the RLS test both pass.
5. The masking test asserts against the **actual stored value**, not a mock.
6. The cold-start test runs against genuinely removed project volumes.
7. `docs/runbooks/analytics-pipeline.md` covers the dropped-slot recovery path.
8. `docs/pdp-compliance.md` states unambiguously whether DSAR erasure is automated.
9. The docs describe what exists — every claim traceable to a file.

## Evidence required — paste the output of exactly these
```
make test 2>&1 | tail -60
docker compose -p odoo19-bct ps
# the live-sync test with its timestamps, in full
# the cross-tenant 403 assertion output
# the reconciliation output including the actual totals compared
git log --oneline feat/analytics-platform | head -40
docker ps --format '{{.Names}}' | grep -E 'odoo19-(platform|analytics)|smart-warga' | head
```

## Escalation triggers — stop and return to Lead
- A test fails and the fix belongs to another agent. Report it; do not fix their code.
- An acceptance criterion from another brief cannot be tested as written.
- A DoD item is unachievable in this build — say so and recommend killing that item rather than
  shipping a test that pretends.
- You need to edit `ci.yml` or `cd.yml`.
