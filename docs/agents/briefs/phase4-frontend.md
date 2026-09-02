# Brief: Frontend — Phase 4 (insight-portal analytics dashboard)

## Objective
A Next.js analytics dashboard where every number on screen came from the semantic layer, every query
was scoped server-side by a tenant the user could not choose, and every panel tells the viewer how
fresh it is. Five view groups, readable on a phone, p95 under 2 s with 12 months of data — measured,
not estimated.

## Read first — in this order
1. `docs/agents/contracts/06-api.md` — Backend's `POST /v1/query` request/response schema and the
   `meta` block. **This is the only data shape you build against.**
2. `docs/agents/contracts/02-session.md` — the JWT claim set, JWKS verification, and the verbatim
   403 body.
3. `docs/agents/contracts/03-metric.md` — the metric catalogue. Every figure you render maps to a
   declared metric.
4. `docs/agents/contracts/04-platform.md` — env variable names, reserved ports, compose conventions.
5. `docs/adr/0001-analytics-warehouse.md` — the per-mart freshness table, which drives what
   "last refreshed at" and the stale indicator must communicate per view.

## Ground truth
`login-gateway` runs on `127.0.0.1:38120`, `semantic-api` on `127.0.0.1:38200`. **Your reserved port
is `127.0.0.1:33000` — do not take another.** This host runs other live stacks (`odoo19-platform-*`,
`odoo19-analytics-*`, `smart-warga-postgres-1`) that must not be disturbed; always scope compose
commands `-p odoo19-bct` and never run `docker system prune`, `volume prune`, or an unscoped `down`.

Node 22 is pinned by digest:
`node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32`.

## Scope — in

### Stack
Next.js **15.5.21**, App Router, **TypeScript strict**, Tailwind. Server Components by default.
Multi-stage Dockerfile on the pinned digest, non-root, `no-new-privileges`, `cap_drop: [ALL]`.

### Data access — the hard rules
- **Server-side only, through the semantic API.** The browser never gets a database connection
  string, never queries Odoo directly (anti-pattern §7.3), and never receives more rows than it
  renders.
- **You never write SQL, and you never reimplement a metric in TypeScript.** If a number you need is
  not in the metric contract, that is a request to Backend via the Lead — not a local calculation.
  A ratio computed in a React component is a brief violation.
- `tenant_id` comes from the verified session only. There must be no code path where a URL parameter,
  header or form field can change which tenant is queried.

### Auth
Reuse `login-gateway`. **Do not build a second auth system.** Verify the JWT server-side against
JWKS with the algorithm pinned to RS256. A user of tenant A requesting tenant B's dashboard gets
**403** — verified by a test, per §6.

### Views — all five, minimum
1. **Executive overview** — revenue, margin, AR ageing, cash position.
2. **Sales** — funnel, per-OU, per-product, YoY.
3. **Inventory** — stock value, ageing, turnover, slow movers.
4. **Finance** — P&L and balance sheet from `fct_account_move_line`.
   *Scope honesty:* the operator chose the 4-addon set, so there are **no Coretax/e-Faktur or PPh
   modules**. Render the PPN/PPh summary **only** if the metric contract actually exposes those
   figures. If it does not, show an explicit "not available in this build" state — **do not fabricate
   a tax summary and do not compute one client-side.**
5. **PPOB operations** — volume, biller success rate, commission, SLA breaches.

### Interaction
- Date-range and OU/tenant filters that **persist across views**.
- Drill-down from summary to line level.
- CSV/XLSX export that goes through **the same masking rules as the warehouse** — since data arrives
  already masked, export must never call an unmasking path, and there must not be one.
- **"Last refreshed at" sourced from `meta.last_refreshed_at`** in the API response — real pipeline
  metadata, **never a client clock**. Show the stale state when `meta.is_stale` is true, and reflect
  that different views have different SLAs (PPOB 60 s vs finance 60 min).

### Performance
- Budget: **p95 dashboard load < 2 s with 12 months of data.** Server Components by default,
  streaming for slow panels, cached aggregates.
