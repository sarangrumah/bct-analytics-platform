{{ config(materialized='view') }}

-- POS orders. A real revenue channel in the metric contract.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('pos_order') }}
)

select
    _tenant_id as tenant_id,
    id as pos_order_id,
    name as pos_order_name,
    state,
    partner_id,
    company_id,
    operating_unit_id,
    session_id,
    config_id,
    pos_reference,
    date_order,
    amount_total,
    amount_tax,
    amount_paid,
    amount_return,
    currency_rate,
    is_refund,
    to_invoice,
    create_date,
    write_date
from latest
