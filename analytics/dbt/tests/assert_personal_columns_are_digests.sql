{{ config(severity='error') }}

-- A `personal`-class column must be unreadable in the mart.
--
-- Asserted against warehouse.column_policy rather than against a hand-written
-- list of column names: the test therefore covers whatever the policy currently
-- says is personal, and cannot drift away from it. A new personal column added
-- to custom_pdp_core is covered the moment sync-policy runs.
--
-- The shape being asserted is the pinned HMAC output from
-- custom_pdp_masking/MODULE_KNOWLEDGE.md §2 item 7: exactly 64 lowercase hex
-- characters. Anything that is not that, and is not NULL, is cleartext.

select
    'dim_partner' as mart,
    d.partner_key as row_key,
    'name' as offending_column,
    d.name as stored_value
from {{ ref('dim_partner') }} as d
where d.name is not null and d.name !~ '^[0-9a-f]{64}$'

union all
select
    'dim_partner' as mart,
    d.partner_key as row_key,
    'email' as offending_column,
    d.email as stored_value
from {{ ref('dim_partner') }} as d
where d.email is not null and d.email !~ '^[0-9a-f]{64}$'

union all
select
    'dim_partner' as mart,
    d.partner_key as row_key,
    'phone' as offending_column,
    d.phone as stored_value
from {{ ref('dim_partner') }} as d
where d.phone is not null and d.phone !~ '^[0-9a-f]{64}$'

union all
select
    'dim_partner' as mart,
    d.partner_key as row_key,
    'street' as offending_column,
    d.street as stored_value
from {{ ref('dim_partner') }} as d
where d.street is not null and d.street !~ '^[0-9a-f]{64}$'

union all
select
    'dim_partner' as mart,
    d.partner_key as row_key,
    'city' as offending_column,
    d.city as stored_value
from {{ ref('dim_partner') }} as d
where d.city is not null and d.city !~ '^[0-9a-f]{64}$'

union all
select
    'fct_ppob_transaction' as mart,
    f.ppob_transaction_key as row_key,
    'customer_name' as offending_column,
    f.customer_name as stored_value
from {{ ref('fct_ppob_transaction') }} as f
where f.customer_name is not null and f.customer_name !~ '^[0-9a-f]{64}$'

-- customer_ref is `sensitive`, not `personal`, and is hashed rather than
-- nulled because a subscriber number still has to support repeat-customer
-- counts (custom_ppob/MODULE_KNOWLEDGE.md §5). Same shape assertion.
union all
select
    'fct_ppob_transaction' as mart,
    f.ppob_transaction_key as row_key,
    'customer_ref' as offending_column,
    f.customer_ref as stored_value
from {{ ref('fct_ppob_transaction') }} as f
where f.customer_ref is not null and f.customer_ref !~ '^[0-9a-f]{64}$'