- **Report measured numbers, not estimates.** State the method, the sample size and the hardware.
  If you miss the budget, say so with the number — do not round it away.

### Charts
- **Pick one library and stay with it.** Justify the choice in one paragraph.
- Accessible: keyboard-navigable, **not colour-only encoding**, sufficient contrast, real text
  alternatives for each chart's takeaway.
- **Readable on a phone — the operator will open this on mobile.** Test at 375 px width and say what
  you did about wide tables and dense charts.

## Scope — out
- `analytics/**` (dbt, warehouse, CDC, semantic-api) — DWH and Backend agents.
- `login-gateway/**` — **Backend agent.** You consume it; you do not modify it.
- `addons/**`, `docker-compose.yml`, `Makefile`, `scripts/**`, `odoo/**`, `postgres/**` —
  Platform agents. You own `insight-portal/**` and may add your service to a compose overlay only by
  agreement through the Lead.
- `.github/workflows/**` — **Security owns `ci.yml`.** To get `insight-portal` into `sca-node` and
  `container-scan`, send the Lead a diff request for Security to merge (§2.1). Do not edit it.
- `docs/**` outside your own `insight-portal/README.md`.

## Contracts consumed
`06-api.md`, `02-session.md`, `03-metric.md`, `04-platform.md`.

## Contracts produced
- `insight-portal/README.md` documenting routes, the server/client component boundary, the measured
  performance figures with method, and the accessibility decisions.
- The list of metrics you consume, so the Lead can confirm each is declared in contract 03.

## Constraints
- **You may start on layout and components against `metrics/fixtures/*.json` generated by Backend's
  `make metric-fixtures` — never against an invented data shape** (§2.4). A hand-written fixture
  whose shape does not match contract 06 is a brief violation.
- No secret in the client bundle. Anything sensitive stays in server components or route handlers.
  `changeme` in `.env.example`; the file itself is Platform-Infra's — send a diff request.
- TypeScript strict; no `any` at the API boundary. Generate or hand-write types from contract 06.

## Acceptance criteria — testable statements only
1. `npm run build` succeeds with TypeScript strict and zero type errors.
2. All five view groups render from **real warehouse data**, not fixtures, in the running stack.
3. **Cross-tenant returns 403**, proven by an automated test (§6 requires this).
4. No network request from the browser reaches the warehouse, Odoo, or the semantic API directly —
   demonstrate by listing the browser's actual requests.
5. "Last refreshed at" matches `warehouse.pipeline_state`, and changes after a new load. Show it is
   not a client clock by freezing the pipeline and confirming the timestamp stops advancing.
6. **Measured p95 load time** for each view with 12 months of seeded data, with the method stated.
7. Renders usably at 375 px width — attach evidence.
8. Keyboard navigation reaches every interactive chart control; no information is conveyed by colour
   alone.
9. Filters persist across view navigation.
10. Export produces a file whose personal fields are masked exactly as stored.

## Evidence required — paste the output of exactly these
```
cd insight-portal && npm run build 2>&1 | tail -25
npm run test 2>&1 | tail -30
curl -s -o /dev/null -w 'portal=%{http_code}\n' http://127.0.0.1:33000/
# cross-tenant test result, named explicitly
npm run test -- --grep "cross-tenant|403" 2>&1 | tail -20
# p95 measurement, whatever tool you chose, with the raw numbers and the method
```
Attach a screenshot or rendered evidence at 375 px and at desktop width for each of the five views.

## Escalation triggers — stop and return to Lead
- A number you need is not in the metric contract. **Ask; do not compute it client-side.**
- The p95 budget cannot be met — report the measured figure and where the time goes, rather than
  quietly missing it.
- The Finance view's PPN/PPh summary has no data source in this build. Say so and render an explicit
  unavailable state; **do not fabricate it.**
- Anything would require the browser to hold a credential that reaches a database.
- You need to edit `login-gateway/**`, `analytics/**`, or `ci.yml`.
