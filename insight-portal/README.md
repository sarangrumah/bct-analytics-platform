# insight-portal

The BCT analytics dashboard. Next.js 15.5.21, App Router, TypeScript strict, Tailwind 4.

Every number on screen came from `POST /v1/query` on the semantic layer. This application writes no
SQL, holds no database credential, and never recomputes a metric. Where the brief asks for a figure
that no declared metric can answer, the panel says so and names what would be required.

---

## Running it

```
npm install
npm run build
npm run start                 # 127.0.0.1:33000
```

`next start` warns that it does not work with `output: standalone`. It serves correctly, but the
container runs `node .next/standalone/server.js`, and so should any run you intend to measure:

```
cp -r .next/static .next/standalone/.next/ && cp -r public .next/standalone/
cd .next/standalone && PORT=33000 HOSTNAME=127.0.0.1 node server.js
```

**Stop the server before `npm run build`.** On Windows the running process holds file handles under
`.next/`, and the build hangs part-way through with no error. That cost twenty minutes once.

In the stack:

```
docker compose -p odoo19-bct -f insight-portal/docker-compose.portal.yml up -d --build
```

Always `-p odoo19-bct`. This host also runs `odoo19-platform-*`, `odoo19-analytics-*` and
`smart-warga-*`; the overlay joins the existing `odoo19-bct_bct` network as external and binds
`127.0.0.1:33000` only.

---

## Routes

| Route | Kind | What it does |
|---|---|---|
| `/` | Server Component | Redirects to `/t/<session tenant>/overview`. The tenant comes from the verified token. |
| `/login` | Server Component | Plain HTML form, no client JavaScript. Posts to the route handler below. |
| `/api/auth/login` | Route Handler | Forwards credentials to `login-gateway`, verifies the returned token, sets an httpOnly cookie. |
| `/api/auth/logout` | Route Handler | Revokes the refresh token upstream, clears both cookies, clears the aggregate cache. |
| `/api/filters` | Route Handler | Writes the date-range and Operating-Unit filter cookie, 303 back. |
| `/api/export` | Route Handler | CSV or XLSX of one panel's query, through the same `query()` the page used. |
| `/healthz` | Route Handler | `{"status":"ok"}`. No token, no data. Container healthcheck. |
| `/t/[tenant]/overview` | Server Component | Revenue, MoM growth, channel and Operating Unit splits. |
| `/t/[tenant]/sales` | Server Component | Sales by month, product, partner, Operating Unit. |
| `/t/[tenant]/inventory` | Server Component | Stock position and valuation by product and Operating Unit. |
| `/t/[tenant]/finance` | Server Component | General ledger balances by account type, P&L / balance-sheet split. |
| `/t/[tenant]/ppob` | Server Component | Volume, success rate, commission, SLA breaches. |
| `/t/[tenant]/drill` | Server Component | Generic drill-down, validated against `GET /v1/metrics`. |

### What `[tenant]` is for

It is compared against the verified session and then discarded. It is never forwarded to the
semantic API and never used to select data. `src/lib/semantic.ts`'s `query()` takes a metric,
dimensions, filters, order and limit — **and no tenant argument at all**, so there is no parameter
through which a URL, header, cookie or form field could change which tenant is queried. The segment
exists so a mis-aimed link fails loudly with 403 instead of quietly showing the viewer their own
data under someone else's name.

---

## Server / client boundary

Server Components by default. **The only client components are the two chart wrappers** and the
legend and theme they share (`src/components/charts/*`). Every page, every panel, the filter bar,
the navigation and the tables render on the server.

- `src/lib/semantic.ts` and `src/lib/session.ts` begin with `import "server-only"`, so importing
  either from a client component is a build error rather than a token in a bundle.
- No variable carries the `NEXT_PUBLIC_` prefix. There is no route by which a server value reaches
  the client bundle.
- The filter bar is a plain `<form method="post">`. It works with JavaScript disabled, before
  hydration, and on a slow phone connection.
- 103 kB shared JS on routes with no chart; 221 kB first load on the five view routes, which is
  Recharts.

`tests/no-database-path.test.ts` asserts the boundary rather than describing it: no client component
imports a server-only module, no source file contains SQL, no dependency is a database driver, no
symbol would unmask anything.

