-- GRAIN: (tenant_id, date_day, operating_unit_id, company_id, revenue_channel,
--         partner_key, product_key)
--
-- THE THREE REVENUE CHANNELS, unioned rather than summed into one number,
-- because they are not the same kind of money and adding them without saying so
-- is how a dashboard overstates revenue:
--
--   invoice           net invoiced revenue excluding tax, credit notes netted
--                     off. This is contract 03's `revenue_net`.
--   pos               POS lines excluding tax. A separate channel because a POS
--                     order may or may not also produce an invoice, and summing
--                     both without care double-counts.
--   ppob_commission   the COMMISSION only. `amount` on a PPOB transaction is
--                     pass-through money owed to the biller and is not revenue
--                     at all; including it would overstate by roughly 40x.
--
-- A caller that wants total revenue sums across revenue_channel deliberately.
-- A caller that does not know the difference gets a channel breakdown rather
-- than a wrong single number.

with invoiced as (
    select
        tenant_id,
        date_day,
        operating_unit_id,
        company_id,
        partner_key,
        product_key,
        operating_unit_key,
        company_key,
        date_key,
        'invoice'::text as revenue_channel,
        sum(revenue_signed_subtotal) as revenue_net,
        count(*) as source_row_count
    from {{ ref('fct_account_move_line') }}
    where
        is_revenue_line
        and parent_state = 'posted'
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
),

pos as (
    select
        tenant_id,
        date_day,
        operating_unit_id,
        company_id,
        partner_key,
        product_key,
        operating_unit_key,
        company_key,
        date_key,
        'pos'::text as revenue_channel,
        sum(price_subtotal) as revenue_net,
        count(*) as source_row_count
    from {{ ref('fct_pos_order_line') }}
    where order_state in ('paid', 'done', 'invoiced')
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
),

ppob as (
    select
        tenant_id,
        date_day,
        operating_unit_id,
        company_id,
        partner_key,
        product_key,
        operating_unit_key,
        company_key,
        date_key,
        'ppob_commission'::text as revenue_channel,
        sum(commission_revenue) as revenue_net,
        count(*) as source_row_count
    from {{ ref('fct_ppob_transaction') }}
    where state = 'success'
    group by 1, 2, 3, 4, 5, 6, 7, 8, 9
)

select * from invoiced
union all
select * from pos
union all
select * from ppob
