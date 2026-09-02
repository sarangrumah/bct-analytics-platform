-- Operating Units, plus one synthetic "unassigned" member per tenant.
--
-- NATURAL KEY is (company_id, code) - `code` alone is unique only within a
-- company. The surrogate is still keyed on (tenant_id, operating_unit_id)
-- because that is what a fact holds and can compute without a lookup.
--
-- THE UNASSIGNED MEMBER (operating_unit_id = -1) exists because stock.move has
-- no Operating Unit of its own and reaches one only through its picking; moves
-- with no picking have none at all. Giving that case a real dimension member
-- means the join is a plain inner join everywhere downstream and nothing is
-- dropped. The alternative - allowing a NULL foreign key - makes every
-- relationships test either fail or need an exception, and exceptions are how
-- a dropped row stops being noticed.
--
-- Type 1 as shipped, matching the module: renaming a unit overwrites its name.

with units as (
    select
        {{ surrogate_key(['ou.tenant_id', 'ou.operating_unit_id']) }} as operating_unit_key,
        ou.tenant_id,
        ou.operating_unit_id,
        ou.operating_unit_code,
        ou.operating_unit_name,
        ou.complete_name,
        ou.company_id,
        c.company_name,
        ou.parent_id,
        ou.parent_path,
        -- parent_path is Odoo's materialised path ("1/4/9/"). Counting its
        -- segments gives depth without a recursive CTE, and the module
        -- guarantees a hierarchy never spans two companies, so company_id is a
        -- flat attribute rather than an ancestor lookup.
        coalesce(
            array_length(string_to_array(trim(both '/' from ou.parent_path), '/'), 1), 1
        ) as hierarchy_depth,
        ou.active,
        false as is_unassigned
    from {{ ref('stg_operating_unit') }} as ou
    left join {{ ref('stg_res_company') }} as c
        on ou.tenant_id = c.tenant_id and ou.company_id = c.company_id
),

unassigned as (
    select
        {{ surrogate_key(['t.tenant_id', '-1']) }} as operating_unit_key,
        t.tenant_id,
        -1 as operating_unit_id,
        'UNASSIGNED' as operating_unit_code,
        'Tanpa Operating Unit' as operating_unit_name,
        'Tanpa Operating Unit' as complete_name,
        null::integer as company_id,
        null::text as company_name,
        null::integer as parent_id,
        null::text as parent_path,
        0 as hierarchy_depth,
        true as active,
        true as is_unassigned
    from {{ source('warehouse', 'tenant_registry') }} as t
    where t.active
)

select * from units
union all
select * from unassigned
