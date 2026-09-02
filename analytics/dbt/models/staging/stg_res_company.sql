{{ config(materialized='view') }}

-- Legal entities. `name` is contract 01 `public` and lands verbatim.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('res_company') }}
)

select
    _tenant_id as tenant_id,
    id as company_id,
    name as company_name,
    currency_id,
    parent_id,
    active,
    email,
    phone,
    create_date,
    write_date
from latest