---

## Metrics consumed

All eleven declared metrics, and nothing else:

`revenue_net`, `revenue_mom_growth`, `sales_total`, `sales_untaxed`, `stock_net_quantity`,
`stock_valuation`, `account_balance`, `ppob_transaction_count`, `ppob_commission_revenue`,
`ppob_sla_breach_count`, `ppob_success_rate`.

Three bindings that are easy to get wrong and are deliberate here:

- **PPOB revenue is `ppob_commission_revenue`.** The much larger `pass_through_amount` on the same
  mart is money owed to the biller. On live data, binding it would overstate revenue by 481×.
- **`revenue_net` sums three UNIONed channels** (`invoice`, `pos`, `ppob_commission`) on purpose, as
  the metric's own `channel_note` declares. The channel split is on the overview so the total is
  never read as one line of business.
- **`stock_net_quantity` and `stock_valuation` get no `date_range`.** `mart_stock_position` is a
  position, not a daily series, and declares no such filter; sending one is a 400. The inventory
  view says so on screen rather than leaving a viewer to discover it by changing dates and watching
  nothing happen.
- **`stock_valuation` is a SUBTOTAL and is labelled as one.** Products with no unit cost carry a
  NULL valuation, and SQL `SUM()` skips NULL without comment — 250 units of real stock on tenant
  `bct` sit outside a number that reads as finished. A "Cakupan harga pokok" panel grouped by
  `has_unit_cost` is rendered *before* the product breakdown so the excluded bucket is seen first.
- **`is_profit_and_loss` can be NULL**, meaning neither — section and note lines carry no account —
  and NULL is not `false`. This seed has zero such rows, which is not evidence they cannot occur, so
  the group is still rendered and labelled. `tests/format.test.ts` proves the label, because no live
  query can reach that branch today.

### What is not available, and why

`src/lib/gaps.ts` is the registry; each entry renders a panel naming the metric that would answer
it. Three distinct reasons, which are not interchangeable:

- **`not_in_build`** — PPN/PPh. The operator chose a four-addon set, so there are no
  Coretax/e-Faktur or PPh withholding modules and no tax data exists. The panel states that. It is
  not computed, not estimated and not derived from anything nearby.
- **`no_data`** — year-on-year growth. The warehouse spans 2025-09-01 to 2026-08-31, so no month has
  a prior-year counterpart and every value would be null. Month-over-month is shown instead and is
  labelled month-over-month.
- **`no_metric`** — gross margin, AR ageing, cash position, sales funnel stages, stock ageing and
  stock turnover. Each would be business logic reimplemented in TypeScript, and several are ratios,
  which the brief forbids outright in a component.

Two entries that used to sit here are gone because Backend declared the metrics:

- **Stock valuation** is now `stock_valuation`, at `standard_price`. `list_price` would have
  overstated inventory by the entire margin.
- **The profit-and-loss / balance-sheet split** is a group-by on `account_balance` over
  `account_type` and `is_profit_and_loss`, not two metrics. Two names for the same measure over a
  filtered set is a view wearing a metric's clothes, and Backend declined to declare them for that
  reason.

---

## Freshness

"Last refreshed at" is `meta.last_refreshed_at` from the API response, which the semantic layer
reads from `warehouse.mart_freshness` over `warehouse.pipeline_state`. Staleness is `meta.is_stale`
— the warehouse's verdict, not this application's.

**There is no relative time anywhere in this application.** No "4 minutes ago". A relative rendering
is the viewer's device doing arithmetic on a pipeline fact, and the one thing a dashboard must not
do about freshness is substitute a clock for the pipeline. The timestamp is shown absolute in UTC,
next to the metric's own SLA, because the SLAs are deliberately not uniform: PPOB is 60 seconds and
finance is 60 minutes (ADR 0001), so the same age means different things on different views.

`tests/no-database-path.test.ts` asserts `Freshness.tsx` contains no `Date.now()` or `new Date()`.

---

## Measured performance

**Budget: p95 dashboard load under 2 s with 12 months of data. Met, with roughly an order of
magnitude of headroom in the worst case.**

