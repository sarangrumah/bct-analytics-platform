# -*- coding: utf-8 -*-
"""X-Store vs Odoo reconciliation, one row per source transaction.

Reads the X24DN rows already staged on ``retail.import.line`` and folds them up
to the transaction the cashier actually rang, then puts the POS order Odoo
booked (if any) beside it. Nothing is recomputed from the file: this is what the
importer saw, so a discrepancy here is a real discrepancy and not a parsing
difference.

Three implementation notes, each of which cost a measurement:

* **``json_to_record``, not repeated ``->>``.** ``raw_data_json`` is stored as
  ``text``, so every ``(raw_data_json::json)->>'k'`` casts the whole document
  again. Pulling nine fields that way took 8.8 s over 48k rows; ``json_to_record``
  parses once and returns them all in 1.5 s. Wrapping the cast in a LATERAL
  subquery does *not* help -- PostgreSQL inlines it and re-evaluates per
  reference. Casting to ``jsonb`` instead is worse still (13 s), because the
  binary conversion is more expensive than the parse it saves.

* **A re-imported day appears in more than one log.** Recovering the July gap
  left 25-Jul with both its original log and a forced re-import; summing across
  both would double every transaction of that day. ``dense_rank`` keeps only the
  newest log per transaction, which also gives the right answer for status: a
  transaction parked on the first run and posted on the second reads *posted*.

* **``aggregate_key`` is empty on rejected rows.** It looks like the natural key
  and is even indexed, but the importer only writes it once a row survives to
  posting -- so the very rows this report exists for would have been invisible.
  The key is rebuilt from the payload instead.
"""

from odoo import fields, models, tools

# The columns lifted out of each staged row. Keep in step with the json_to_record
# type list in the view below; a name that is not in the payload comes back NULL
# rather than raising, which is why an older file with fewer columns still loads.
_STATUS = [
    ("posted", "Posted"),
    ("parked", "Parked"),
    ("skipped", "Skipped"),
    ("missing", "Not in Odoo"),
]


