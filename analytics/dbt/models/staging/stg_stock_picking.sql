{{ config(materialized='view') }}

-- Transfers. THE ONLY PLACE a stock move can reach an Operating Unit: stock.move carries no operating_unit_id of its own (custom_operating_unit/MODULE_KNOWLEDGE.md §3).
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('stock_picking') }}
)

select
    _tenant_id as tenant_id,
    id as stock_picking_id,
    name as picking_name,
    state,
    partner_id,
    company_id,
    operating_unit_id,
    picking_type_id,
    location_id,
    location_dest_id,
    scheduled_date,
    date_done,
    create_date,
    write_date
from latest
