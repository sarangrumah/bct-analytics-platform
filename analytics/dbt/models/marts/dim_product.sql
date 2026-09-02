-- SCD Type 2 product dimension, projected from the dbt snapshot.
--
-- Same two-key strategy as dim_partner, for the same reasons:
--   product_key          md5(tenant_id | product_id) - durable, computable by a
--                        fact from the FK it already holds, stable across a
--                        full refresh. A sequence-backed surrogate is not:
--                        rebuild the dimension and every historical key
--                        changes, silently repointing every fact that stored
--                        one.
--   product_version_key  md5(product_key | valid_from) - unique per version,
--                        and the column `unique` is tested on.
--
-- Watched at the VARIANT grain over int_product, which already carries the
-- template's attributes. A template rename therefore produces one new version
-- per affected variant, which is correct: the variant is what a fact points at.
--
-- THE UNKNOWN MEMBER exists because a journal item frequently has no product at
-- all - 120 of the 431 seeded account_move_line rows are payment-term lines -
-- so its computed product_key is md5(tenant | '_dbt_null_'). Same reasoning as
-- dim_partner: a real row means `relationships` needs no exception, and an
-- exception is how a genuinely broken key stops being noticed.

with versions as (

    select
        s.product_key,
        {{ surrogate_key(['s.product_key', 's.dbt_valid_from']) }} as product_version_key,
        s.tenant_id,
        s.product_id,
        s.product_tmpl_id,
        s.default_code,
        s.product_name,
        s.product_type,
        s.categ_id,
        s.uom_id,
        s.list_price,
        s.sale_ok,
        s.purchase_ok,
        s.is_storable,
        s.available_in_pos,
        s.barcode,
        s.active,
        s.company_id,
        false as is_unknown,
        s.dbt_valid_from as valid_from,
        coalesce(s.dbt_valid_to, timestamp '9999-12-31 00:00:00') as valid_to,
        (s.dbt_valid_to is null) as is_current
    from {{ ref('scd_product') }} as s

),

unknown_member as (

    select
        {{ surrogate_key(['t.tenant_id', 'null']) }} as product_key,
        md5(t.tenant_id || '|unknown-product') as product_version_key,
        t.tenant_id,
        null::integer as product_id,
        null::integer as product_tmpl_id,
        null::text as default_code,
        null::text as product_name,
        null::text as product_type,
        null::integer as categ_id,
        null::integer as uom_id,
        null::numeric as list_price,
        null::boolean as sale_ok,
        null::boolean as purchase_ok,
        null::boolean as is_storable,
        null::boolean as available_in_pos,
        null::text as barcode,
        true as active,
        null::integer as company_id,
        true as is_unknown,
        timestamp '1970-01-01 00:00:00' as valid_from,
        timestamp '9999-12-31 00:00:00' as valid_to,
        true as is_current
    from {{ source('warehouse', 'tenant_registry') }} as t
    where t.active

)

select * from versions
union all
select * from unknown_member
