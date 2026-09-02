{{ config(materialized='view') }}

-- Chart of accounts. Exists so fct_account_move_line can carry account_type,
-- which is what splits P&L from balance sheet BY FILTER rather than by shipping
-- two metrics that differ only in name and return identical numbers.
--
-- CLASSIFICATION NOTES, because two of these columns were genuinely decided
-- rather than defaulted (contract 01, ce88c72):
--   account_type  internal / none  - the column this model exists for.
--   code_store    internal / none  - company_dependent jsonb, and it LANDS
--                 VERBATIM. The barcode ruling's transform limb (never HMAC a
--                 map keyed by something other than the data subject) is
--                 satisfied by construction for any non-hashing class; its
--                 reclassify limb followed from barcode being a natural
--                 person's data, which a ledger account is not.
--   note          sensitive + drop_to_null -> always NULL. Physical type is
--                 `text`, so the loader's TEXT_TYPES guard would have happily
--                 accepted a `personal` classification and landed a clean
--                 64-character digest of prose: a pseudonym of nothing,
--                 indistinguishable from a working hash. The type guard catches
--                 unhashable columns, not pointlessly-hashable ones.

with latest as (
    {{ raw_latest('account_account') }}
)

select
    _tenant_id as tenant_id,
    id as account_id,
    account_type,
    active,
    reconcile,
    non_trade,
    -- NO company_id. Odoo 19 shares the chart of accounts across companies
    -- and carries the per-company code in the company_dependent code_store
    -- map instead. Selecting it would have been an assumption from every
    -- other Odoo table's shape rather than from this one's.
    currency_id,
    create_date,
    write_date
from latest
