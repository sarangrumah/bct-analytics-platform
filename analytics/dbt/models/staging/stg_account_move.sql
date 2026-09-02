{{ config(materialized='view') }}

-- Journal entries and invoices. `access_token` and `inalterable_hash` are contract 01 `secret` and do not exist as columns in raw, so they cannot be named here.
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('account_move') }}
)

select
    _tenant_id as tenant_id,
    id as account_move_id,
    name as move_name,
    state,
    move_type,
    payment_state,
    partner_id,
    commercial_partner_id,
    company_id,
    operating_unit_id,
    journal_id,
    currency_id,
    date as move_date,
    invoice_date,
    amount_untaxed,
    amount_tax,
    amount_total,
    amount_residual,
    create_date,
    write_date
from latest
