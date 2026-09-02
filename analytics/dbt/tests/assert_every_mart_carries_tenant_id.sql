{{ config(severity='error') }}

-- Every fact and dimension carries tenant_id (master prompt §3.3, contract 05).
--
-- Asserted against the catalogue rather than by reading the models, so a mart
-- added later without a tenant_id is caught by this test rather than by a
-- reviewer noticing. It is also the precondition for RLS: the policy predicate
-- is `tenant_id = current_setting('app.tenant_id')`, so a mart without the
-- column cannot be protected at all - warehouse.apply_tenant_rls() raises on
-- exactly that case, and this test is the second net under it.

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

select
    t.table_schema,
    t.table_name
from information_schema.tables as t
where
    t.table_schema = 'marts'
    and t.table_type = 'BASE TABLE'
    and not exists (
        select 1
        from information_schema.columns as c
        where
            c.table_schema = t.table_schema
            and c.table_name = t.table_name
            and c.column_name = 'tenant_id'
    )
