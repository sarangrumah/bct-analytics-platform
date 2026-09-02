{{ config(materialized='view') }}

-- Sales orders. operating_unit_id is stamped on the document.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('sale_order') }}
)

select
    _tenant_id as tenant_id,
    id as sale_order_id,
    name as order_name,
    state,
    invoice_status,
    partner_id,
    company_id,
    operating_unit_id,
    currency_id,
    user_id,
    team_id,
    date_order,
    amount_untaxed,
    amount_tax,
    amount_total,
    create_date,
    write_date
from latest
