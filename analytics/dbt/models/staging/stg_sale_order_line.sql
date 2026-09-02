{{ config(materialized='view') }}

-- Sales order lines - the grain of fct_sale_order_line.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('sale_order_line') }}
)

select
    _tenant_id as tenant_id,
    id as sale_order_line_id,
    order_id as sale_order_id,
    product_id,
    company_id,
    currency_id,
    name as line_description,
    state,
    display_type,
    invoice_status,
    product_uom_qty,
    qty_delivered,
    qty_invoiced,
    price_unit,
    discount,
    price_subtotal,
    price_total,
    is_downpayment,
    create_date,
    write_date
from latest
