# `tests/` — the integration suite

Tests for the **seams between components**, which is where unit tests do not reach. Unit tests live
next to their code (`analytics/cdc/tests/`, `analytics/semantic-api/tests/`, `addons/*/tests/`);
this directory is about whether the pieces are telling each other the truth.

```bash
make test                                   # everything
bash tests/run.sh -k live_sync -s           # one test, showing its evidence blocks
bash tests/run.sh -m "not slow" -ra         # skip the long ones, print every skip reason
make test-coldstart                         # DESTRUCTIVE; see §5
```

`-ra` is on by default. **Read the skip list** — a component that does not exist yet produces a SKIP
with a reason, never a pass, so "not covered" is as visible as "covered".

---

## 1. How it talks to the system

Standard library plus pytest, and nothing else. Every database call goes through
`docker exec … psql`; every HTTP call through `urllib`. That is not a workaround for a missing
driver — it means the suite exercises the containers the way an operator does, and it runs on a
machine with no `psycopg2`.

Two consequences worth knowing before you add a test:

- `helpers.db.query()` returns tuples of `str | None`. A SQL `NULL` comes back as `None` and an
  empty string as `""`, which matters because contract 05 makes those mean different things in a
  masked column.
- `helpers.db.grid()` returns psql's human-readable result table, for pasting into evidence
  verbatim. Use it next to an assertion, not instead of one.

---

## 2. Three rules that are not style preferences

### 2.1 Assert the identity before asserting the isolation

RLS is **never evaluated** for a `SUPERUSER` or a `BYPASSRLS` role. An isolation test pointed at one
passes for ever while proving nothing at all. So every isolation test calls
`db.assert_rls_subject(target)` first, which fails loudly if the connection bypasses row security.

There is a second half to the trap. Those booleans render as `t`/`f` in a result grid but as
`true`/`false` through `||`, so a check written against `'f'` silently never matches — and a check
that never matches also never fails. `helpers.db` parses the column and returns a real `bool`.

### 2.2 Discover the target; do not trust the compose file

`conftest.cdc_target` reads `CDC_WAREHOUSE_HOST` off the *running* loader container. This is not
paranoia: at the time the suite was written the compose file said `warehouse-db` while the process
actually running had been started against a single-role superuser fixture database. A live-sync test
that trusted the compose file would have reported on an empty table, and an isolation test would
have passed while proving nothing. Every test prints which database it asserted against.

### 2.3 A zero is only evidence if there was something to hide

`test_05` asserts that the *other* tenant genuinely has rows before treating "zero rows for the
other tenant" as isolation. `test_06`'s UNASSIGNED-OU test **fails** rather than passes when the
dataset cannot distinguish the current behaviour from the bug it replaced. A green test on an empty
comparison is worse than a red one, because it stops anyone looking again.

---

## 3. Layout

| File | Asserts |
|---|---|
| `test_00_environment.py` | what is running; `wal_level`; `warehouse_reader` write denials; the four warehouse roles; the loader's missing grants; contract-05 metadata columns; **that the other stacks on this host are untouched** |
| `test_01_live_sync.py` | create → update → **delete** end to end with real timestamps and LSNs. The most important test here |
| `test_02_idempotency.py` | a second load over the same range changes neither the live projection nor the row count |
| `test_03_reconciliation.py` | warehouse totals == Odoo totals, per table and per day; debit==credit; stock quantity |
| `test_04_masking.py` | personal columns are digests of the *actual* value; `secret` columns do not exist as columns; every landed column is classified |
| `test_05_tenant_isolation.py` | RLS at the storage layer, with the identity asserted first |
| `test_06_cross_tenant_403.py` | the contract-02 403 body, character for character; `allowed_ou` semantics |
| `test_07_token_abuse.py` | tampered signature, `alg:none`, HS256 substitution, foreign key, expired, wrong `iss`/`aud` |
| `test_08_freshness.py` | `last_success_at` advances — and, decisively, **stops** when the pipeline stops |
| `test_09_slot_lag_alert.py` | the alert rules fire at ADR 0001's thresholds and not below them; retained WAL grows with no consumer |
| `test_10_backfill_resumability.py` | `SIGKILL` mid-backfill, resume, byte-identical result |
| `test_11_cold_start.py` | from removed volumes, `make up-dev` + `make up-analytics`. **Destructive**, §5 |
| `test_12_clone_install.py` | every declared file present in a `git clone` of the branch |

`helpers/pdp.py` re-implements the PDP digest **from the specification by hand**. It deliberately
does not import `bct_cdc.pdp_hash`: a test that imported the loader's own function would assert only
that a function agrees with itself.

---

## 4. Markers

| Marker | Meaning |
|---|---|
| `live` | needs the `odoo19-bct` stack running |
| `destructive` | mutates state — creates and deletes records, stops and restarts the loader. Always restores in a `finally` |
| `slow` | over ~60 s |
| `coldstart` | removes this project's volumes. Opt-in only, §5 |
| `notyet` | written against a component that does not exist yet |

---

## 5. The cold-start tests

Gated twice: the `coldstart` marker, and `RUN_COLDSTART=1`. `make test-coldstart` sets both through
`scripts/coldstart-guard.sh`, which snapshots every volume and container belonging to another
project and fails if any disappeared.

That guard matters more than it looks. This host also runs `odoo19-platform-*`,
`odoo19-analytics-*` and `smart-warga-postgres-1`, and `docker volume rm` has no undo. The guard is
a smoke detector, not a fire door: it can detect a raw `docker volume rm` in test code, it cannot
prevent one. Nothing in `tests/` may name a container outside this project, and
`helpers.env.assert_project_scoped` refuses at the point of use rather than by convention.

---

## 6. Adding a test

- Put the *reason* in the docstring. Not what it checks — what goes wrong in production if it does
  not, and how that failure would otherwise present. A test whose docstring only restates its
  assertions cannot tell a future reader whether it is safe to delete.
- Assert on numbers and print them via the `evidence` fixture, so a failure is diagnosable from the
  output rather than needing a re-run under a debugger.
- Prefer a test that **fails as NOT COVERED** over one that passes on a vacuous comparison.
- If a test cannot run yet, `pytest.skip()` with a reason that names the missing component and what
  will make it runnable. Never assert something weaker so it goes green.
- **Never weaken a failing test to make it pass.** Report the failure with the numbers, and route it
  to the code's owner.
