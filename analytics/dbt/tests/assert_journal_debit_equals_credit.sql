{{ config(severity='error') }}

-- Double entry, per day per tenant, asserted on the mart itself.
--
-- recon_daily already carries a `journal_balance` check comparing the warehouse
-- against Odoo. This is deliberately a SECOND, independent assertion made
-- directly against fct_account_move_line, because the two fail in different
-- situations: recon_daily catches "the warehouse disagrees with the source",
-- this catches "the fact table does not balance at all" - which a transformation
-- bug can produce even when every source total was replicated correctly.

select
    f.tenant_id,
    f.date_day,
    sum(f.debit) as total_debit,
    sum(f.credit) as total_credit,
    sum(f.debit) - sum(f.credit) as imbalance
from {{ ref('fct_account_move_line') }} as f
group by 1, 2
having sum(f.debit) <> sum(f.credit)
