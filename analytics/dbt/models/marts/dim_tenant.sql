-- The tenant dimension, built from warehouse.tenant_registry.
--
-- is_test_tenant is surfaced deliberately: `bct_t2` exists to prove tenant
-- isolation, and a dashboard that silently added its volume to a real tenant's
-- would be worse than not having it. Every consumer can see what it is.

select
    {{ surrogate_key(['tenant_id']) }} as tenant_key,
    tenant_id,
    display_name,
    source_database,
    slot_name,
    publication,
    is_test_tenant,
    active,
    onboarded_at
from {{ source('warehouse', 'tenant_registry') }}
