-- PPOB billers. sla_target_seconds is the denominator of every PPOB SLA metric.
--
-- NOTE the snapshot warning in custom_ppob/MODULE_KNOWLEDGE.md §4: sla_breached
-- on a transaction depends on the biller's CURRENT target, so raising a target
-- retroactively un-breaches history. fct_ppob_transaction therefore snapshots
-- sla_target_seconds onto the fact row at build time, and this dimension is the
-- current value. The two will differ after a target change, and that is correct.

select
    {{ surrogate_key(['tenant_id', 'biller_id']) }} as biller_key,
    tenant_id,
    biller_id,
    biller_code,
    biller_name,
    biller_category,
    sla_target_seconds,
    company_id,
    active,
    create_date,
    write_date
from {{ ref('stg_ppob_biller') }}