Method — stated because the number means nothing without it:

- Client is `scripts/measure-p95.mjs` on the same host, over loopback. This deliberately excludes
  network transit: what is measured is the application and the warehouse behind it.
- **Sequential**, one request at a time. That is the single-user dashboard-load model the budget
  describes; a concurrent run measures throughput, a different question.
- Each sample is time to **last byte of the full HTML document**, not to first byte. The shell
  streams early, so a TTFB figure would report when the navigation bar arrived rather than when the
  figures did. TTFB is recorded alongside so the streaming gap is visible rather than hidden.
- 5 warm-up requests **discarded**, then **60 measured samples per view**. The cold figure is
  reported separately rather than folded in.
- p95 is nearest-rank: the value at `ceil(0.95 × 60)`, the 57th slowest sample. No interpolation.
- Data: 12 months, 2025-09-01 to 2026-08-31. `mart_revenue_daily` 777 rows, `mart_sales_daily` 290,
  `mart_ppob_transaction` 345, `mart_stock_position` 27/28, `fct_account_move_line` 862.
- Hardware: Windows 11 (10.0.26200), 16 logical CPUs, 31.3 GiB RAM, Node 24.11.1. The whole stack —
  Odoo, both Postgres instances, the gateway, the semantic API and the portal — on one machine.

Shipped default (`INSIGHT_PORTAL_CACHE_TTL_SECONDS=30`), all times in ms:

| view | n | cold | min | p50 | **p95** | p99 | max | ttfb p95 | kB | failed panels |
|---|---|---|---|---|---|---|---|---|---|---|
| overview | 60 | 132 | 16 | 19 | **27** | 31 | 31 | 11 | 83 | 0 |
| sales | 60 | 73 | 18 | 24 | **39** | 48 | 48 | 14 | 99 | 0 |
| inventory | 60 | 95 | 18 | 21 | **27** | 35 | 35 | 10 | 91 | 0 |
| finance | 60 | 94 | 16 | 20 | **31** | 45 | 45 | 13 | 88 | 0 |
| ppob | 60 | 121 | 18 | 22 | **30** | 35 | 35 | 11 | 96 | 0 |

Worst p95 across the five views: **39 ms**.

Cache disabled (`INSIGHT_PORTAL_CACHE_TTL_SECONDS=0`), every panel queries the warehouse — the
honest worst case:

| view | n | cold | min | p50 | **p95** | p99 | max | ttfb p95 | kB | failed panels |
|---|---|---|---|---|---|---|---|---|---|---|
| overview | 60 | 290 | 104 | 128 | **153** | 162 | 162 | 32 | 83 | 0 |
| sales | 60 | 143 | 90 | 121 | **164** | 190 | 190 | 34 | 99 | 0 |
| inventory | 60 | 129 | 98 | 113 | **142** | 182 | 182 | 30 | 91 | 0 |
| finance | 60 | 114 | 86 | 111 | **150** | 155 | 155 | 28 | 88 | 0 |
| ppob | 60 | 143 | 125 | 144 | **213** | 314 | 314 | 33 | 96 | 0 |

Worst p95 across the five views: **213 ms**.

Both are reported because only one of them is honest on its own. The cached number is what an
operator experiences; the uncached number is what the system can actually do, and it is the one to
budget against.

### Where the time goes

Under 100 ms of the uncached figure is the semantic API and the warehouse — panels report their own
`query_duration_ms` on screen, typically 11–90 ms each. The rest is React rendering the server
components and serialising the payload. Panels run concurrently (`src/lib/panels.ts`), bounded to
four in flight.

### Why four

Found by measurement. With the cache off, the ten-panel PPOB view issued ten queries at once, and
`semantic-api` runs a `ThreadedConnectionPool` with `maxconn=8`. Ten concurrent requests exhausted
it, psycopg2 raised `PoolError: connection pool exhausted`, and **133 upstream 500s appeared across
a 300-request run** — visible on screen as "Panel gagal dimuat", which is at least honest, but the
panels were genuinely empty.

