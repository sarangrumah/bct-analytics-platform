-- Legal entities. Type 1: a company rename overwrites, because a company is a
-- registered legal entity and its identity is the registration, not the label.

select
    {{ surrogate_key(['tenant_id', 'company_id']) }} as company_key,
    tenant_id,
    company_id,
    company_name,
    currency_id,
    parent_id,
    active,
    create_date,
    write_date
from {{ ref('stg_res_company') }}
