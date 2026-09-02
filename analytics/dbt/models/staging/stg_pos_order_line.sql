{{ config(materialized='view') }}

-- POS lines - the grain of fct_pos_order_line.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('pos_order_line') }}
)

select
    _tenant_id as tenant_id,
    id as pos_order_line_id,
    order_id as pos_order_id,
    product_id,
    company_id,
    full_product_name,
    qty,
    price_unit,
    discount,
    price_subtotal,
    price_subtotal_incl,
    total_cost,
    create_date,
    write_date
from latest
