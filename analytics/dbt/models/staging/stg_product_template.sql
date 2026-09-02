{{ config(materialized='view') }}

-- Product templates. `name` and the description columns are Odoo 19 jsonb translation maps; ->>'en_US' is the only populated key in this database and the extraction is done here so no downstream model has to know the column is jsonb.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('product_template') }}
)

select
    _tenant_id as tenant_id,
    id as product_tmpl_id,
    default_code as template_default_code,
    type as product_type,
    categ_id,
    uom_id,
    list_price,
    active,
    sale_ok,
    purchase_ok,
    is_storable,
    available_in_pos,
    company_id,
    create_date,
    write_date,
    name ->> 'en_US' as product_name
from latest
