{{ config(materialized='view') }}

-- Partners, with every contract 01 `personal` column already a 64-character HMAC digest and every `sensitive` free-text column already NULL. Masking happened at load; nothing here masks, and nothing here can unmask.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('res_partner') }}
)

select
    _tenant_id as tenant_id,
    id as partner_id,
    name,
    complete_name,
    email,
    phone,
    street,
    street2,
    city,
    zip,
    country_id,
    state_id,
    vat,
    ref,
    function,
    is_company,
    active,
    employee,
    customer_rank,
    supplier_rank,
    company_id,
    parent_id,
    commercial_partner_id,
    lang,
    tz,
    create_date,
    write_date
from latest
