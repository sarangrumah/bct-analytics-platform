-- GRAIN: one journal item. (tenant_id, account_move_line_id) is unique.
--
-- This is the table the debit == credit reconciliation is asserted on, per day
-- per tenant, and that test is severity: error.
--
-- The Operating Unit comes from the MOVE, not the line: account.move.line has
-- no operating_unit_id and never had one. A move with no unit lands in the
-- explicit UNASSIGNED member rather than being dropped by the join.

select
    {{ surrogate_key(['l.tenant_id', 'l.account_move_line_id']) }} as account_move_line_key,
    l.tenant_id,
    l.account_move_line_id,
    l.account_move_id,
    m.move_name,
    m.move_type,
    m.payment_state,
    l.move_date as date_day,
    m.invoice_date,

    {{ surrogate_key(['l.tenant_id', 'coalesce(l.partner_id, m.partner_id)']) }} as partner_key,
    {{ surrogate_key(['l.tenant_id', 'l.product_id']) }} as product_key,
    {{ surrogate_key(['l.tenant_id', 'l.company_id']) }} as company_key,
    {{ surrogate_key(['l.tenant_id', 'coalesce(m.operating_unit_id, -1)']) }} as operating_unit_key,
    {{ surrogate_key(['l.tenant_id', 'l.move_date']) }} as date_key,

    coalesce(l.partner_id, m.partner_id) as partner_id,
    l.product_id,
    l.company_id,
    coalesce(m.operating_unit_id, -1) as operating_unit_id,
    l.account_id,
    -- THE COLUMN THAT SPLITS P&L FROM BALANCE SHEET BY FILTER. Without it the
    -- only way to offer both is two metrics differing solely in name and
    -- returning identical numbers under two headings - a "Balance Sheet"
    -- panel that is actually invoice revenue lines.
    a.account_type,
    -- NULL, not false, when the line has no account: a section or note line
    -- is neither P&L nor balance sheet, and false would put it in the balance
    -- sheet bucket by omission.
    case
        when a.account_type is null then null
        else a.account_type like 'income%' or a.account_type like 'expense%'
    end as is_profit_and_loss,
    l.journal_id,
    l.parent_state,
    l.display_type,

    l.debit,
    l.credit,
    l.balance,
    l.amount_currency,
    l.quantity,
    l.price_unit,
    l.price_subtotal,
    l.price_total,
    l.discount,

    -- Signed invoiced revenue. A credit note is a positive price_subtotal on a
    -- move of type out_refund; the sign lives in the move type, not in the
    -- amount. Netting it here means mart_revenue_daily cannot get it wrong and
    -- no dashboard has to know the rule.
    case
        when m.move_type = 'out_refund' then -l.price_subtotal
        when m.move_type = 'out_invoice' then l.price_subtotal
        else 0
    end as revenue_signed_subtotal,
    (m.move_type in ('out_invoice', 'out_refund') and l.display_type = 'product') as is_revenue_line
from {{ ref('stg_account_move_line') }} as l
inner join {{ ref('stg_account_move') }} as m
    on
        l.tenant_id = m.tenant_id
        and l.account_move_id = m.account_move_id
-- LEFT, not inner. account_id is nullable on a journal item - section and note
-- lines carry none - and an inner join here would drop them from the fact
-- entirely, silently changing the set that debit == credit is asserted over.
-- That invariant is the one thing this table exists to guarantee.
left join {{ ref('stg_account_account') }} as a
    on
        l.tenant_id = a.tenant_id
        and l.account_id = a.account_id
