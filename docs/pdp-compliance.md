# UU 27/2022 (PDP) compliance — what the warehouse actually does

Indonesia's *Undang-Undang Nomor 27 Tahun 2022 tentang Pelindungan Data Pribadi* obliges a
controller to classify personal data, minimise what it processes, secure it, and honour a data
subject's rights — including erasure.

This document describes **what is implemented in this repository**, with a file or a query behind
every claim. Where an obligation is met by a person following a procedure rather than by code, it
says so in those words. An obligation that is described in the passive voice, with no file behind
it, is not implemented.

The single most important sentence in this document is in §5:
**DSAR erasure propagation into the warehouse is a manual runbook. It is not automated.**

---

## 1. Classification — the taxonomy exists and is enforced

Five classes, frozen in `docs/agents/contracts/01-classification.md` and implemented twice: once in
Odoo as a registry, once in the warehouse as a policy table the loader executes.

| Class | Meaning | Warehouse transform |
|---|---|---|
| `public` | already public | `none` |
| `internal` | business data, not about a person | `none` |
| `personal` | *data pribadi umum* — identifies a person | `hmac_sha256` |
| `sensitive` | *data pribadi spesifik* — health, biometrics, finances, beliefs | `hmac_sha256_nullable`; free text → `NULL` |
| `secret` | credentials, tokens, integrity hashes | `drop` — never selected, so it cannot land |

**Where it lives**

- `addons/custom_pdp_core/models/pdp_field_classification.py` and its seed
  `addons/custom_pdp_core/data/pdp.field.classification.csv` — the registry inside Odoo, one row per
  physical column.
- `warehouse.column_policy` — the same taxonomy as data the loader reads at startup. Currently
  **698 rows**: 19 `public`, 628 `internal`, 27 `personal`, 19 `sensitive`, 5 `secret`.

**Unclassified is a hard failure, structurally rather than by convention.** Three things make it so:

1. the loader derives its table list *from* the policy, so an unclassified table is not replicated
   rather than replicated unmasked;
2. `warehouse_loader` holds no `CREATE` on schema `raw`, so it cannot invent a landing table for a
   column that has no policy row (verified: `permission denied for schema raw`);
3. `tests/test_04_masking.py::test_every_landed_column_is_classified` fails if any column present in
   `raw.*` has no row in `warehouse.column_policy`.

Neither the DWH agent nor the Backend agent may default a column to `public`.

---

## 2. Minimisation — `secret` columns are absent, not masked

Five columns are classified `secret` and mapped to `drop`:

```
account_move.access_token       account_move.inalterable_hash
pos_order.access_token          pos_order.ticket_code
sale_order.access_token
```

They are **not** selected by the loader, so they do not exist as columns in the warehouse at all.
That is stronger than nulling them: a column that does not exist cannot be exposed by a `SELECT *`,
cannot appear in a `pg_dump`, and cannot be picked up by a future model whose author did not know
the history.

Asserted by `tests/test_04_masking.py::test_secret_columns_do_not_exist_as_columns`, which reads
`information_schema.columns` rather than trusting the loader.

---

## 3. Pseudonymisation — how, and what it is and is not

Every `personal` value is replaced **during load**, before it is written, by

```
HMAC-SHA256(key = per-tenant salt, message = UTF-8 bytes of the value)  →  64 lowercase hex chars
```

Specified in `addons/custom_pdp_masking/MODULE_KNOWLEDGE.md` §2 and implemented identically in
`addons/custom_pdp_masking/models/pdp_hash.py` and `analytics/cdc/bct_cdc/pdp_hash.py`. The loader
asserts agreement over JSON-RPC at startup against `pdp.masking.rule.get_digest_spec()`, because a
silent divergence would break joins rather than raise an error.

Four properties matter legally as well as technically:

- **The salt is the HMAC key, not a prefix.** `sha256(salt || value)` is a different and weaker
  construction; `tests/helpers/pdp.py::salt_concat_sha256` exists purely as a negative control so
  that a loader which quietly used it would fail a test.
- **The salt is per tenant.** The same email address under two tenants produces two different
  digests, so tenants cannot be joined to each other even by someone holding both extracts.
