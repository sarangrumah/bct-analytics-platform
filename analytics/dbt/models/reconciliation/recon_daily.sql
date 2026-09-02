{{ config(materialized='table', schema='warehouse') }}

-- RECONCILIATION: the warehouse against ODOO, per day per tenant.
--
-- WHY THIS READS ODOO DIRECTLY. A reconciliation that compares a mart against a
-- control total the warehouse computed for itself is the model marking its own
-- homework: every transformation bug is present on both sides and the test
-- passes. So the source side of every comparison below is a LIVE read of the
-- Odoo database through postgres_fdw, as `warehouse_reader` - a role holding
-- SELECT and REPLICATION and nothing else, so there is no write path back into
-- the ERP (contract 04 §2, anti-pattern §7.10).
--
-- The foreign tables in src_<tenant> are generated from warehouse.column_policy
-- with an EXPLICIT column list, so no `secret`-class column exists as a name
-- the warehouse can type. The columns used below are all `internal`: amounts,
-- quantities and dates.
--
-- THE FAILURE MODE THIS EXISTS FOR is not "somebody wrote a bad join". It is
-- ADR 0001's named risk: an Odoo module upgrade changes a table, replication of
-- that table breaks or starts dropping rows, and the mart drifts QUIETLY. A
-- freshness check would not catch it, because rows are still arriving. Only
-- comparing totals catches it, and only failing the build makes anyone look.
--
-- tests/assert_reconciliation_matches_odoo.sql asserts `passed` on every row at
-- severity: error. It fails the pipeline. It is not a warning.

{% set tenant_list = active_tenants() %}

with source_totals as (
    {% for t in tenant_list %}
    select
        '{{ t }}'::text as tenant_id,
        'sale_line_subtotal'::text as check_name,
        o.date_order::date as date_day,
        sum(l.price_subtotal) as source_value
    from src_{{ t }}.sale_order_line as l
    join src_{{ t }}.sale_order as o on l.order_id = o.id
    where l.display_type is null
    group by 1, 2, 3

    union all
    select
        '{{ t }}'::text as tenant_id,
        'journal_debit'::text as check_name,
        l.date as date_day,
        sum(l.debit) as source_value
    from src_{{ t }}.account_move_line as l
    group by 1, 2, 3

    union all
    select
        '{{ t }}'::text as tenant_id,
        'journal_credit'::text as check_name,
        l.date as date_day,
        sum(l.credit) as source_value
    from src_{{ t }}.account_move_line as l
    group by 1, 2, 3

    -- The debit == credit invariant, expressed as a reconcilable figure so it
    -- lands in the same table and the same alert as everything else. Both sides
    -- must be zero AND must agree.
    union all
    select
        '{{ t }}'::text as tenant_id,
        'journal_balance'::text as check_name,
        l.date as date_day,
        sum(l.debit) - sum(l.credit) as source_value
    from src_{{ t }}.account_move_line as l
    group by 1, 2, 3

    union all
    select
        '{{ t }}'::text as tenant_id,
        'stock_quantity'::text as check_name,
        m.date::date as date_day,
        sum(m.quantity) as source_value
    from src_{{ t }}.stock_move as m
    group by 1, 2, 3

    union all
    select
        '{{ t }}'::text as tenant_id,
        'pos_line_subtotal'::text as check_name,
        o.date_order::date as date_day,
        sum(l.price_subtotal) as source_value
    from src_{{ t }}.pos_order_line as l
    join src_{{ t }}.pos_order as o on l.order_id = o.id
    group by 1, 2, 3

    union all
    select
        '{{ t }}'::text as tenant_id,
        'ppob_commission'::text as check_name,
        p.requested_at::date as date_day,
        sum(p.commission) as source_value
    from src_{{ t }}.ppob_transaction as p
    group by 1, 2, 3
    {% if not loop.last %}
    union all
    {% endif %}
    {% endfor %}
),

warehouse_totals as (
    select
        f.tenant_id,
        'sale_line_subtotal'::text as check_name,
        f.date_day,
        sum(f.price_subtotal) as warehouse_value
    from {{ ref('fct_sale_order_line') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'journal_debit'::text as check_name,
        f.date_day,
        sum(f.debit) as warehouse_value
    from {{ ref('fct_account_move_line') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'journal_credit'::text as check_name,
        f.date_day,
        sum(f.credit) as warehouse_value
    from {{ ref('fct_account_move_line') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'journal_balance'::text as check_name,
        f.date_day,
        sum(f.debit) - sum(f.credit) as warehouse_value
    from {{ ref('fct_account_move_line') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'stock_quantity'::text as check_name,
        f.date_day,
        sum(f.quantity) as warehouse_value
    from {{ ref('fct_stock_move') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'pos_line_subtotal'::text as check_name,
        f.date_day,
        sum(f.price_subtotal) as warehouse_value
    from {{ ref('fct_pos_order_line') }} as f
    group by 1, 2, 3

    union all
    select
        f.tenant_id,
        'ppob_commission'::text as check_name,
        f.date_day,
        sum(f.commission_revenue) as warehouse_value
    from {{ ref('fct_ppob_transaction') }} as f
    group by 1, 2, 3
),

joined as (
    -- FULL OUTER, not inner. An inner join would silently pass a day that
    -- exists on one side only, which is exactly the "replication stopped
    -- delivering this table" failure the whole model exists to catch.
    select
        coalesce(s.tenant_id, w.tenant_id) as tenant_id,
        coalesce(s.check_name, w.check_name) as check_name,
        coalesce(s.date_day, w.date_day) as date_day,
        round(coalesce(s.source_value, 0), 2) as source_value,
        round(coalesce(w.warehouse_value, 0), 2) as warehouse_value
    from source_totals as s
    full outer join warehouse_totals as w
        on
            s.tenant_id = w.tenant_id
            and s.check_name = w.check_name
            and s.date_day = w.date_day
)

-- THE CURRENT DAY IS NEVER RECONCILED, and this is not a way of making the
-- test easier to pass.
--
-- Today is the day the pipeline is actively writing. Odoo committed a row
-- three seconds ago; the WAL record for it has not been decoded yet; both
-- numbers are correct and they differ. A reconciliation that compares a day
-- still being written compares against a moving target, so it flaps, and a
-- test that flaps is a test everyone learns to ignore - which is precisely how
-- a real drift goes unnoticed. Reconciling only CLOSED days is the standard
-- answer and it is what makes a failure here mean something.
--
-- Measured on this database while writing it: a throughput test created 9 250
-- PPOB transactions dated today. Without this predicate every run's result
-- would depend on whether the CDC consumer happened to be caught up at the
-- instant dbt ran.

select
    j.tenant_id,
    j.date_day,
    j.check_name,
    j.source_value,
    j.warehouse_value,
    j.warehouse_value - j.source_value as difference,
    case
        -- journal_balance is an invariant, not just an agreement: a warehouse
        -- that reproduces an imbalance present in Odoo has replicated
        -- faithfully and is still serving books that do not balance.
        when j.check_name = 'journal_balance'
            then j.source_value = 0 and j.warehouse_value = 0
        else j.source_value = j.warehouse_value
    end as passed,
    now() as checked_at
from joined as j
where j.date_day < current_date
