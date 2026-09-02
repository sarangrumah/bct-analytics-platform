{{ config(materialized='view') }}

-- Billers. sla_target_seconds is the denominator of every PPOB SLA metric.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('ppob_biller') }}
)

select
    _tenant_id as tenant_id,
    id as biller_id,
    name as biller_name,
    code as biller_code,
    category as biller_category,
    sla_target_seconds,
    company_id,
    active,
    create_date,
    write_date
from latest
