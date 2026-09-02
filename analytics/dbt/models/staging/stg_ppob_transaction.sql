{{ config(materialized='view') }}

-- PPOB transactions. REVENUE SEMANTICS: `amount` is pass-through money owed to the biller and is NOT revenue; the revenue of a PPOB row is `commission`. Summing amount or total_amount and calling it revenue overstates it by roughly 40x (custom_ppob/MODULE_KNOWLEDGE.md §2).
--
-- Staging does exactly three things and no more: deduplicate to the latest
-- version per (tenant, id), drop rows whose latest version is a tombstone, and
-- rename `_tenant_id` to `tenant_id`. All three live in the raw_latest macro so
-- they cannot drift between models. No business logic here.

with latest as (
    {{ raw_latest('ppob_transaction') }}
)

select
    _tenant_id as tenant_id,
    id as ppob_transaction_id,
    name as transaction_name,
    partner_id,
    biller_id,
    product_id,
    operating_unit_id,
    company_id,
    currency_id,
    amount,
    admin_fee,
    commission,
    total_amount,
    customer_ref,
    customer_name,
    biller_reference,
    state,
    requested_at,
    settled_at,
    sla_seconds,
    sla_breached,
    create_date,
    write_date
from latest
