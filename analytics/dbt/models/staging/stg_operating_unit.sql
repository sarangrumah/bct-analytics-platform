{{ config(materialized='view') }}

-- Operating Units. Natural key is (company_id, code) - `code` alone is unique only within a company (custom_operating_unit/MODULE_KNOWLEDGE.md §2). complete_name and parent_path are stored columns, so the hierarchy needs no recursive CTE.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('operating_unit') }}
)

select
    _tenant_id as tenant_id,
    id as operating_unit_id,
    name as operating_unit_name,
    code as operating_unit_code,
    complete_name,
    company_id,
    parent_id,
    parent_path,
    manager_id,
    active,
    create_date,
    write_date
from latest
