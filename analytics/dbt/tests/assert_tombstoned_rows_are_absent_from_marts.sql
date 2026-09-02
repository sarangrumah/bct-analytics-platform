{{ config(severity='error') }}

-- A row whose latest landing version is a tombstone must not appear in a mart.
--
-- ADR 0001: every decoded DELETE lands as _op='D', the landing zone stays
-- append-only, and marts filter to the latest non-deleted version per key - so
-- a delete in Odoo removes the row from the mart within the freshness SLA.
--
-- THE SUBTLE HALF, and it is the half worth testing. Filtering tombstones
-- BEFORE ranking rather than after does not leave the deleted row present - it
-- RESURRECTS THE PREVIOUS VERSION as the latest surviving one. A deleted record
-- comes back as live and current, with plausible values and a recent
-- _ingested_at, reconciling against nothing and looking completely healthy.
-- That is the same shape as the other wrong-answers-that-do-not-error found on
-- this project. raw_latest ranks first and checks _op second; this asserts the
-- outcome rather than trusting the macro.
--
-- COVERS TWO TABLES ON PURPOSE. This test originally checked sale_order_line
-- alone, and sale_order_line carries no tombstones in this dataset - so it
-- passed while proving nothing. ppob_transaction carries 9 000 real tombstones
-- from a bulk unlink(), each with a plausible 'U' version behind it, which is
-- exactly the resurrection case above. A test that can only pass is not a test.
--
-- The ranking below deliberately mirrors raw_latest rather than reusing it: a
-- test that calls the macro it is checking would agree with a broken macro.

with ranked_sol as (

    select
        r._tenant_id as tenant_id,
        r.id as source_id,
        r._op as op,
        row_number() over (
            partition by r._tenant_id, r.id
            order by coalesce(r._lsn, '0/0'::pg_lsn) desc, r._ingested_at desc, r._row_id desc
        ) as version_rank
    from {{ source('raw', 'sale_order_line') }} as r

),

ranked_ppob as (

    select
        r._tenant_id as tenant_id,
        r.id as source_id,
        r._op as op,
        row_number() over (
            partition by r._tenant_id, r.id
            order by coalesce(r._lsn, '0/0'::pg_lsn) desc, r._ingested_at desc, r._row_id desc
        ) as version_rank
    from {{ source('raw', 'ppob_transaction') }} as r

),

violations as (

    select
        'sale_order_line' as source_table,
        t.tenant_id,
        t.source_id
    from ranked_sol as t
    inner join {{ ref('fct_sale_order_line') }} as f
        on
            t.tenant_id = f.tenant_id
            and t.source_id = f.sale_order_line_id
    where
        t.version_rank = 1
        and t.op = 'D'

    union all

    select
        'ppob_transaction' as source_table,
        t.tenant_id,
        t.source_id
    from ranked_ppob as t
    inner join {{ ref('fct_ppob_transaction') }} as f
        on
            t.tenant_id = f.tenant_id
            and t.source_id = f.ppob_transaction_id
    where
        t.version_rank = 1
        and t.op = 'D'

)

select
    v.source_table,
    v.tenant_id,
    v.source_id
from violations as v