- **The salt never reaches the ERP.** It comes from `WAREHOUSE_MASK_SALT_<TENANT>` in the
  environment (SOPS-managed, `changeme` in `.env.example`). The loader connects to Odoo as
  `warehouse_reader`, which cannot read `ir.config_parameter`, so an ERP compromise does not leak
  the key that would make the digests reversible.
- **Empty string maps to `NULL`, never to a digest.** Hashing `""` would give every blank cell one
  shared non-NULL value — a fabricated join key that would merge unrelated people.

### What this is not

**Pseudonymisation is not anonymisation, and the warehouse is not out of scope.** A digest is
deterministic: with the salt, any candidate value can be tested. So warehouse contents remain
*data pribadi* under UU 27/2022, and the salt is the control that keeps them pseudonymous. Two
consequences follow, and they are stated here so nobody has to infer them:

1. The salt is a security control of the same weight as a password. It belongs in SOPS, never in git,
   never in a log line, never in an HTTP response.
2. **Changing a salt is a migration, not a rotation.** Every digest already stored was computed with
   the old key, so the same person acquires two identities and their history splits silently. To
   rotate, re-load the warehouse.

Verified end to end rather than asserted: `tests/test_04_masking.py` takes a real cleartext email out
of Odoo, finds that partner's row in `raw.res_partner`, and asserts the stored value equals the
digest computed independently in the test — and that the cleartext appears nowhere in the column.
`tests/test_01_live_sync.py` does the same for a record it creates itself, proving masking happens
*during* load: there is no window in which the warehouse holds the personal value.

---

## 4. Access control and audit

### 4.1 Storage-layer tenant isolation

Every mart carries `ENABLE` **and** `FORCE ROW LEVEL SECURITY`, applied by a dbt `post-hook` so it
survives `--full-refresh`. The serving identity `warehouse_rls` is `NOSUPERUSER NOBYPASSRLS` and
matches only the tenant policy, so with `app.tenant_id` unset it reads zero rows — fail closed.

Measured, as `warehouse_rls` (`rolsuper=f, rolbypassrls=f`), across all 16 marts:

> **13,755 rows belonging to tenant `bct_t2` exist in the marts, and none were visible to a session
> scoped to tenant `bct`.**

`tests/test_05_tenant_isolation.py` asserts its own identity first, and separately asserts that the
other tenant *has* rows — otherwise "zero rows for the other tenant" would be the absence of data
rather than the presence of isolation.

### 4.2 In-Odoo masking

`custom_pdp_masking` masks personal fields in the Odoo UI and — the case that is easy to miss —
in `export_data`, so a user with `base.group_allow_export` but without the PDP viewer group receives
`***8a2b1f58` rather than cleartext.

### 4.3 Audit

Three layers, because no single one is sufficient (`docs/agents/contracts/05-warehouse.md` §B):

1. `ALTER ROLE warehouse_rls SET log_statement='all'` — applied by the *server*, so a client cannot
   opt out. Asserted by `tests/test_05_tenant_isolation.py::test_statement_logging_is_applied_by_the_server`.
2. `warehouse.log_access()` — records the semantic fact (which metric, which tenant scope, how many
   rows) that a raw statement log cannot reconstruct. `SECURITY DEFINER`, and it reads the tenant
   scope from `app.tenant_id` *inside* the function, so an audit row cannot claim a scope the query
   did not run under.
3. RLS itself, so an unattributed read returns zero rows rather than another tenant's data.

**Stated plainly rather than implied:** Postgres cannot trigger on `SELECT`, and
`postgres:16-alpine` does not ship `pgaudit`, so layer 2 **cannot be made mandatory inside the
database**. A client that forgets to call `warehouse.log_access()` is caught by layer 1 and by
nothing else.

---

## 5. Data subject rights — and the erasure answer

### 5.1 The answer, unambiguously

> **DSAR erasure propagation into the warehouse is NOT automated. It is a manual runbook, performed
> by an operator, and it is documented in §5.4 below.**

There is no scheduled job, no Odoo model, no API endpoint and no script in this repository that
erases a data subject from the warehouse. `grep -rli 'dsar|erasure|right.to.be.forgotten|
subject_request' addons/ analytics/ scripts/ docs/` returns nothing outside this file and the QA
brief that asked the question. The reader should not have to discover that by grepping, so it is
stated here first.

