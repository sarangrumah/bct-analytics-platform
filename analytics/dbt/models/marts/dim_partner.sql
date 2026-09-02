-- SCD Type 2 partner dimension, projected from the dbt snapshot.
--
-- SURROGATE KEY STRATEGY - the master prompt asks for this by name, and there
-- are TWO keys here doing two different jobs:
--
--   partner_key          md5(tenant_id | partner_id). The DURABLE key. Stable
--                        across a full refresh, identical for every version of
--                        the same partner, and computable by a fact from the
--                        foreign key it already holds - so a fact never waits
--                        for this dimension to be built and there is no
--                        late-arriving-dimension deadlock.
--   partner_version_key  md5(partner_key | valid_from). The VERSION key, unique
--                        per row of history. This is what `unique` is tested
--                        on; partner_key is deliberately NOT unique here,
--                        because a dimension with history has many rows per
--                        entity and a unique test on the durable key would
--                        force the history out.
--
-- A fact joins partner_key for current-state reporting, or
-- (partner_key, valid_from <= event_ts < valid_to) for as-of reporting.
--
-- THE UNKNOWN MEMBER. POS orders and PPOB transactions are routinely anonymous,
-- so partner_id is NULL and the fact's computed partner_key is
-- md5(tenant | '_dbt_null_'). Giving that a real dimension row means every
-- foreign key in every fact resolves, so `relationships` is a plain test with
-- no exception - and an exception is how a genuinely broken key stops being
-- noticed.
--
-- Three of the columns here - name, ref and function - are SQL keywords, and
-- the linter objects (RF04) to a keyword used as an identifier. They keep those
-- names anyway: they are the physical Odoo column names, the warehouse
-- column should match its source, and quoting them instead just trades RF04
-- for RF06 (unnecessary quotes). The exception is declared, with this
-- reason, in analytics/dbt/.sqlfluff rather than hidden in a noqa comment.
--
-- EVERY personal column below is already a 64-character HMAC digest. Masking
-- happened at load, before the row reached `raw`. dbt performs no masking and
-- can perform none: there is no cleartext anywhere in this database to unmask.

with versions as (

    select
        s.partner_key,
        {{ surrogate_key(['s.partner_key', 's.dbt_valid_from']) }} as partner_version_key,
        s.tenant_id,
        s.partner_id,
        s.name,
        s.complete_name,
        s.email,
        s.phone,
        s.street,
        s.city,
        s.zip,
        s.country_id,
        s.state_id,
        s.vat,
        s.ref,
        s.function,
        s.is_company,
        s.employee,
        s.active,
        s.customer_rank,
        s.supplier_rank,
        s.company_id,
        s.parent_id,
        s.commercial_partner_id,
        s.lang,
        s.tz,
        false as is_unknown,
        s.dbt_valid_from as valid_from,
        coalesce(s.dbt_valid_to, timestamp '9999-12-31 00:00:00') as valid_to,
        (s.dbt_valid_to is null) as is_current
    from {{ ref('scd_res_partner') }} as s

),

unknown_member as (

    select
        {{ surrogate_key(['t.tenant_id', 'null']) }} as partner_key,
        md5(t.tenant_id || '|unknown-partner') as partner_version_key,
        t.tenant_id,
        null::integer as partner_id,
        null::text as name,
        null::text as complete_name,
        null::text as email,
        null::text as phone,
        null::text as street,
        null::text as city,
        null::text as zip,
        null::integer as country_id,
        null::integer as state_id,
        null::text as vat,
        null::text as ref,
        null::text as function,
        null::boolean as is_company,
        null::boolean as employee,
        true as active,
        null::integer as customer_rank,
        null::integer as supplier_rank,
        null::integer as company_id,
        null::integer as parent_id,
        null::integer as commercial_partner_id,
        null::text as lang,
        null::text as tz,
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
