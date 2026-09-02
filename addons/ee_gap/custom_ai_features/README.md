# Custom AI Features

End-user surface for the platform's `ai-gateway`. Three demoable
capabilities:

| Capability | Where | What it does |
|------------|-------|--------------|
| **Ask AI** | Cog menu of 9 key models (account.move, purchase.order, sale.order, res.partner, helpdesk.ticket, approval.request, hr.payslip, fsm.work.order, custom.coretax.transaction) | Sends the record snapshot to `/v1/workflow/recommend` and renders the structured suggestion (priority, tags, next actions) in a wizard. |
| **Anomaly Inbox** | Top menu **AI → Anomaly Inbox**. Nightly `_cron_run` (02:30 UTC) scans `account.move`, `hr.payslip`, `custom.coretax.transaction` via `/v1/workflow/anomaly`. | Surfaces high-confidence outliers with severity + rationale + suggested action. Triage / dismiss / resolve workflow. |
| **NLQ Chat** | `/ai/chat` (linked from top menu). | Lets a user ask plain-language questions ("vendor bills > Rp 100 juta posted bulan ini"); the AI returns a query plan that's executed read-only with PII masking respected. |
| **Document Auto-Classify** | Override on `document.document.create`. | New documents are sent to `/v1/workflow/classify-document` and tagged with a `pdp.classification` + 2-5 topic tags. Operator can override. |

## ai-gateway endpoints added

- `POST /v1/workflow/anomaly` — `{model, res_id, metric, latest_value, history, context, locale}` → `{is_anomaly, severity, score, rationale, suggested_action}`.
- `POST /v1/workflow/classify-document` — `{filename, mimetype, text_excerpt, locale}` → `{classification_code, confidence, tags, rationale}`.
- `POST /v1/workflow/nlq` — `{question, schema_hint, locale, user_can_view_pii}` → `{model, domain, fields, order, limit, rationale, follow_up}`.

Each system prompt is cached on Anthropic side (`cache_control:
ephemeral`) so the same prompt across calls hits the prompt cache —
materially cheaper for the always-on anomaly cron.

## Security

- `custom_ai_features.group_ai_user` — use Ask AI, NLQ chat, view own
  anomaly findings.
- `custom_ai_features.group_ai_admin` — triage findings, configure
  scanners. Inherits user.
- NLQ session record rule: users only see their own sessions.
- NLQ execution is read-only by construction (`search_read` only,
  whitelist of allowed models + fields, PII fields stripped when
  user lacks `pdp.group_view_pii`).

## Anomaly Scanner Configuration

The scanner registry lives in `models/ai_anomaly_scan.py` (`SCANNERS`
list). Adding a new model needs one entry:

```python
{
    "model": "your.model",
    "metric": "amount_field",
    "filter_domain": [...],
    "history_days": 90,
    "min_history": 5,
}
```

The cron `cron_anomaly_nightly` fires at 02:30 UTC daily; admins can
also trigger manually from **AI → Configuration → Scan History**.

## Audit

Every state change on findings + every NLQ answer writes to
`pdp.audit_log` via the chained mixin. Findings surfaced from
sensitive models (payslips) inherit the source's classification when
auditing.

## Grafana

`observability/grafana/dashboards/ai-features.json` ships pre-built
panels for: anomaly counts last 24h, NLQ activity 7d, ai-gateway
req/min + p95 latency by endpoint, Anthropic prompt-cache hit ratio,
open critical findings table.

## Dependencies

- `custom_ai_bridge` (extends `custom.ai`)
- `custom_pdp_taxonomy` + `custom_pdp_audit` (audit + masking)
- `mail`, `portal`, `website` (UI)

## Roadmap

- Sidebar chat widget (vs. portal page) using OWL component.
- Per-record "Why was this flagged?" link from any model record to its
  associated `ai.anomaly.finding`.
- Custom anomaly model registration via a public API on
  `ai.anomaly.scan` (no code edit required to add a new scanner).
