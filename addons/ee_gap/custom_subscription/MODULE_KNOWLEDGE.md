---
status: draft
generated_at: 2026-05-21T00:00:00Z
generator: claude-code-bootstrap-v1
module: custom_subscription
manifest_version: 19.0.0.1.0
---

# custom_subscription

## Purpose
Subscription contract lifecycle + recurring billing + MRR/LTV analytics + AI-assisted churn prediction. Closes the CE-side gap against EE `sale_subscription` for SaaS, retainer, and membership use cases.

A `subscription.plan` declares the SKU + billing cadence + price + optional trial; a `subscription.contract` ties a partner to a plan and runs a state machine (`draft` → `active` ↔ `paused` → `churned`/`closed`). A daily cron generates an `account.move` (`out_invoice`) per active contract whose `next_billing_date <= today`. MRR and LTV are stored computed metrics. A churn-prediction button calls `custom.ai._recommend` and stamps the resulting summary + priority on the contract.

## Business Flow
- Set up `subscription.plan` records: `recurring_interval` ∈ daily/weekly/monthly/yearly, `recurring_count` (every N intervals), `price`, `currency_id`, `product_id` (billing SKU, `sale_ok=True`), `trial_days`. `code` uniqueness enforced.
- Create a `subscription.contract` (`draft`) linking `partner_id` + `plan_id` + `start_date`; name from sequence `subscription.contract` (fallback `SUB/0001`).
- `action_activate()`: state `draft|paused` → `active`. If `plan.trial_days` and state was `draft`, sets `next_billing_date = start + trial_days`; otherwise advances by `_advance(start, interval, count)` (`timedelta(days)` / `timedelta(weeks)` / `relativedelta(months/years)`).
- `cron_generate_invoices` (`@api.model`): daily search `state='active' AND next_billing_date<=today` → per contract `action_invoice_now()`. Each run creates an `account.move` (`out_invoice`) with one `invoice_line_id` (the plan's `product_id`, qty=1, price_unit=`plan.price`), back-linked via `x_custom_subscription_id`; auto-posts (logs warning on failure); advances `next_billing_date` by `_advance(base, interval, count)`. Posts chatter message.
- Workflow buttons: `action_pause()` → paused; `action_churn()` → churned (lost); `action_close()` → closed (graceful termination).
- AI churn: `action_churn_predict()` builds `_custom_ai_payload()` (contract ref, partner, plan, MRR, LTV, state, last 6 invoices), calls `env['custom.ai']._recommend(model='subscription.contract', res_id=self.id, payload=...)`, parses `summary`/`response`/raw JSON and `priority` ∈ info/warn/critical; writes `ai_churn_summary`, `ai_churn_priority`, posts mt_note. Errors surface as `display_notification` (warning), never block.
- Metrics: `_compute_metrics` derives `mrr` from `plan.price` / `recurring_count` × normalisation factor (daily×30, weekly×30/7, monthly×1, yearly÷12), `lifetime_value = sum(paid/in_payment invoices.amount_total)`, `invoice_count = len(invoice_ids)`. `_compute_last_invoice` picks most recent by `invoice_date`.

## Key Models
- `subscription.plan` — plan SKU + cadence + price. Sequence-style `code` (unique).
- `subscription.contract` — partner × plan instance. Inherits `mail.thread` + `mail.activity.mixin`.
- `account.move` (inherited) — adds back-ref `x_custom_subscription_id` (indexed M2o).

## Important Fields
- `subscription.plan.recurring_interval` (Selection daily/weekly/monthly/yearly) — drives `_advance()` and MRR normalisation.
- `subscription.plan.recurring_count` (Integer, default 1, ≥1) — "every N intervals"; divisor in MRR formula.
- `subscription.plan.price` (Monetary, required) — flat price per billing event.
- `subscription.plan.product_id` (M2o `product.product`, required, `sale_ok=True`) — invoice line product.
- `subscription.plan.trial_days` (Integer, default 0) — only applied once at first activation.
- `subscription.contract.state` (Selection draft/active/paused/churned/closed) — `active` is the only state that bills; `churned` and `closed` are terminal.
- `subscription.contract.next_billing_date` (Date, tracking) — cron query key; advanced after each invoice.
- `subscription.contract.mrr` (Monetary, computed, stored) — only non-zero when `state=='active'`.
- `subscription.contract.lifetime_value` (Monetary, computed, stored) — sum of `amount_total` for invoices in `payment_state in ('paid','in_payment')`.
- `subscription.contract.invoice_count` (Integer, computed, stored).
- `subscription.contract.ai_churn_summary` (Text) / `ai_churn_priority` (Selection info/warn/critical) — populated by `action_churn_predict()`.
- `subscription.contract.payment_term_id` (M2o `account.payment.term`) — copied onto generated invoices.
- `account.move.x_custom_subscription_id` (M2o, indexed) — back-ref for `invoice_ids` O2m.

## Public Methods
- `subscription.contract.action_activate()` / `action_pause()` / `action_churn()` / `action_close()`.
- `subscription.contract.action_invoice_now()` — manual billing trigger; also advances `next_billing_date`.
- `subscription.contract.cron_generate_invoices()` (`@api.model`) — daily cron entry.
- `subscription.contract.action_churn_predict()` — AI bridge call.
- `subscription.contract._custom_ai_payload()` — payload builder for `custom.ai._recommend`.
- Module helper: `_advance(date_from, interval, count)` (top-level function).

## Integration Points
- **Depends on:** `custom_core`, `custom_ai_bridge`, `sale_management`, `account`.
- **Inherits from:** `mail.thread` + `mail.activity.mixin` on contract; `account.move` extended with `x_custom_subscription_id`.
- **Extended by:** `custom_payment_id` declares `custom_subscription` as a dependency so payment provider config can be tied to subscription flows.
- **External calls:** `custom_ai_bridge` AI gateway via `custom.ai._recommend` (for churn prediction). No direct HTTP.
- **Cross-vertical:** generic.

## Gotchas
- **No proration**. Mid-period plan changes or upgrades are not supported; manual cancel + new contract.
- **`action_invoice_now()` always advances `next_billing_date`**, even when posting fails (the exception is caught and logged at `warning`). Re-running creates a duplicate invoice — operators must reconcile manually.
- **MRR uses calendar-naive math** — daily×30, weekly×30/7, yearly÷12, monthly is `price/recurring_count`. Approximate.
- **`lifetime_value` only counts `paid`/`in_payment`** invoices — drafts and posted-unpaid are excluded; LTV jumps when invoices reach `in_payment`.
- **`x_custom_subscription_id`** is the ONLY linkage; if an invoice is created outside `action_invoice_now()`, the field must be set manually for metrics to update.
- **Trial only applies once** — pausing and re-activating skips the trial branch (`state=='draft'` check).
- **`_advance()` returns `date_from` unchanged for unknown intervals** — silent no-op if `plan.recurring_interval` is corrupted.
- **AI fallback parses `summary`/`response`/raw JSON** in that order; if the gateway returns shape changes, the fallback `json.dumps(result)[:1000]` may store a giant blob in `ai_churn_summary`.
- **No payment provider integration** — invoices are posted but not collected automatically. Pair with `custom_payment_id` for end-to-end SaaS billing.

## Out of Scope
- **Usage-based / metered billing** — only flat-rate per period.
- **Dunning / failed-payment retry** — `custom_accounting_full.custom.followup.level` covers generic AR follow-up, not subscription-specific dunning.
- **Plan migrations / upgrades / downgrades** — no migration helper.
- **Coupons / discounts** — not modelled.
- **Multi-currency contract pricing** — currency is taken from plan; per-customer pricing requires separate plans.
- **Annual contracts with monthly billing** — out of scope; one contract = one cadence.
