{{ config(materialized='view') }}

-- Stock moves - the grain of fct_stock_move.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('stock_move') }}
)

select
    _tenant_id as tenant_id,
    id as stock_move_id,
    reference as move_reference,
    state,
    product_id,
    company_id,
    picking_id as stock_picking_id,
    location_id,
    location_dest_id,
    product_qty,
    product_uom_qty,
    quantity,
    picked,
    is_inventory,
    is_in,
    is_out,
    date as move_datetime,
    create_date,
    write_date
from latest