That is **not** the T-1 scope guard: `bct_semantic_pool_guard_trips` stayed at 0 throughout and the
documented `503 scope_guard` never appeared. Two different failures that both involve the word
"pool". `src/lib/limit.ts` caps this process at four concurrent upstream calls, comfortably inside
the pool even with another consumer using it. After the cap, failed panels are **0** in both runs.

### Caching

`src/lib/cache.ts` keys on the **verified session** as well as the request body:
`sub | tenant_id | all_ou | allowed_ou | body`. `/v1/query` bodies carry no tenant — the tenant comes
from the token — so a cache keyed on the body alone would serve one tenant's rows to another, and
every layer below would be innocent. TTL is bounded by the metric's own `refresh_sla_seconds`, so a
cached value can never be older than the freshness the panel advertises, and `meta` is cached with
the rows so a cached panel reports the pipeline timestamp it actually came from.

Next's own data cache is deliberately **not** used (`cache: "no-store"`) for the same reason: it is
keyed on the request, and the request does not identify the tenant.

---

## Charts

**Recharts 3.10.1.** One library, both chart types.

The choice came down to Recharts versus visx versus hand-written SVG, and the deciding factor was
keyboard access rather than looks. Recharts 3 ships `accessibilityLayer`, which makes the plot a
focusable element and steps through data points with the arrow keys, announcing each — that is
acceptance criterion 8 working, rather than something to build and then argue about. visx would have
meant implementing focus management, roving tabindex and announcements by hand for two chart types,
which is more code in exactly the area where hand-rolled code tends to be quietly wrong. Hand-written
SVG in Server Components would have shipped zero client JavaScript and was genuinely tempting at
221 kB first load, but it puts the same accessibility burden back on me, and the brief asks for a
library. Recharts 3 is also React 19 and Server Components compatible, which several alternatives
are not yet. The cost is honest and stated: those 221 kB are Recharts, and they are the only client
JavaScript in the application.

Colours are the validated default categorical order — slot 1 blue `#2a78d6`, slot 2 orange
`#eb6834`, slot 3 aqua `#1baf7a` — checked with a validator rather than by eye:

```
light (surface #fcfcfb): CVD worst all-pairs dE 9.2 (deutan), normal-vision worst 24.0,
                         contrast WARN on aqua at 2.74:1
dark  (surface #1a1a19): CVD worst all-pairs dE 9.4, normal-vision worst 20.9, all >= 3:1
```

The light-mode contrast warning obliges relief, taken as the data table that ships under every
chart. No chart uses more than two series, and a second series is drawn with a dash pattern as well
as a hue, so identity never rests on colour. Two measures of different scale get **two charts, never
two y-axes** — PPOB volume and SLA breaches sit side by side for exactly that reason.

---

## Accessibility

Verified by `scripts/keyboard-audit.mjs`, which sends real Tab keystrokes over the DevTools Protocol
and reads `document.activeElement` after each one. That matters: an element can match `[tabindex]`
and still be unreachable behind `inert`, `display:none` or a focus trap, so querying what *looks*
focusable proves less than walking the focus order the browser actually builds.

All five views pass all fifteen checks. Per view:

- the skip link is the first focus stop;
- all five navigation destinations are reachable;
- both date inputs, the apply button and all three range presets are reachable;
- a chart surface takes focus, and arrow keys step through it and surface values;
- every data table is reachable as a labelled scrollable region;
- CSV and XLSX export links and the logout control are reachable;
- **no focus stop is anonymous to a screen reader** — every one has an accessible name;
- nothing is encoded by colour alone;
- freshness state is stated in words (`Segar` / `Basi`) as well as colour and an icon.

Two defects the audit found, both fixed: the horizontal bar charts answer **ArrowLeft**, not
ArrowRight (asserting only on ArrowRight reported a working chart as broken), and their tooltip
announced the literal data key — `value : 99.851 unit`. `<Bar>` now carries an explicit `name`, so a
screen reader hears `Saldo per akun : -Rp 439.850.000`.

Every chart is a `<figure>` with a `<figcaption>` saying what is plotted, how many points, in what
unit, and that the numbers are in the table below. The caption is deliberately **descriptive, not
interpretive** — a sentence claiming a trend would be an assertion about the data that nothing in
this application is entitled to make.

### 375 px

