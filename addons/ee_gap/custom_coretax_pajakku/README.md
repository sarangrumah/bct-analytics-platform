# Custom Coretax — Pajakku Adapter

Host-to-host adapter for **Pajakku (mitrapajakku)** as a concrete
implementation of `custom.coretax.adapter.base`. When a tenant enables
this adapter, every e-Faktur / Bupot submission produced by
`custom_coretax` flows through Pajakku's API instead of requiring
manual upload via the DJP Coretax portal.

## Architecture

```
┌───────────────────────┐
│ custom_coretax wizard │
│  (XML generator)      │
└────────┬──────────────┘
         │ submit_xml(bytes, config=..., type=...)
         ▼
┌─────────────────────────┐
│ adapter dispatcher       │  resolves by config.adapter_type
└────────┬─────────────────┘
         │
         ▼ (config.adapter_type == "pajakku")
┌─────────────────────────┐
│ custom.coretax.adapter  │
│      .pajakku           │
│                         │
│  + OAuth2 client_creds  │
│  + retry (1s/2s/4s)     │
│  + 429 Retry-After      │
│  + circuit breaker      │
│  + token cache          │
└────────┬────────────────┘
         │ HTTP + Bearer token
         ▼
┌─────────────────────────┐
│  Pajakku API (sandbox   │
│  or production)         │
└─────────────────────────┘
```

Every call materialises a `custom.coretax.transaction` row capturing
payload, response, NSFP, retry count, and state — visible under
**Coretax → Pajakku → Transactions**.

## Setup per tenant

1. **Open Coretax Config** (Coretax → Configuration → Coretax Config).
2. Switch **Adapter Type** to `Pajakku (host-to-host ASPP)`.
3. Open the **Pajakku ASPP** tab:
   - Tick **Pajakku enabled**.
   - Keep **Sandbox mode** on for the first run.
   - Fill in **Client ID** from your Pajakku dashboard.
   - Click **Set / Rotate Secret...** to capture the client secret.
     It is encrypted at rest via `custom.ir.config` (Fernet wrap with
     the master KMS key) and never persisted in plaintext on the config.
4. Click **Test Connection** to perform an OAuth2 handshake. A
   green notification confirms reachability.
5. Submit a draft faktur from the Coretax export wizard — the adapter
   takes over from there.

## Retry + Circuit Breaker Tuning

Module-level constants in `models/coretax_adapter_pajakku.py`:

| Constant | Default | Purpose |
|----------|---------|---------|
| `_MAX_RETRIES` | 3 | Per-call retry attempts |
| `_BACKOFF_BASE` | 1.0 | Doubles each attempt (1s, 2s, 4s) |
| `_CB_THRESHOLD` | 10 | Consecutive failures before breaker opens |
| `_CB_OPEN_SECONDS` | 3600 | Breaker stays open for 1h |

HTTP 429 honors `Retry-After` header (capped at 30s). HTTP 5xx + transport
errors trigger backoff retry. HTTP 401 forces an immediate token refresh
and one retry.

When the breaker trips, the adapter posts to the company's mail thread
("Pajakku circuit breaker OPENED after N consecutive failures") and
refuses subsequent submits for the duration. State auto-resets after
the window expires — no manual intervention required.

## Sync Cron

`cron_pajakku_poll_pending` runs every 30 minutes:

- For each `submitted` transaction, calls `query_nsfp`. On approval,
  stamps NSFP back on the source `account.move` and marks
  `coretax_status = approved`. On rejection, surfaces the DJP message
  on the chatter.
- Retries `queued` transactions whose `retry_count < _MAX_RETRIES`,
  subject to the circuit breaker.

## Usage Metering

`custom.coretax.pajakku.usage` aggregates per-company per-month counters:

- `api_calls` — total HTTP requests (incl. OAuth + polling)
- `faktur_submits` — e-Faktur submissions
- `bupot_submits` — Bupot submissions
- `errors` — failed calls

Visible under **Coretax → Pajakku → Usage / Billing** (admin only).

## Audit

Every state transition on `custom.coretax.transaction` writes to
`pdp.audit_log` via `pdp.audited.mixin`:

- `coretax_pajakku_submitted`
- `coretax_pajakku_approved`
- `coretax_pajakku_rejected`
- `coretax_pajakku_error`

## Security Groups

- `custom_coretax_pajakku.group_pajakku_user` — read transactions,
  retry failed ones.
- `custom_coretax_pajakku.group_pajakku_admin` — configure adapter,
  rotate secret, test connection, view usage. Inherits user.

## Dependencies

- `custom_core` (for `custom.ir.config` Fernet helpers + `custom.security`)
- `custom_pdp_taxonomy`, `custom_pdp_audit` (audit chain)
- `custom_coretax` (base adapter, config, bukti potong model)
- Python: `requests`

## Install

```bash
make install MODULE=custom_coretax_pajakku DB=<tenant_db>
```

## Scope Note

Per the Phase 2 locked decision, this module ships **without a bundled
mock server**. Until live Pajakku sandbox credentials are configured,
attempts to submit raise `UserError`. The adapter code itself is
production-grade and will work unchanged against the real Pajakku API
once credentials are entered + Test Connection succeeds.

## Roadmap

- Webhook ingestion endpoint (Pajakku push) — currently polled only.
- OnlinePajak adapter (separate module sharing the same base contract).
- Per-tenant SLA dashboard powered by `custom.coretax.pajakku.usage`.
