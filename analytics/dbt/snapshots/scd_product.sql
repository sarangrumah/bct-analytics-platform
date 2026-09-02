{% snapshot scd_product %}

{#-
    SCD Type 2 history for products, watched at the VARIANT grain over
    int_product (which already carries the template's attributes).

    Same `check` strategy and the same reasoning as scd_res_partner. Watched at
    the variant grain because that is what a fact points at: a template rename
    produces one new version per affected variant, which is correct.

    list_price is in check_cols deliberately. A price change is a real
    dimension event - a report that attributes last quarter's units to this
    quarter's list price is wrong in a way nobody notices.
-#}

{{
    config(
        target_schema='snapshots',
        unique_key='product_key',
        strategy='check',
        hard_deletes='invalidate',
        check_cols=[
            'default_code', 'product_name', 'product_type', 'categ_id', 'uom_id',
            'list_price', 'sale_ok', 'purchase_ok', 'is_storable',
            'available_in_pos', 'barcode', 'active', 'company_id'
        ]
    )
}}

select
    {{ surrogate_key(['p.tenant_id', 'p.product_id']) }} as product_key,
    p.tenant_id,
    p.product_id,
    p.product_tmpl_id,
    p.default_code,
    p.product_name,
    p.product_type,
    p.categ_id,
    p.uom_id,
    p.list_price,
    p.sale_ok,
    p.purchase_ok,
    p.is_storable,
    p.available_in_pos,
    p.barcode,
    p.active,
    p.company_id
from {{ ref('int_product') }} as p

{% endsnapshot %}
