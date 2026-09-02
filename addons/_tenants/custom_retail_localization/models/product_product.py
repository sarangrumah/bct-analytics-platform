# -*- coding: utf-8 -*-
from odoo import models


class ProductProduct(models.Model):
    _inherit = "product.product"

    # Backs the Product Variants list ordering. Odoo renders the view's
    # default_order as:
    #   WHERE active IS TRUE
    #   ORDER BY COALESCE(is_favorite, FALSE) DESC, default_code, id
    # so the leading term must be indexed as that exact expression -- a plain
    # btree on is_favorite is not usable for the sort. Postgres defaults
    # (DESC -> NULLS FIRST, ASC -> NULLS LAST) already match what Odoo emits,
    # which spells out NULLS only when the order string does.
    _retail_list_order_index = models.Index("(COALESCE(is_favorite, FALSE) DESC, default_code, id) WHERE active IS TRUE")

    def init(self):
        super().init()
        # The product.value indexes are declared from here rather than from an
        # `_inherit = "product.value"` model. Inheriting that model pulls
        # product.value into this module's init_models() pass, and Odoo's
        # registry.check_foreign_keys() then re-creates any missing core foreign
        # key -- including product_value_product_id_fkey, which prd_retail_begbal
        # and rnd_retail have lost. On those two the ALTER TABLE fails against the
        # rows whose product_id no longer resolves, and the upgrade rolls back.
        # Raw DDL keeps product.value out of that pass entirely.
        #
        # Core ships product.value with a primary key and nothing else:
        #  - product_id backs the FK's ON DELETE SET NULL, fired for every variant
        #    delete when an X101 re-import rebuilds the Size x Inseam matrix.
        #  - last_value matches product.product._compute_value ->
        #    _get_last_product_value: filters move_id IS NULL AND company_id = %s
        #    AND lot_id IS NULL, then DISTINCT ON (product_id)
        #    ORDER BY product_id, date DESC, id DESC.
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS product_value_product_id_index
            ON product_value (product_id)
            """
        )
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS product_value_last_value_idx
            ON product_value (company_id, product_id, date DESC, id DESC)
            WHERE move_id IS NULL AND lot_id IS NULL
            """
        )
