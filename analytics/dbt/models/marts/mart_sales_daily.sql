-- GRAIN: (tenant_id, date_day, operating_unit_id, company_id, partner_key, product_key)
--
-- Pre-aggregated to the LOWEST grain the metric contract declares, not to the
-- narrowest one a single dashboard needs. Contract 03 lists partner_key and
-- product_key as legal group-by dimensions for sales metrics, so they have to
-- survive the aggregation - a mart rolled up past them cannot answer its own
-- declared contract, and the API would have to fall back to the fact table.

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
    count(*) as line_count,
    count(distinct sale_order_id) as order_count,
    sum(product_uom_qty) as qty_ordered,
    sum(qty_delivered) as qty_delivered,
    sum(qty_invoiced) as qty_invoiced,
    sum(price_subtotal) as amount_untaxed,
    sum(price_total) as amount_total,
    sum(price_total) - sum(price_subtotal) as amount_tax
from {{ ref('fct_sale_order_line') }}
group by 1, 2, 3, 4, 5, 6, 7, 8, 9
