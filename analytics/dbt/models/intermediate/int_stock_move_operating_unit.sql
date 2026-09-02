-- Resolve a stock move's Operating Unit, and make the unresolvable case
-- explicit instead of letting it vanish.
--
-- stock.move carries NO operating_unit_id. It reaches its unit through
-- picking_id -> stock_picking.operating_unit_id
-- (custom_operating_unit/MODULE_KNOWLEDGE.md §3, which states this and tells
-- the warehouse to decide what to do about it).
--
-- Moves with no picking - inventory adjustments, scrap, some manufacturing
-- consumption - therefore have no unit at all. In the seeded database that is
-- 9 of 248 moves, and they carry the largest quantities in the dataset. An
-- INNER join would have dropped them and a unit-grained stock mart would have
-- been quietly wrong by the biggest numbers it contains.
--
-- So: a LEFT join to an explicit "unassigned" dimension member (id -1, present
-- in dim_operating_unit for every tenant), plus a boolean a dashboard can
-- filter on. Nothing is dropped and nothing is silently attributed to a branch
-- that did not do it.

select
    m.*,
    p.picking_name,
    p.state as picking_state,
    p.date_done as picking_date_done,
    coalesce(p.operating_unit_id, -1) as operating_unit_id,
    (p.operating_unit_id is null) as operating_unit_unassigned
from {{ ref('stg_stock_move') }} as m
left join {{ ref('stg_stock_picking') }} as p
    on
        m.tenant_id = p.tenant_id
        and m.stock_picking_id = p.stock_picking_id
