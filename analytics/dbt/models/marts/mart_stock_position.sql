-- GRAIN: (tenant_id, product_key, company_id, operating_unit_id)
--
-- Net position derived from completed moves, not from stock.quant. That is a
-- deliberate choice and it is worth being explicit about the trade: quant is
-- Odoo's authoritative on-hand figure, but it is a mutable snapshot with no
-- history, so a warehouse built on it can answer "how much now" and nothing
-- else. Summing signed moves gives the same answer AND makes every historical
-- position reconstructible, which is the point of having a warehouse.
--
-- Only `done` moves count. A reserved or waiting move has not changed stock.
--
-- VALUATION, and the NULL in it is load-bearing. unit_cost comes from
-- dim_product_cost, which unpacks Odoo's company_dependent standard_price map
-- at its true (product, company) grain. The join is a LEFT join because a
-- product can have no cost recorded - 2 of the 14 in this database - and for
-- those rows stock_valuation is NULL rather than 0.
--
-- That distinction matters more than it looks. NULL here means "we do not know
-- what this stock is worth"; 0 would mean "this stock is worth nothing", and
-- coalescing would understate a total while making it look complete. But NULL
-- has its own trap - sum() skips NULLs silently, so a total over a position
-- with unpriced products looks like a finished number and is not. That is why
-- has_unit_cost is carried on every row: a consumer can aggregate
-- count(*) FILTER (WHERE NOT has_unit_cost) alongside the total and say how
-- much of the position is unvalued, instead of quietly reporting a partial
-- figure as a whole one.

with position as (

    select
        tenant_id,
        product_key,
        company_key,
        operating_unit_key,
        company_id,
        operating_unit_id,
        product_id,
        sum(case when is_in then quantity else 0 end) as qty_in,
        sum(case when is_out then quantity else 0 end) as qty_out,
        sum(signed_quantity) as net_qty,
        count(*) as move_count,
        count(*) filter (where is_inventory) as inventory_adjustment_count,
        max(move_datetime) as last_move_at
    from {{ ref('fct_stock_move') }}
    where state = 'done'
    group by 1, 2, 3, 4, 5, 6, 7

)

select
    p.tenant_id,
    p.product_key,
    p.company_key,
    p.operating_unit_key,
    p.company_id,
    p.operating_unit_id,
    p.product_id,
    p.qty_in,
    p.qty_out,
    p.net_qty,
    p.move_count,
    p.inventory_adjustment_count,
    p.last_move_at,
    c.unit_cost,
    (c.unit_cost is not null) as has_unit_cost,
    p.net_qty * c.unit_cost as stock_valuation
from position as p
left join {{ ref('dim_product_cost') }} as c
    on
        p.tenant_id = c.tenant_id
        and p.product_key = c.product_key
        and p.company_id = c.company_id
