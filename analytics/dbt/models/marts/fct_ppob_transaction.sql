-- GRAIN: one PPOB transaction. (tenant_id, ppob_transaction_id) is unique.
--
-- REVENUE SEMANTICS, and this is the single easiest thing to get wrong in this
-- warehouse: `amount` is pass-through - collected from the customer and owed to
-- the biller - and is NOT revenue. The revenue of a PPOB row is `commission`.
-- A typical row is 100 000 IDR of amount against 1 500 IDR of commission, so a
-- mart that sums amount and calls it revenue overstates it by roughly 40x
-- (custom_ppob/MODULE_KNOWLEDGE.md §2). The column is named
-- `commission_revenue` here so the mistake has to be deliberate.
--
-- sla_target_seconds is SNAPSHOT ONTO THE FACT. The module computes
-- sla_breached against the biller's CURRENT target, so raising a target
-- retroactively un-breaches history; the module says so and says the warehouse
-- must snapshot the target if it wants history to stay true. It does.

select
    {{ surrogate_key(['t.tenant_id', 't.ppob_transaction_id']) }} as ppob_transaction_key,
    t.tenant_id,
    t.ppob_transaction_id,
    t.transaction_name,

    t.requested_at::date as date_day,
    t.requested_at,
    t.settled_at,

    {{ surrogate_key(['t.tenant_id', 't.partner_id']) }} as partner_key,
    {{ surrogate_key(['t.tenant_id', 't.product_id']) }} as product_key,
    {{ surrogate_key(['t.tenant_id', 't.biller_id']) }} as biller_key,
    {{ surrogate_key(['t.tenant_id', 't.company_id']) }} as company_key,
    {{ surrogate_key(['t.tenant_id', 'coalesce(t.operating_unit_id, -1)']) }} as operating_unit_key,
    {{ surrogate_key(['t.tenant_id', 't.requested_at::date']) }} as date_key,

    t.partner_id,
    t.product_id,
    t.biller_id,
    b.biller_code,
    b.biller_category,
    t.company_id,
    coalesce(t.operating_unit_id, -1) as operating_unit_id,
    t.currency_id,

    t.state,
    t.customer_ref,      -- `sensitive`: already an HMAC digest, still supports repeat-customer counts
    t.customer_name,     -- `personal`:  already an HMAC digest
    t.biller_reference,

    -- COALESCED TO ZERO, and this is a correction made against real data
    -- rather than a defensive habit. Odoo's Monetary columns are plain
    -- `numeric` with no NOT NULL: 9 250 of the 9 610 ppob_transaction rows in
    -- the seeded database carry NULL admin_fee and NULL commission. A NULL
    -- there means "no fee was charged", which is zero revenue - so summing it
    -- as NULL would make a day's commission vanish rather than read as 0, and
    -- `sum()` skipping NULLs hides it completely.
    --
    -- The not_null test on commission_revenue stays. It is now testing
    -- something real: that this coalesce is present and that no other NULL
    -- path exists.
    coalesce(t.amount, 0) as pass_through_amount,
    coalesce(t.admin_fee, 0) as admin_fee,
    coalesce(t.commission, 0) as commission_revenue,
    coalesce(t.total_amount, 0) as customer_paid_amount,
    (coalesce(t.admin_fee, 0) - coalesce(t.commission, 0)) as channel_share,

    t.sla_seconds,
    t.sla_breached,
    b.sla_target_seconds as sla_target_seconds_at_build,
    (t.state = 'success') as is_success,
    (t.state = 'failed') as is_failed,
    (t.state = 'reversed') as is_reversed
from {{ ref('stg_ppob_transaction') }} as t
join {{ ref('stg_ppob_biller') }} as b
    on
        t.tenant_id = b.tenant_id
        and t.biller_id = b.biller_id
