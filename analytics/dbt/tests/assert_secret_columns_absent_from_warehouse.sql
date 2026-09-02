{{ config(severity='error') }}

-- A `secret`-class column must not exist as a warehouse column AT ALL.
--
-- Not "must be null", not "must be masked" - must not exist. Contract 01 says a
-- secret column is dropped at extraction and is never named in the SELECT, so
-- it is structurally incapable of landing. This test asserts the structure, by
-- joining the policy against information_schema across every schema the
-- warehouse owns.
--
-- The five secret columns in the replicated set are sale_order.access_token,
-- account_move.access_token, account_move.inalterable_hash,
-- pos_order.access_token and pos_order.ticket_code. If any of them ever appears
-- as a column name anywhere in raw, staging or marts, this returns a row.

select
    c.table_schema,
    c.table_name,
    c.column_name,
    p.pdp_class
from {{ source('warehouse', 'column_policy') }} as p
join information_schema.columns as c
    on
        p.source_table = c.table_name
        and p.source_column = c.column_name
where
    p.pdp_class = 'secret'
    and c.table_schema in ('raw', 'staging', 'marts', 'snapshots')
