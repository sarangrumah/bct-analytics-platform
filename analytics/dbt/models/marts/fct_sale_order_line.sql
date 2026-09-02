-- GRAIN: one sales order line. (tenant_id, sale_order_line_id) is unique.
--
-- Section and note lines (display_type is not null) are excluded: they carry no
-- quantity and no amount and would inflate every count of "lines sold".

select
    {{ surrogate_key(['l.tenant_id', 'l.sale_order_line_id']) }} as sale_order_line_key,
    l.tenant_id,
    l.sale_order_line_id,
    l.sale_order_id,
    o.order_name,
    o.date_order::date as date_day,
    o.date_order,

    -- Dimension foreign keys, computed from the ids this fact already holds
    -- rather than looked up. See the surrogate-key note on dim_partner.
    {{ surrogate_key(['l.tenant_id', 'o.partner_id']) }} as partner_key,
    {{ surrogate_key(['l.tenant_id', 'l.product_id']) }} as product_key,
    {{ surrogate_key(['l.tenant_id', 'l.company_id']) }} as company_key,
    {{ surrogate_key(['l.tenant_id', 'coalesce(o.operating_unit_id, -1)']) }} as operating_unit_key,
    {{ surrogate_key(['l.tenant_id', 'o.date_order::date']) }} as date_key,

    o.partner_id,
    l.product_id,
    l.company_id,
    coalesce(o.operating_unit_id, -1) as operating_unit_id,
    l.currency_id,

    o.state as order_state,
    l.state as line_state,
    l.invoice_status,
    l.is_downpayment,

    l.product_uom_qty,
    l.qty_delivered,
    l.qty_invoiced,
    l.price_unit,
    l.discount,
    l.price_subtotal,
    l.price_total
from {{ ref('stg_sale_order_line') }} as l
join {{ ref('stg_sale_order') }} as o
    on
        l.tenant_id = o.tenant_id
        and l.sale_order_id = o.sale_order_id
where l.display_type is null
