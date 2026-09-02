-- ===========================================================================
-- 50-seed.sql — the two metadata tables that are configuration, not data.
--
-- Idempotent: every statement is an upsert keyed on the natural key, so
-- re-applying converges rather than duplicating, and an operator edit is
-- overwritten by the tracked value (the ADR is authoritative, not the
-- database -- same posture as custom_pdp_masking's noupdate="0" rules).
-- ===========================================================================

-- Created and executed as the owning role, not as the applying superuser.
SET ROLE :wh_user;

-- ---------------------------------------------------------------------------
-- Per-mart freshness SLA — GATE 2 accepted this table AS WRITTEN, including
-- its deliberate non-uniformity. PPOB is 60 s because SLA breaches are the
-- point of that view; finance is 60 min because financial reporting tolerates
-- hourly and a uniform-strict policy was explicitly rejected as wasting VPS
-- headroom. Do not "tidy" these into one number.
-- ---------------------------------------------------------------------------
INSERT INTO warehouse.mart_sla (mart_name, sla_seconds, on_breach, source_tables, note) VALUES
  ('mart_ppob_transaction',   60,  'page',
     ARRAY['ppob_transaction','ppob_biller'],
     'PPOB is operational. A stale PPOB view hides exactly the SLA breaches it exists to show.'),
  ('fct_ppob_transaction',    60,  'page',
     ARRAY['ppob_transaction','ppob_biller'],
     'Same source, same SLA as the aggregate it feeds.'),
  ('mart_stock_position',     300, 'alert',
     ARRAY['stock_move','stock_picking'],
     'Dashboard shows is_stale on breach.'),
  ('fct_stock_move',          300, 'alert',
     ARRAY['stock_move','stock_picking'], NULL),
  ('mart_sales_daily',        300, 'alert',
     ARRAY['sale_order','sale_order_line'],
     'Dashboard shows is_stale on breach.'),
  ('fct_sale_order_line',     300, 'alert',
     ARRAY['sale_order','sale_order_line'], NULL),
  ('fct_pos_order_line',      300, 'alert',
     ARRAY['pos_order','pos_order_line'],
     'POS is a revenue channel in the metric contract and shares the sales SLA.'),
  ('mart_revenue_daily',      900, 'alert',
     ARRAY['account_move','account_move_line','ppob_transaction','pos_order','pos_order_line'],
     'Widest source set of any mart: net revenue nets credit notes off invoiced revenue.'),
  ('mart_account_move_line',  3600,'alert',
     ARRAY['account_move','account_move_line'],
     'Financial reporting tolerates hourly. ADR 0001, freshness table.'),
  ('fct_account_move_line',   3600,'alert',
     ARRAY['account_move','account_move_line'], NULL),
  ('dim_partner',             3600,'alert', ARRAY['res_partner'],       'SCD2 dimension.'),
  ('dim_product',             3600,'alert', ARRAY['product_product','product_template'], 'SCD2 dimension.'),
  ('dim_company',             3600,'alert', ARRAY['res_company'],       NULL),
  ('dim_operating_unit',      3600,'alert', ARRAY['operating_unit'],    NULL)
ON CONFLICT (mart_name) DO UPDATE SET
  sla_seconds   = EXCLUDED.sla_seconds,
  on_breach     = EXCLUDED.on_breach,
  source_tables = EXCLUDED.source_tables,
  note          = EXCLUDED.note;

-- ---------------------------------------------------------------------------
-- Tenant registry.
--
-- `bct` is the real tenant: it is the Odoo database name, which is what
-- contract 05 defines _tenant_id to be.
--
-- `bct_t2` is a TEST TENANT and is flagged as one. It exists because tenant
-- isolation cannot be proven with one tenant -- "returns zero rows for another
-- tenant's data" needs another tenant to have data. It mirrors the same Odoo
-- database under a second tenant identity and, critically, a DIFFERENT SALT,
-- which is also what makes contract 01's cross-tenant separation property
-- testable: the same partner must hash differently in the two tenants.
--
-- It is NOT a second Odoo database. Standing one up to develop against is
-- forbidden by master prompt §3.0 / anti-pattern §7.1, and this achieves the
-- isolation proof without it. is_test_tenant is surfaced on dim_tenant so no
-- dashboard mistakes it for production volume.
-- ---------------------------------------------------------------------------
INSERT INTO warehouse.tenant_registry
  (tenant_id, display_name, source_database, slot_name, publication, mask_salt_env, is_test_tenant, active) VALUES
  ('bct',    'BCT (primary)',        'bct', 'bct_slot_bct',    'bct_cdc_bct',    'WAREHOUSE_MASK_SALT_BCT',     false, true),
  ('bct_t2', 'BCT isolation tenant', 'bct', 'bct_slot_bct_t2', 'bct_cdc_bct_t2', 'WAREHOUSE_MASK_SALT_DEFAULT', true,  true)
ON CONFLICT (tenant_id) DO UPDATE SET
  display_name    = EXCLUDED.display_name,
  source_database = EXCLUDED.source_database,
  slot_name       = EXCLUDED.slot_name,
  publication     = EXCLUDED.publication,
  mask_salt_env   = EXCLUDED.mask_salt_env,
  is_test_tenant  = EXCLUDED.is_test_tenant,
  active          = EXCLUDED.active;

RESET ROLE;