`evidence/*-375px.png` and `evidence/*-desktop.png`, ten screenshots from `scripts/screenshot.mjs`.
Measured content width equals viewport width on every one: **no page scrolls sideways at either
width.**

What was done about the two things that break first on a phone:

- **Wide tables** scroll inside their own `overflow-x: auto` region with an accessible name and
  `tabindex={0}`, so the row a viewer came for is reachable by keyboard and the page body never
  scrolls horizontally. Columns are not hidden and text is not shrunk to fit.
- **Dense charts** use horizontal bars for categories, because category names are words and 375 px
  forces vertical bars into rotated or truncated labels. Time series thin their tick density rather
  than rotating labels. Chart containers are sized to include the x-axis band, so a card never grows
  a nested scrollbar that hides the axis.
- The KPI row is one column at 375 px, two at `sm`, four at `lg`. The navigation scrolls
  horizontally rather than collapsing behind a hamburger, which would need client JavaScript for a
  list of five.

---

## Export

CSV and XLSX, both written by `src/lib/xlsx.ts` with no dependencies — a hand-built ZIP container and
OOXML. A spreadsheet library pulled in for one function would be a permanent addition to the surface
Security's `sca-node` job scans. `tests/export.test.ts` unzips a generated workbook with Python's
`zipfile` and reads the cells back, rather than checking the writer against its own output.

**Masking.** The export path calls `query()` — the same function the pages call, the only function in
this application that obtains data — and writes out the rows it returns. Those rows were masked
upstream in the warehouse. There is no unmasking path here because there is no second data path at
all: the portal holds no database credential. `tests/live-freshness-and-masking.test.ts` reads the
masked `partner_key` out of the warehouse with psql, asserts the export contains that exact string,
and separately asserts that no plaintext partner name from Odoo appears in the export or on the
rendered page.

CSV cells beginning `=`, `+`, `-`, `@`, tab or CR are prefixed with an apostrophe. The warehouse
masks personal data; it does not defend the spreadsheet the data is opened in.

A null measure exports as an empty cell, never as zero. `revenue_mom_growth` has no prior month for
the first row of a window, and a spreadsheet that says 0 there asserts flat growth nobody measured.

Every export carries a provenance footer: metric, source model, tenant, pipeline timestamp,
staleness and row count. A spreadsheet outlives the screen it came from.

---

## Security

`npm audit --audit-level=high` - the exact command `sca-node` runs - reports **0 vulnerabilities**.

Getting there did not require Next 16. `package.json` carries:

```json
"overrides": { "postcss": "^8.5.18", "sharp": "^0.35.0" }
```

which resolves `next` **15.5.21 unchanged**, `postcss` 8.5.26 and `sharp` 0.35.4. The nested
`postcss` 8.4.31 that used to ship inside `next/node_modules` is flattened away, so it is gone from
the image too. npm's own "will install next@16.3.3, which is a breaking change" is its remediation
heuristic, not a statement that no other remedy exists.

**The runtime stage removes npm, yarn and corepack.** This is the more important half and it is not
a version chase. Those are not application dependencies and are not in the lockfile - they arrive
inside the base image carrying their own vendored trees (`tar`, `pacote`, `sigstore`, `ip-address`,
`brace-expansion`, `picomatch`), which is where a CRITICAL and twelve HIGHs came from. `npm audit`
cannot see any of them, because it reads the declared tree and these are not declared. A Next
standalone runtime needs `node`; a package manager beside the application in production is also an
arbitrary-code-download tool beside the application in production.

Verified in the built image: `npm`, `npx`, `yarn`, `yarnpkg` and `corepack` all absent,
`/usr/local/lib/node_modules` and `/opt` both empty, `node v22.23.2` still works, and the container
reports **healthy** - the healthcheck is `node -e fetch(/healthz)` running inside it.

`sharp` and `@img` are still deleted from the runtime stage, but that is now plain surface reduction
rather than a CVE mitigation: at 0.35.4 the libvips CVEs are fixed at source, the image optimizer is
off in `next.config.ts`, and nothing imports `next/image`.

### Redirects

Route handlers emit a **relative** `Location` via `src/lib/redirect.ts`. Middleware does not use that
helper and must not: the two runtimes differ in a way that is easy to "tidy up" into a bug.

