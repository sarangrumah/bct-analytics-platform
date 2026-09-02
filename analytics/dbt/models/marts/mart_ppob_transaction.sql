-- GRAIN: (tenant_id, date_day, operating_unit_id, biller_key, state)
--
-- The 60-second mart. ADR 0001 gives it the tightest SLA in the project and a
-- PAGE on breach, because SLA breaches are the whole point of the view and a
-- stale PPOB dashboard hides exactly what it exists to show.

select
    tenant_id,
    date_day,
    operating_unit_id,
    operating_unit_key,
    biller_key,
    biller_code,
    biller_category,
    company_key,
    date_key,
    state,
    count(*) as transaction_count,
    count(*) filter (where sla_breached) as sla_breach_count,
    sum(pass_through_amount) as pass_through_amount,
    sum(admin_fee) as admin_fee,
    -- The ONLY revenue figure on a PPOB row. See fct_ppob_transaction.
    sum(commission_revenue) as commission_revenue,
    sum(customer_paid_amount) as customer_paid_amount,
    avg(nullif(sla_seconds, 0))::numeric(12, 2) as avg_sla_seconds,
    max(sla_seconds) as max_sla_seconds,
    percentile_cont(0.95) within group (order by sla_seconds)::numeric(12, 2) as p95_sla_seconds
from {{ ref('fct_ppob_transaction') }}
group by 1, 2, 3, 4, 5, 6, 7, 8, 9, 10
