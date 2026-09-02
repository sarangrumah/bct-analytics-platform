-- GRAIN: one stock move. (tenant_id, stock_move_id) is unique.
--
-- operating_unit_id is resolved in int_stock_move_operating_unit, which is
-- where the "a move with no picking has no Operating Unit" problem is handled
-- explicitly rather than by an inner join that would drop those rows.

select
    {{ surrogate_key(['m.tenant_id', 'm.stock_move_id']) }} as stock_move_key,
    m.tenant_id,
    m.stock_move_id,
    m.stock_picking_id,
    m.move_reference,
    m.move_datetime::date as date_day,
    m.move_datetime,

    {{ surrogate_key(['m.tenant_id', 'm.product_id']) }} as product_key,
    {{ surrogate_key(['m.tenant_id', 'm.company_id']) }} as company_key,
    {{ surrogate_key(['m.tenant_id', 'm.operating_unit_id']) }} as operating_unit_key,
    {{ surrogate_key(['m.tenant_id', 'm.move_datetime::date']) }} as date_key,

    m.product_id,
    m.company_id,
    m.operating_unit_id,
    m.operating_unit_unassigned,
    m.location_id,
    m.location_dest_id,
    m.state,
    m.picked,
    m.is_inventory,
    m.is_in,
    m.is_out,
    m.picking_state,

    m.product_qty,
    m.product_uom_qty,
    m.quantity,
    -- Signed movement, so a position is a plain sum. A move that is neither in
    -- nor out (an internal transfer) contributes zero to a net position, which
    -- is correct: it moved location, not stock level.
    case
        when m.is_in then m.quantity
        when m.is_out then -m.quantity
        else 0
    end as signed_quantity
from {{ ref('int_stock_move_operating_unit') }} as m
