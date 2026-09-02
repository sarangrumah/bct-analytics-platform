{% snapshot scd_res_partner %}

{#-
    SCD Type 2 history for partners.

    STRATEGY: `check`, not `timestamp`.
    A timestamp strategy keyed on write_date is the obvious choice and it is
    wrong for this warehouse, for exactly the reason ADR 0001 rejects a
    write_date tap: a direct SQL write or an ON DELETE CASCADE leaves no
    write_date trace. The landing zone sees those changes through logical
    decoding regardless, so the snapshot must compare the VALUES it cares about
    rather than trust a timestamp that may not have moved.

    UNIQUE KEY is partner_key = md5(tenant_id | partner_id), so history is
    per-tenant. Two tenants that both have partner id 42 are two entities with
    two independent histories, which is what tenant isolation means at the
    dimension level.

    hard_deletes='invalidate': a partner whose latest landing row is a tombstone
    gets dbt_valid_to set instead of silently freezing as current. ADR 0001
    requires a delete in Odoo to disappear from the mart within the SLA, and a
    dimension row that stays is_current forever after its entity was deleted is
    that requirement quietly failing.

    check_cols is enumerated rather than 'all'. 'all' would include write_date,
    which changes on every touch, so every no-op write would open a new version
    and the history would be noise. These are the columns a report groups or
    filters by; a change in one of them is a genuinely different partner.
-#}

{{
    config(
        target_schema='snapshots',
        unique_key='partner_key',
        strategy='check',
        hard_deletes='invalidate',
        check_cols=[
            'name', 'complete_name', 'email', 'phone', 'street', 'city', 'zip',
            'country_id', 'state_id', 'vat', 'ref', 'function', 'is_company',
            'active', 'customer_rank', 'supplier_rank', 'company_id',
            'parent_id', 'commercial_partner_id', 'lang', 'tz'
        ]
    )
}}

select
    {{ surrogate_key(['p.tenant_id', 'p.partner_id']) }} as partner_key,
    p.tenant_id,
    p.partner_id,
    p.name,
    p.complete_name,
    p.email,
    p.phone,
    p.street,
    p.street2,
    p.city,
    p.zip,
    p.country_id,
    p.state_id,
    p.vat,
    p.ref,
    p.function,
    p.is_company,
    p.employee,
    p.active,
    p.customer_rank,
    p.supplier_rank,
    p.company_id,
    p.parent_id,
    p.commercial_partner_id,
    p.lang,
    p.tz
from {{ ref('stg_res_partner') }} as p

{% endsnapshot %}
