{{ config(materialized='view') }}

-- Journal items - the grain of fct_account_move_line, and the table the debit==credit reconciliation is asserted on.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('account_move_line') }}
)

select
    _tenant_id as tenant_id,
    id as account_move_line_id,
    move_id as account_move_id,
    journal_id,
    company_id,
    account_id,
    partner_id,
    product_id,
    name as line_description,
    parent_state,
    display_type,
    date as move_date,
    invoice_date,
    debit,
    credit,
    balance,
    amount_currency,
    quantity,
    price_unit,
    price_subtotal,
    price_total,
    discount,
    create_date,
    write_date
from latest