| | `request.nextUrl` comes from | correct form |
|---|---|---|
| middleware | the incoming `Host` header | `NextResponse.redirect(request.nextUrl.clone())` |
| route handler | `request.url`, i.e. the **bind address** | a relative `Location` |

Inside the container `HOSTNAME=0.0.0.0` — correct for binding, meaningless as a destination — so a
route handler that builds its target from `request.url` **or** from `nextUrl` answers
`location: http://0.0.0.0:3000/...`, which a browser resolves verbatim and fails on. Middleware was
never affected. Unifying them the other way (relative everywhere) 500s every request:
middleware parses its own `Location` with `new URL()` and rejects a relative one.

Both wrong turns were taken and measured before the current shape was settled on. `redirect.ts`
records them next to the code so the asymmetry reads as deliberate rather than untidy.

`tests/live-redirects.test.ts` follows every redirect rather than reading its status. **A redirect
status code is not evidence of a working redirect** — the original bug shipped with the whole suite
green, because the suite carried a session cookie and requested views directly, so nothing ever
followed one.

### OS packages

`libcrypto3` and `libssl3` are pinned to an exact version and upgraded in the runtime stage,
following the shape already used in `odoo/Dockerfile` — exact version, upgrade only, and the command
for re-deriving it written above the pin:

```
docker run --rm -u 0 --entrypoint sh <image> \
  -c 'apk update >/dev/null && apk policy libcrypto3 libssl3'
```

Pinned rather than a bare `apk upgrade`, for the same reason a `==` beats a `>=`: a floating upgrade
makes the scan's verdict depend on whatever the mirror serves that day, and a green run nobody can
reproduce tomorrow is not evidence. An unsatisfiable pin fails the build with "unable to select
packages", which is the point — moving it becomes a reviewed change rather than silent drift.

This closes CVE-2026-14456 (openssl, denial of service via unbounded memory). Verified in the
shipped image: `libcrypto3-3.5.8-r0`, `libssl3-3.5.8-r0`.

Applied to the runtime stage only. The deps and build stages never ship, so upgrading them would
cost a layer and prove nothing.

### The lockfile must be generated on Linux

`npm install` on Windows prunes platform-specific optional dependencies and writes a lockfile with
only the win32 variants of `sharp`. The host install succeeds and `npm audit` is clean - and then
`npm ci` inside Alpine fails with `Missing: @emnapi/runtime from lock file`, so **a fresh clone
cannot build the image**. The working tree looks healthy throughout, which is the same shape as the
`.gitignore` exclusion earlier in this build.

The lockfile here is generated inside the pinned base image and carries all 31 platform variants:

```
docker run --rm -v "$PWD:/app" -w /app \
  node:22-alpine@sha256:c610fcdfb1d5b4740dd70c284ed3cb16bb857e0f7166196e36a5501df7a3aa32 \
  npm install --package-lock-only
```

Verified both ways afterwards: `npm ci` on the Windows host, and the image build in Alpine.

### Three gates, three questions

`npm audit` and `trivy-fs` read the **declared** tree; `container-scan` reads **what ships**. A
mitigation that deletes a package from the runtime stage is invisible to the first two, and a
package manager smuggled in by the base image is invisible to them as well - the container scan is
the only gate that found the CRITICAL. Keeping all three is the point, not a duplication to
reconcile.

---

## Testing

```
npm run test                              # unit only; live suite announced as NOT RUN
PORTAL_E2E=1 BCT_DEV_PASSWORD=... npm run test
npm run test -- --grep "cross-tenant|403"
node scripts/measure-p95.mjs
node scripts/keyboard-audit.mjs
node scripts/screenshot.mjs evidence
node scripts/freshness-freeze-proof.mjs   # stops and restarts odoo19-bct-cdc
```

107 tests. The runner prints, loudly, whether the live suite ran — a green run that never reached the
database is the defect pattern this build keeps producing, and the runner refuses to let that look
like a full pass.

Each guard has a recorded way it was made to fail. Those live in the commit messages and in the file
headers, next to the assertion they justify, because a check nobody has seen fail is not yet known
to work.
