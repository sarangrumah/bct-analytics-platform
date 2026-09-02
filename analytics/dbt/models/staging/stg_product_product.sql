{{ config(materialized='view') }}

-- Product variants - the grain fct_* joins on.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('product_product') }}
)

select
    _tenant_id as tenant_id,
    id as product_id,
    product_tmpl_id,
    default_code,
    barcode,
    -- LEFT AS jsonb ON PURPOSE. standard_price is company_dependent in
    -- Odoo 19, so the value is a MAP keyed by company id -
    -- {"1": 42000.0} - not a scalar. Casting it to text or numeric here
    -- would be the res.partner.barcode mistake again: a map keyed by
    -- something other than this row's grain treated as a single value.
    -- It is unpacked at its true grain in dim_product_cost.
    standard_price,
    active,
    create_date,
    write_date
from latest