class RetailImportRecon(models.Model):
    _name = "retail.import.recon"
    _description = "Retail Import — X-Store vs Odoo Reconciliation"
    _auto = False
    _rec_name = "txn_ref"
    _order = "trans_date desc, store_code, transnum"

    # --- transaction header -------------------------------------------------
    txn_ref = fields.Char(
        "Receipt", readonly=True, help="Store-register-transaction, the same reference the POS order carries."
    )
    store_code = fields.Char("Store Code", readonly=True)
    store_name = fields.Char("Store", readonly=True)
    trans_date = fields.Date("Trading Day", readonly=True)
    register = fields.Char("Register", readonly=True)
    transnum = fields.Char("Transaction", readonly=True)
    staff_name = fields.Char("Cashier", readonly=True)
    member_id = fields.Char("Member", readonly=True)
    transaction_note = fields.Char("Note", readonly=True)

    # --- what the file said -------------------------------------------------
    line_count = fields.Integer("Source Lines", readonly=True)
    source_qty = fields.Float("Source Qty", readonly=True)
    source_amount = fields.Float("Source Amount", readonly=True)
    source_tax = fields.Float("Source Tax", readonly=True)

    # --- what Odoo booked ---------------------------------------------------
    pos_order_id = fields.Many2one("pos.order", "POS Order", readonly=True)
    odoo_amount = fields.Float("Odoo Amount", readonly=True)
    difference = fields.Float(
        "Difference",
        readonly=True,
        help="Source amount less what Odoo booked. Anything other than zero needs explaining.",
    )

    # --- outcome ------------------------------------------------------------
    status = fields.Selection(_STATUS, "Status", readonly=True)
    reason = fields.Char("Reason", readonly=True, help="The importer's own message for a rejected transaction.")
    log_id = fields.Many2one("retail.import.log", "Import Log", readonly=True)
    company_id = fields.Many2one("res.company", "Company", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute(
            f"""
            CREATE OR REPLACE VIEW {self._table} AS (
            WITH src AS (
                SELECT
                    l.log_id,
                    l.state                                  AS line_state,
                    l.error_message,
                    l.target_res_id,
                    g.company_id,
                    r.store_code,
                    r.store_name,
                    r.trans_date::date                       AS trans_date,
                    COALESCE(NULLIF(r.register, ''), '1')    AS register,
                    r.transnum,
                    r.staff_name,
                    r.member_id,
                    r.transaction_note,
                    COALESCE(r.total_amount, 0)              AS total_amount,
                    COALESCE(r.tax_amount, 0)                AS tax_amount,
                    COALESCE(r.net_qty, 0)                   AS net_qty
                FROM retail_import_line l
                JOIN retail_import_log g ON g.id = l.log_id
                CROSS JOIN LATERAL json_to_record((l.raw_data_json)::json) AS r(
                    store_code       text,
                    store_name       text,
                    trans_date       text,
                    register         text,
                    transnum         text,
                    staff_name       text,
                    member_id        text,
                    transaction_note text,
                    total_amount     numeric,
                    tax_amount       numeric,
                    net_qty          numeric
                )
                WHERE g.file_type = 'x24'
                  AND l.raw_data_json IS NOT NULL
                  AND l.raw_data_json <> ''
                  AND r.transnum IS NOT NULL
                  AND r.trans_date IS NOT NULL
            ),
            ranked AS (
                SELECT src.*,
                       dense_rank() OVER (
                           PARTITION BY store_code, trans_date, register, transnum
                           ORDER BY log_id DESC
                       ) AS log_rank
                FROM src
            ),
            agg AS (
                SELECT
                    store_code,
                    max(store_name)                          AS store_name,
                    trans_date,
                    register,
                    transnum,
                    store_code || '-' || register || '-' || transnum AS txn_ref,
                    max(staff_name)                          AS staff_name,
                    max(member_id)                           AS member_id,
                    max(transaction_note)                    AS transaction_note,
                    count(*)::int                            AS line_count,
                    sum(net_qty)                             AS source_qty,
                    sum(total_amount)                        AS source_amount,
                    sum(tax_amount)                          AS source_tax,
                    max(log_id)                              AS log_id,
                    max(company_id)                          AS company_id,
                    max(target_res_id)                       AS target_res_id,
                    bool_or(line_state = 'error')            AS has_error,
                    bool_and(line_state = 'skipped')         AS all_skipped,
                    -- Every line of a parked transaction carries the same message,
                    -- so any one of them is the reason for the whole receipt.
                    max(CASE WHEN line_state = 'error' THEN error_message END) AS reason
                FROM ranked
                WHERE log_rank = 1
                GROUP BY store_code, trans_date, register, transnum
            )
            SELECT
                row_number() OVER (ORDER BY a.trans_date DESC, a.store_code, a.transnum)::int AS id,
                a.txn_ref,
                a.store_code,
                a.store_name,
                a.trans_date,
                a.register,
                a.transnum,
                a.staff_name,
                a.member_id,
                a.transaction_note,
                a.line_count,
                a.source_qty,
                a.source_amount,
                a.source_tax,
                po.id                                        AS pos_order_id,
                COALESCE(po.amount_total, 0)                 AS odoo_amount,
                a.source_amount - COALESCE(po.amount_total, 0) AS difference,
                -- Status answers "is this money in Odoo", not "did the last run
                -- complain". A transaction the importer rejected but which is in
                -- Odoo anyway -- posted by an earlier run, then re-imported into a
                -- failure -- is posted; its reason stays visible in the next column
                -- so the complaint is not lost.
                CASE
                    WHEN po.id IS NOT NULL THEN 'posted'
                    WHEN a.has_error       THEN 'parked'
                    WHEN a.all_skipped     THEN 'skipped'
                    ELSE 'missing'
                END                                          AS status,
                a.reason,
                a.log_id,
                a.company_id
            FROM agg a
            -- Prefer the order the importer itself recorded; fall back to the
            -- receipt reference so a transaction that posted through a later
            -- re-import still lines up with its order.
            LEFT JOIN LATERAL (
                SELECT o.id, o.amount_total
                FROM pos_order o
                WHERE o.id = a.target_res_id
                   OR o.pos_reference = a.txn_ref
                ORDER BY (o.id = a.target_res_id) DESC NULLS LAST
                LIMIT 1
            ) po ON TRUE
            )
        """
        )
