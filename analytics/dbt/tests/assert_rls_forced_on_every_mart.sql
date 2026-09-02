{{ config(severity='error') }}

-- Row-level security is ENABLED and FORCED on every mart, with both policies.
--
-- THE FAILURE THIS CATCHES is the one that looks like success: dbt drops and
-- recreates a table on --full-refresh, and a policy created by hand goes with
-- it. Nothing errors. Every query still returns rows. The tenant boundary is
-- simply gone, and the next cross-tenant test to run would still pass because
-- the test data only has one tenant in view.
--
-- FORCE matters as much as ENABLE: these tables are owned by `warehouse`, and a
-- table owner is exempt from its own policies unless the table is forced. Merely
-- enabled RLS on an owner-queried table protects nothing.

-- depends_on: {{ ref('dim_date') }}
-- depends_on: {{ ref('dim_partner') }}
-- depends_on: {{ ref('dim_product') }}
-- depends_on: {{ ref('dim_company') }}
-- depends_on: {{ ref('dim_operating_unit') }}
-- depends_on: {{ ref('dim_tenant') }}
-- depends_on: {{ ref('dim_biller') }}
-- depends_on: {{ ref('fct_sale_order_line') }}
-- depends_on: {{ ref('fct_account_move_line') }}
-- depends_on: {{ ref('fct_stock_move') }}
-- depends_on: {{ ref('fct_pos_order_line') }}
-- depends_on: {{ ref('fct_ppob_transaction') }}
-- depends_on: {{ ref('mart_sales_daily') }}
-- depends_on: {{ ref('mart_revenue_daily') }}
-- depends_on: {{ ref('mart_stock_position') }}
-- depends_on: {{ ref('mart_ppob_transaction') }}
-- depends_on: {{ ref('recon_daily') }}

select
    n.nspname as table_schema,
    c.relname as table_name,
    c.relrowsecurity as rls_enabled,
    c.relforcerowsecurity as rls_forced,
    (
        select count(*) from pg_policy as p
        where p.polrelid = c.oid
    ) as policy_count
from pg_class as c
join pg_namespace as n on c.relnamespace = n.oid
where
    n.nspname in ('marts', 'warehouse')
    and c.relkind = 'r'
    and c.relname not in (
        -- warehouse.* metadata is not tenant-scoped data: column_policy is a
        -- schema description, mart_sla is configuration, and pipeline_state and
        -- dbt_run_result are operational telemetry the exporter must read
        -- without holding a tenant scope.
        'column_policy', 'pipeline_state', 'tenant_registry',
        'mart_sla', 'access_audit', 'dbt_run_result'
    )
    and (
        not c.relrowsecurity
        or not c.relforcerowsecurity
        or (
            select count(*) from pg_policy as p
            where p.polrelid = c.oid
        ) <> 2
    )
