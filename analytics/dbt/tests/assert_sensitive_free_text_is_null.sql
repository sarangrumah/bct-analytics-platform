{{ config(severity='error') }}

-- A `sensitive` column carrying mask_null must hold NULL everywhere.
--
-- These are the free-text fields where anything at all can have been typed,
-- including an Art. 4(3) identifier, so nothing may survive - not even a
-- digest (contract 01: "free-text fields dropped to NULL"). The column still
-- EXISTS in the landing zone and keeps its type; what must never appear is a
-- value.
--
-- Checked in `raw`, not in a mart, on purpose: raw is where a value would have
-- to survive in order to reach a mart at all, so this catches the leak one
-- layer earlier than any mart-level check could.
--
-- The four columns below are the mask_null columns on tables that feed a mart.
-- Enumerating them rather than walking column_policy is a real limitation: a
-- dbt test cannot execute dynamic SQL, and generating one test per policy row
-- would need a custom generic test over a relation-and-column pair. The
-- coverage gap is closed from the other side by
-- analytics/warehouse/bin/warehouse_ctl.py verify, which walks the whole policy.

select
    'raw.res_partner' as offending_table,
    'comment' as offending_column,
    count(*) as offending_rows
from {{ source('raw', 'res_partner') }}
where comment is not null
having count(*) > 0

union all
select
    'raw.ppob_transaction' as offending_table,
    'failure_reason' as offending_column,
    count(*) as offending_rows
from {{ source('raw', 'ppob_transaction') }}
where failure_reason is not null
having count(*) > 0

union all
select
    'raw.account_move' as offending_table,
    'narration' as offending_column,
    count(*) as offending_rows
from {{ source('raw', 'account_move') }}
where narration is not null
having count(*) > 0

union all
select
    'raw.sale_order' as offending_table,
    'note' as offending_column,
    count(*) as offending_rows
from {{ source('raw', 'sale_order') }}
where note is not null
having count(*) > 0
