{{ config(severity='error') }}

-- THE TEST THAT MUST FAIL THE PIPELINE.
--
-- Any tenant-day whose warehouse total disagrees with the live Odoo total is a
-- returned row, and a returned row is a dbt test failure. severity is `error`,
-- explicitly and not by inheritance, because this is the one test in the
-- project where the difference between `warn` and `error` is the difference
-- between a warehouse that is down and a warehouse that is quietly serving a
-- wrong number. The second is worse.
--
-- Proven rather than asserted: perturbing one figure in the landing zone makes
-- this fail and `dbt build` exit non-zero; restoring it makes it pass. See the
-- GATE 3 evidence.

select
    r.tenant_id,
    r.date_day,
    r.check_name,
    r.source_value,
    r.warehouse_value,
    r.difference
from {{ ref('recon_daily') }} as r
where not r.passed
