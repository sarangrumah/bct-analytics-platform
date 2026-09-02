-- GRAIN: one POS order line. (tenant_id, pos_order_line_id) is unique.
--
-- POS is a real revenue channel in the metric contract, not a curiosity; it
-- shares the sales freshness SLA.

select
    {{ surrogate_key(['l.tenant_id', 'l.pos_order_line_id']) }} as pos_order_line_key,
    l.tenant_id,
    l.pos_order_line_id,
    l.pos_order_id,
    o.pos_order_name,
    o.pos_reference,
    o.date_order::date as date_day,
    o.date_order,

    {{ surrogate_key(['l.tenant_id', 'o.partner_id']) }} as partner_key,
    {{ surrogate_key(['l.tenant_id', 'l.product_id']) }} as product_key,
    {{ surrogate_key(['l.tenant_id', 'l.company_id']) }} as company_key,
    {{ surrogate_key(['l.tenant_id', 'coalesce(o.operating_unit_id, -1)']) }} as operating_unit_key,
    {{ surrogate_key(['l.tenant_id', 'o.date_order::date']) }} as date_key,

    o.partner_id,
    l.product_id,
    l.company_id,
    coalesce(o.operating_unit_id, -1) as operating_unit_id,
    o.session_id,
    o.config_id,
    o.state as order_state,
    o.is_refund,
    o.to_invoice,

    l.full_product_name,
    l.qty,
    l.price_unit,
    l.discount,
    l.price_subtotal,
    l.price_subtotal_incl,
    l.total_cost
from {{ ref('stg_pos_order_line') }} as l
join {{ ref('stg_pos_order') }} as o
    on
        l.tenant_id = o.tenant_id
        and l.pos_order_id = o.pos_order_id
