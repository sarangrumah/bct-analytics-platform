-- One row per product VARIANT, carrying its template's attributes.
--
-- Facts join products at the variant grain (product_product.id), but every
-- attribute a dashboard groups by - name, category, list price - lives on the
-- template. Doing the join once here means dim_product and every fact see the
-- same definition of "the product", and it is the model the SCD2 snapshot
-- watches, so a template rename produces one new dimension version rather than
-- one per variant per model.

select
    v.tenant_id,
    v.product_id,
    v.product_tmpl_id,
    -- A variant's own code overrides the template's; Odoo falls back the same way.
    t.product_name,
    t.product_type,
    t.categ_id,
    t.uom_id,
    t.list_price,
    t.sale_ok,
    t.purchase_ok,
    t.is_storable,
    t.available_in_pos,
    v.barcode,
    t.company_id,
    coalesce(v.default_code, t.template_default_code) as default_code,
    (v.active and t.active) as active,
    greatest(v.write_date, t.write_date) as write_date
from {{ ref('stg_product_product') }} as v
join {{ ref('stg_product_template') }} as t
    on
        v.tenant_id = t.tenant_id
        and v.product_tmpl_id = t.product_tmpl_id