**This is a gap against UU 27/2022 Pasal 8 and 16(1)(f)** (the right to erasure, and the
controller's duty to stop processing on withdrawal of consent) for any deployment with real data
subjects. It is recorded as a gap rather than presented as a design choice. See §7.

### 5.2 What *is* automated, and why it is not sufficient

A **delete in Odoo does propagate**, quickly and provably. Logical decoding sees `unlink()`, the
tombstone lands in `raw.*` with `_op='D'`, and the mart's latest-non-deleted projection stops
returning the row. Measured end to end on this stack:

```
CREATE landing latency 0.16s | UPDATE 0.15s | DELETE 0.20s   (budget 60s each)
operation history for the key:  I -> U -> U -> U -> D
latest non-deleted version per key: 0 rows
```

So after an erasure in Odoo, the person **disappears from every mart and every dashboard** within
the freshness SLA. For most purposes that is the visible outcome a data subject cares about.

It is not erasure, for three reasons, all structural:

1. **`raw.*` is append-only.** The tombstone hides the earlier rows from the projection; it does not
   remove them. The digests of the person's name, email and phone remain in the landing zone.
   `warehouse_loader` holds no `DELETE` precisely so that history cannot be rewritten by the
   pipeline — which is the right property for integrity and the wrong one for erasure.
2. **SCD2 dimensions retain history by design.** `dim_partner` and `dim_product` are `dbt snapshot`
   models; superseded versions are what a slowly-changing dimension is *for*.
3. **Backups retain everything.** `make warehouse-backup` and `make tenant-backup` produce dumps
   that predate the erasure.

### 5.3 What "erased" has to mean here

A complete erasure must reach five places. Anything less is partial, and should be described as
partial:

| Place | How it is cleared | Automated? |
|---|---|---|
| Odoo (OLTP) | the operator deletes or anonymises the record | no |
| `marts.*`, `staging.*` | falls out automatically once `raw` is corrected and dbt rebuilds | **yes, once §5.4 step 3 is done** |
| `raw.*` landing zone | privileged `DELETE` as `warehouse_admin` | no |
| `snapshots.*` (SCD2) | privileged `DELETE`, then `dbt snapshot` | no |
| backups | expiry, or a documented restore-and-redact | no |

### 5.4 The manual runbook

Perform in this order. Steps 2–4 require `warehouse_admin`, the only superuser, which is deliberate:
no automated identity in this system can rewrite the landing zone.

```bash
# 0. Record the request: who asked, what identifier, on what date, and who authorised the erasure.
#    UU 27/2022 requires the controller to be able to demonstrate it acted; a log that only shows
#    rows disappearing does not show that.

# 1. In Odoo: delete or anonymise the data subject. This is the step that propagates on its own.
#    Prefer anonymise where the record is referenced by accounting documents that must be retained
#    under separate statutory retention.

# 2. Compute the digests to target. Do this on an operator workstation with the tenant salt;
#    NEVER by adding a lookup to a service, which would put the salt on a network path.
python3 - <<'PY'
import hashlib, hmac, os
salt = os.environ["WAREHOUSE_MASK_SALT_BCT"].encode()
for value in ["budi.santoso@contoh.invalid", "Budi Santoso", "+62-812-0000-0000"]:
    print(value, hmac.new(salt, value.encode(), hashlib.sha256).hexdigest())
PY

# 3. Remove the landing-zone rows for that subject. `id` is the Odoo primary key, which is the
#    reliable key; the digests above are for columns where the id is not known.
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  DELETE FROM raw.res_partner WHERE _tenant_id = 'bct' AND id = <partner_id>;"

# 4. Remove the retained SCD2 versions.
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  DELETE FROM snapshots.dim_partner_snapshot WHERE tenant_id = 'bct' AND partner_id = <partner_id>;"

# 5. Rebuild, so the marts reflect the corrected landing zone.
make dbt-run

# 6. Verify. Do not close the request on the strength of the DELETE having run.
docker exec odoo19-bct-warehouse-db psql -U warehouse_admin -d warehouse -c "
  SELECT count(*) FROM raw.res_partner WHERE _tenant_id='bct' AND id=<partner_id>;
  SELECT count(*) FROM marts.dim_partner WHERE tenant_id='bct' AND partner_key='<digest>';"

# 7. Backups: record the earliest backup that still contains the subject and the date it expires,
#    or restore-redact-redump if the retention window is longer than the statutory response period.
```

**Step 6 is not optional.** An erasure that was performed but not verified is indistinguishable from
one that silently failed, and it is the controller who has to demonstrate which happened.

### 5.5 Access, rectification, portability

- **Access / portability** — served from Odoo, which holds the cleartext. The warehouse cannot
  answer a subject access request: it holds digests, and reversing one requires the salt, which is
  exactly the property §3 relies on. Do not build an SAR path through the warehouse.
- **Rectification** — automated. A correction in Odoo propagates as an `UPDATE` and the mart shows
  the new value within the SLA. Note that the *old* digest remains in the append-only landing zone;
  if the old value was itself personal data that must be erased, this is a §5.4 case, not a §5.5 one.

---

## 6. Retention, transfer, and the security posture that supports all of it

- **Retention.** The warehouse holds no data Odoo does not hold, so Odoo's retention governs. The
  landing zone's append-only history is the one place the warehouse retains *more* than Odoo, and
  §5.3 is where that matters.
- **Cross-border transfer.** Everything runs on one VPS in a single deployment. No processor outside
  it receives personal data. If that changes, UU 27/2022 Pasal 56 applies and this section must be
  rewritten before the change ships, not after.
- **Read-only by construction.** The pipeline connects to Odoo as `warehouse_reader`, holding only
  `SELECT` and `REPLICATION`. There is no write path from analytics into the ERP — not by policy,
  because the role cannot. Verified with pasted denials for `INSERT`, `UPDATE`, `DELETE`,
  `CREATE TABLE` and `CREATE TEMP TABLE` in
  `tests/test_00_environment.py::test_warehouse_reader_is_read_only_by_construction`.
- **Least privilege in the warehouse.** Four roles; only `warehouse_admin` is a superuser and
  nothing queries data as it. `warehouse`, `warehouse_loader` and `warehouse_rls` are all
  `rolsuper=f, rolbypassrls=f`, asserted on every suite run.
- **The serving path cannot reach the landing zone.** `warehouse_rls` has no privilege on schema
  `raw`, so unmodelled, minimally-processed data is unreachable from the API.
- **Transport and tokens.** Sessions are RS256 JWTs; the gateway holds the private key and verifiers
  fetch only the public half from JWKS. `tenant_id` comes from the verified token and never from a
  header, cookie or request body.

---

## 7. Known gaps — stated, not buried

| # | Gap | Consequence | Owner |
|---|---|---|---|
| 1 | **DSAR erasure is manual** (§5.1) | Erasure depends on an operator following §5.4 correctly and completely. There is no evidence trail generated by the system itself. | product decision |
| 2 | No `custom_pdp_audit` module | Consent and DSAR handling inside Odoo is not modelled. The warehouse's audit (§4.3) covers reads of the mart, not lifecycle events. | product decision |
| 3 | Semantic audit cannot be made mandatory | `warehouse.log_access()` is a call a client can omit; `pgaudit` is not available in `postgres:16-alpine`. `log_statement='all'` is the compensating control. | accepted, documented |
| 4 | Backups are outside the erasure path | A restore can reintroduce an erased subject. | §5.4 step 7 is the manual compensation |
| 5 | Landing-zone history outlives a mart delete | An Odoo delete removes the subject from every mart but not from `raw.*`. | by design; §5.4 step 3 is the manual compensation |

**Recommendation, offered rather than assumed.** Gap 1 is the one worth closing with code, and the
shape is already available: an `ir.cron`-driven erasure queue in Odoo writing to a
`warehouse.erasure_request` table, with a privileged worker that performs §5.4 steps 3–6 and records
what it deleted. It is deliberately **not** built here, because it needs an owner, a retention
policy for the erasure log itself, and a decision about accounting records under separate statutory
retention — none of which is QA's to decide. Until it exists, §5.4 is the honest description of how
this obligation is met.

---

## 8. How to re-verify every claim in this document

```bash
bash tests/run.sh -k "masking or isolation or reader_is_read_only or role_model"
```

Each of those prints the identity it ran as and the values it compared, so the evidence can be read
rather than trusted. The masking tests re-derive the digest from the specification by hand rather
than importing the loader's implementation, so they cannot pass by a function agreeing with itself.
