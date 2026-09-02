# Part of custom_demo_seed. Licence: LGPL-3.
"""Parameterised, idempotent, reproducible demo volume.

Why this module exists: the Phase 4 performance budget is "p95 under 2 s with 12 months of data",
and that cannot be measured against an empty database. This generates the data.

Four properties it must have, and how each is obtained:

* **Idempotent.** Every record is created through :meth:`_ensure`, which registers an
  ``ir.model.data`` external ID. A second run finds the external ID and returns the existing
  record instead of creating a new one, so running the generator twice changes no row count.
* **Reproducible.** All randomness comes from a single ``random.Random(seed)``.
* **Honest about its shape.** See "The parameter-authority rule" below. This is the property the
  first version of this module did NOT have, and it was a real defect.
* **Never in production.** Not ``auto_install``, no module depends on it, and it generates nothing
  at install time.

The parameter-authority rule
----------------------------
Idempotency by external ID has a failure mode that is worse than the problem it solves: once the
records exist, ``_ensure`` returns them **whatever parameters the caller asked for**. So
``generate(partners=4)`` on a database already seeded with 40 returned 40 partners, silently, with
no error - the caller had no way to notice it had been ignored. For Phase 3 that is poison: the
DWH and QA agents seed at chosen volumes to build fixtures and to test reconciliation, and a
reconciliation test that silently ran against the wrong row count is worse than one that fails.

Two mechanisms fix it, and both are needed:

1. **Datasets.** ``dataset`` namespaces the external IDs and every human-visible reference, so two
   different shapes are two different, independently idempotent datasets that never share a
   record. Tests use their own dataset and are therefore hermetic - their result cannot depend on
   what a previous run left in the database.
2. **Shape authority.** The full parameter set of a dataset is recorded when it is first
   generated. A later call for the *same* dataset with a *different* shape raises
   :class:`~odoo.exceptions.UserError` naming every parameter that conflicts. A caller asking for
   a shape it does not get always finds out.

Why there is no ``reset=True``
------------------------------
It was considered and rejected. A dataset's documents include posted journal entries and done
stock moves, and Odoo forbids deleting both **by design** - ``account.move`` guards them with
``_unlink_account_audit_trail_except_once_post`` and ``stock.move`` with
``_unlink_if_draft_or_cancel``. Deleting them means passing ``force_delete`` to bypass the audit
trail. A fixture module that ships an audit-trail bypass is a worse thing to have in the codebase
than the inconvenience it saves, and a reset that silently half-worked would be worse still.

The supported way to get a different shape is a new ``dataset`` name; the supported way to get a
clean slate is a fresh database (``make init-db``). Both are exact.
"""

import json
import logging
import random
import re
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

MODULE = "custom_demo_seed"

#: Dataset used when the caller does not name one.
DEFAULT_DATASET = "default"

#: A dataset name must be lowercase alphanumeric. No underscore: the external-ID prefix is
#: ``<dataset>__``, and allowing underscores in the name would make prefixes ambiguous.
DATASET_RE = re.compile(r"^[a-z][a-z0-9]{0,19}$")

#: ``ir.config_parameter`` key holding a dataset's recorded shape, as JSON.
SHAPE_PARAM = "custom_demo_seed.shape.%s"

#: The parameters that define a dataset. Any difference in any of these is a different dataset
#: shape. ``anchor`` is included because the month buckets are anchored to today: the same numeric
#: parameters run in a different calendar month describe a different 12-month window, and
#: pretending otherwise would silently widen the dataset.
SHAPE_KEYS = (
    "seed",
    "partners",
    "products",
    "operating_units",
    "months",
    "sale_orders_per_month",
    "pos_orders_per_month",
    "ppob_per_month",
    "with_pos",
    "anchor",
    "company_id",
)

OPERATING_UNITS = [
    ("JKT", "Cabang Jakarta"),
    ("BDG", "Cabang Bandung"),
    ("SBY", "Cabang Surabaya"),
    ("MDN", "Cabang Medan"),
]

BILLERS = [
    ("PLN-PRE", "PLN Prabayar (Demo)", "electricity", 20),
    ("PLN-POST", "PLN Pascabayar (Demo)", "electricity", 30),
    ("PDAM", "PDAM Kota (Demo)", "water", 45),
    ("TELCO", "Pulsa Seluler (Demo)", "telco", 15),
    ("INET", "Internet Rumah (Demo)", "internet", 60),
    ("BPJS", "BPJS Kesehatan (Demo)", "insurance", 90),
]

#: Obviously-synthetic person names. Common Indonesian given names, but every record also carries
#: a "(Demo NNN)" suffix, a `.invalid` e-mail domain and a `+62-800-555-` phone, so none of these
#: can be mistaken for a real person's record.
GIVEN_NAMES = [
    "Budi", "Siti", "Agus", "Dewi", "Eko", "Rina", "Joko", "Ani", "Bayu", "Lestari",
    "Andi", "Maya", "Rizki", "Putri", "Hendra", "Sari", "Fajar", "Indah", "Yusuf", "Nurul",
]
FAMILY_NAMES = [
    "Santoso", "Wijaya", "Pratama", "Kusuma", "Hidayat", "Nugroho", "Saputra", "Wahyuni",
    "Permata", "Halim",
]
CITIES = ["Jakarta", "Bandung", "Surabaya", "Medan", "Semarang", "Makassar"]

PRODUCTS = [
    ("P-VCR-010", "Voucher Data Demo 10GB", 55000.0, 42000.0, False),
    ("P-VCR-025", "Voucher Data Demo 25GB", 110000.0, 88000.0, False),
    ("P-TKN-050", "Token Listrik Demo 50rb", 52500.0, 50000.0, False),
    ("P-TKN-100", "Token Listrik Demo 100rb", 102500.0, 100000.0, False),
    ("P-SIM-001", "Kartu Perdana Demo", 25000.0, 15000.0, False),
    ("P-RTR-001", "Router WiFi Demo", 450000.0, 310000.0, False),
    ("P-RTR-002", "Router WiFi Demo Pro", 890000.0, 640000.0, False),
    ("P-CBL-001", "Kabel LAN Demo 5m", 35000.0, 18000.0, False),
    ("P-ADP-001", "Adaptor Demo 12V", 75000.0, 41000.0, False),
    ("P-INS-001", "Jasa Instalasi Demo", 150000.0, 90000.0, True),
    ("P-SVC-001", "Jasa Perawatan Demo", 200000.0, 120000.0, True),
    ("P-CNS-001", "Konsultasi Jaringan Demo", 500000.0, 300000.0, True),
]

#: A STORABLE product deliberately left with NO ``standard_price``.
#:
#: It exists to exercise exactly one branch: ``mart_stock_position.has_unit_cost = false``. DWH
#: wrote and reviewed that branch, then measured that it had never executed - 27 position rows, 27
#: valued, 0 unvalued - because the only two products in this database without a cost (Tips and
#: Down Payment, created by point_of_sale and sale) are NON-storable, so they have no stock moves
#: and never reach a position row at all. DWH recorded it as NOT VERIFIED inside the model file.
#: That correlation is structural rather than accidental: a product with no cost is usually a
#: service. The one shape that breaks it is a storable product, with stock moves, and no cost.
#:
#: Four properties are load-bearing and none of them is decoration:
#:
#: * ``is_storable`` - a non-storable product produces no stock move, so no position row;
#: * NO ``standard_price`` key in the create values - a cost of 0.0 would be WRITTEN into the
#:   company_dependent jsonb map as ``{"1": 0.0}``, ``dim_product_cost`` would emit a row, the LEFT
#:   join would match and ``has_unit_cost`` would come back TRUE. Absent is not the same as zero,
#:   and this is precisely the distinction ``mart_stock_position`` documents;
#: * ``list_price`` 0.0 - not tidiness. A sales price on a deliberately unvalued item invites
#:   ``coalesce(unit_cost, list_price)``, which is the "plausible column that is wrong by a large
#:   factor" that ``dim_product_cost`` measured at 1.46x. It also leaves DWH's list-vs-cost
#:   measurement untouched whichever way that join is written;
#: * ``sale_ok`` / ``purchase_ok`` / ``available_in_pos`` all False - it must reach a position row
#:   through the inventory adjustment ONLY, never through a sale, or the seeded revenue marts
#:   would change shape.
#:
#: It is NOT governed by the ``products`` shape parameter and is NOT in ``SHAPE_KEYS``. It is not
#: a shape choice a caller makes; it is part of what this fixture IS. That is also what makes it
#: appear on a re-run of a dataset that already exists - a dataset seeded before this record
#: existed gains it, rather than being silently skipped because "data already exists", which is the
#: defect this module's shape-authority mechanism was built for.
UNCOSTED_PRODUCT_CODE = "P-NOC-001"
UNCOSTED_PRODUCT_NAME = "Barang Demo Tanpa Harga Pokok"
UNCOSTED_PRODUCT_QTY = 250.0

FAILURE_REASONS = [
    "Saldo deposit biller tidak mencukupi (demo)",
    "Nomor pelanggan tidak ditemukan di sistem biller (demo)",
    "Timeout dari host biller (demo)",
    "Tagihan sudah dibayar melalui kanal lain (demo)",
]

#: Models the dataset tracks by external ID, in the order `summary` reports them.
TRACKED_MODELS = (
    "operating.unit",
    "res.partner",
    "product.template",
    "ppob.biller",
    "sale.order",
    "pos.order",
    "ppob.transaction",
    "stock.move",
)


class DemoSeedGenerator(models.TransientModel):
    """Service object. Transient because it holds no state between runs."""

    _name = "demo.seed.generator"
    _description = "Demo Data Seed Generator"

    seed = fields.Integer(default=20260101, required=True)
    partner_count = fields.Integer(string="Partners", default=40, required=True)
    product_count = fields.Integer(string="Products", default=12, required=True)
    operating_unit_count = fields.Integer(string="Operating Units", default=2, required=True)
    months = fields.Integer(default=12, required=True)
    sale_orders_per_month = fields.Integer(default=10, required=True)
    pos_orders_per_month = fields.Integer(default=8, required=True)
    ppob_per_month = fields.Integer(default=30, required=True)
    with_pos = fields.Boolean(string="Generate POS orders", default=True)
    dataset = fields.Char(
        default=DEFAULT_DATASET,
        required=True,
        help="Independent, separately idempotent dataset. Two datasets never share a record. "
        "Use a new name to seed a different shape alongside an existing one.",
    )

    def action_generate(self):
        self.ensure_one()
        summary = self.generate(
            seed=self.seed,
            partners=self.partner_count,
            products=self.product_count,
            operating_units=self.operating_unit_count,
            months=self.months,
            sale_orders_per_month=self.sale_orders_per_month,
            pos_orders_per_month=self.pos_orders_per_month,
            ppob_per_month=self.ppob_per_month,
            with_pos=self.with_pos,
            dataset=self.dataset,
        )
        message = "\n".join("%s: %s" % (key, value) for key, value in sorted(summary.items()))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Demo data generated"),
                "message": message,
                "sticky": True,
                "type": "success",
            },
        }

    # ==================================================================
    # Dataset naming
    # ==================================================================

    @api.model
    def _dataset_context(self, dataset):
        """Return the naming context for ``dataset``.

        ``prefix``    - external-ID prefix, e.g. ``default__`` or ``unittest__``.
        ``tag``       - upper-case infix for machine references; empty for the default dataset so
                        that its references keep the historical ``DEMO-C-0001`` shape.
        ``tag_lower`` - the same, lower-case, for logins and e-mail local parts.
        """
        # Validated strictly, on the raw value:
        #   * only None means "unspecified"; "" is a caller mistake, not a synonym for default;
        #   * no case folding. Accepting "Default" as "default" would silently drop a caller into
        #     the shared dataset when they plainly meant a distinct one - the same class of
        #     silent substitution this whole mechanism exists to prevent.
        name = DEFAULT_DATASET if dataset is None else dataset
        if not isinstance(name, str) or not DATASET_RE.match(name):
            raise UserError(
                _(
                    "Invalid dataset name %(name)r. Use 1-20 lowercase letters and digits, "
                    "starting with a letter, with no underscore and no capitals. The name is "
                    "matched exactly: 'Default' is not 'default'.",
                    name=dataset,
                )
            )
        is_default = name == DEFAULT_DATASET
        return {
            "name": name,
            "prefix": "%s__" % name,
            "tag": "" if is_default else "%s-" % name.upper(),
            "tag_lower": "" if is_default else "%s." % name,
        }

    @api.model
    def _migrate_legacy_xmlids(self, env, ds):
        """Move pre-dataset external IDs into the default namespace.

        The first release of this module wrote unprefixed external IDs (``partner_0001``). Without
        this, adding the prefix would orphan every record an existing database had already seeded
        and the next run would create a second copy of all of them. Runs once; a no-op afterwards.
        """
        if ds["name"] != DEFAULT_DATASET:
            return
        legacy = env["ir.model.data"].search([("module", "=", MODULE)]).filtered(
            lambda row: "__" not in row.name
        )
        if legacy:
            for row in legacy:
                row.name = ds["prefix"] + row.name
            # Two steps, both required, and in this order:
            #  1. flush - _xmlid_lookup runs a raw `cr.execute`, which does NOT trigger an ORM
            #     flush, so without this it would read the OLD names straight from Postgres;
            #  2. clear the cache - _xmlid_lookup is @ormcache('xmlid'), so even after the flush
            #     env.ref() would keep answering from the pre-rename cache.
            # Miss either one and _ensure fails to find every migrated record and tries to create
            # a second copy: "duplicate key value violates unique constraint
            # operating_unit_code_company_uniq".
            env.flush_all()
            env.registry.clear_cache()
            _logger.info(
                "custom_demo_seed: migrated %d legacy external ID(s) into the '%s' dataset",
                len(legacy), DEFAULT_DATASET,
            )

    # ==================================================================
    # Entry point
    # ==================================================================

    @api.model
    def generate(
        self,
        seed=20260101,
        partners=40,
        products=12,
        operating_units=2,
        months=12,
        sale_orders_per_month=10,
        pos_orders_per_month=8,
        ppob_per_month=30,
        with_pos=True,
        company=None,
        dataset=DEFAULT_DATASET,
    ):
        """Generate demo volume and return a dict of counts.

        Calling it twice with the same arguments creates nothing the second time. Calling it with
        *different* arguments for a dataset that already exists raises, naming the conflict -
        it never silently returns a shape you did not ask for.

        :param seed: RNG seed. Same seed, same data.
        :param months: how many whole months back from today to spread the data over.
        :param dataset: independent namespace; see the module docstring.
        :return: ``{"partners": n, "sale_orders": n, ...}`` - the number of records that now exist
            in this dataset, not the number created by this call.
        """
        if not self.env.user._is_admin():
            raise UserError(_("Only an administrator may generate demo data."))
        if operating_units < 2:
            raise UserError(
                _("At least 2 Operating Units are required: the demo must span more than one.")
            )
        if operating_units > len(OPERATING_UNITS):
            raise UserError(
                _("At most %s Operating Units are defined.", len(OPERATING_UNITS))
            )
        if products > len(PRODUCTS):
            raise UserError(_("At most %s products are defined.", len(PRODUCTS)))
        if months < 1:
            raise UserError(_("months must be at least 1."))

        env = self.env(su=True)
        company = company or env.company
        ds = self._dataset_context(dataset)
        self._migrate_legacy_xmlids(env, ds)

        shape = {
            "seed": seed,
            "partners": partners,
            "products": products,
            "operating_units": operating_units,
            "months": months,
            "sale_orders_per_month": sale_orders_per_month,
            "pos_orders_per_month": pos_orders_per_month,
            "ppob_per_month": ppob_per_month,
            "with_pos": bool(with_pos),
            "anchor": date.today().strftime("%Y-%m"),
            "company_id": company.id,
        }
        self._assert_shape(env, ds, shape)

        rng = random.Random(seed)
        started = datetime.now()
        _logger.info(
            "custom_demo_seed: generating dataset=%s shape=%s", ds["name"], shape
        )

        self._ensure_chart_of_accounts(env, company)
        units = self._ensure_operating_units(env, ds, company, operating_units)
        partner_records = self._ensure_partners(env, ds, company, partners, rng)
        product_records = self._ensure_products(env, ds, company, products)
        biller_records = self._ensure_billers(env, ds, company)
        self._ensure_demo_users(env, ds, units)
        self._ensure_stock(env, ds, company, product_records)
        self._ensure_uncosted_product(env, ds, company)

        pos_configs = self._ensure_pos_configs(env, ds, company, units) if with_pos else None

        for offset in range(months - 1, -1, -1):
            month_start = (date.today().replace(day=1) - relativedelta(months=offset))
            self._seed_sale_orders(
                env, ds, company, month_start, offset, units, partner_records, product_records,
                sale_orders_per_month, rng,
            )
            self._seed_ppob(
                env, ds, company, month_start, offset, units, partner_records, product_records,
                biller_records, ppob_per_month, rng,
            )
            if pos_configs:
                self._seed_pos(
                    env, ds, company, month_start, offset, units, partner_records,
                    product_records, pos_configs, pos_orders_per_month, rng,
                )

        env["ir.config_parameter"].sudo().set_param(
            SHAPE_PARAM % ds["name"], json.dumps(shape, sort_keys=True)
        )
        summary = self.summary(dataset=ds["name"])
        summary["elapsed_seconds"] = round((datetime.now() - started).total_seconds(), 1)
        _logger.info("custom_demo_seed: done dataset=%s %s", ds["name"], summary)
        return summary

    # ==================================================================
    # Shape authority
    # ==================================================================

    @api.model
    def get_shape(self, dataset=DEFAULT_DATASET):
        """Return the recorded shape of ``dataset``, or ``False`` if it has never been seeded."""
        ds = self._dataset_context(dataset)
        raw = self.env["ir.config_parameter"].sudo().get_param(SHAPE_PARAM % ds["name"])
        return json.loads(raw) if raw else False

    @api.model
    def _assert_shape(self, env, ds, requested):
        """Raise unless ``requested`` matches what this dataset was seeded with.

        This is the guard for the defect described in the module docstring: without it, a caller
        asking for 4 partners on a database seeded with 40 got 40, silently.
        """
        recorded = self.get_shape(ds["name"])
        if not recorded:
            return
        conflicts = [
            (key, recorded.get(key), requested[key])
            for key in SHAPE_KEYS
            if recorded.get(key) != requested[key]
        ]
        if not conflicts:
            return
        lines = "\n".join(
            "  - %s: already seeded as %r, you asked for %r" % (key, was, now)
            for key, was, now in conflicts
        )
        raise UserError(
            _(
                "Demo dataset '%(dataset)s' already exists with a different shape, so this call "
                "would have silently returned data you did not ask for.\n\n"
                "%(conflicts)s\n\n"
                "Choose one:\n"
                "  * call generate() again with dataset='<a new name>' to seed this shape "
                "alongside the existing one - datasets never share records; or\n"
                "  * re-run with the shape above if you meant to reuse the existing dataset; or\n"
                "  * start from a fresh database (make init-db) if you want a clean slate.\n\n"
                "There is deliberately no reset: this dataset's posted journal entries and done "
                "stock moves cannot be deleted without bypassing Odoo's audit trail.",
                dataset=ds["name"],
                conflicts=lines,
            )
        )

    # ==================================================================
    # Counting - exact, by external ID, never by name pattern
    # ==================================================================

    @api.model
    def _tracked(self, env, ds, model_name, xmlid_prefix=""):
        """Return the records of ``model_name`` belonging to this dataset.

        ``xmlid_prefix`` narrows to one family of records inside the dataset (``"product_"`` for
        the catalogue, ``"uncostedproduct"`` for the single no-cost fixture). It exists so the
        ``products`` counter keeps reporting the number the ``products`` PARAMETER asked for: a
        counter that silently included an extra unconditional record would make ``summary()``
        disagree with the recorded shape, which is the same "the author's view and the consumer's
        view differ" failure the shape-authority mechanism exists to prevent.
        """
        rows = env["ir.model.data"].search([
            ("module", "=", MODULE),
            ("model", "=", model_name),
            ("name", "=like", ds["prefix"] + xmlid_prefix + "%"),
        ])
        return env[model_name].browse(rows.mapped("res_id")).exists()

    @api.model
    def summary(self, dataset=DEFAULT_DATASET, company=None):
        """Return this dataset's current row counts.

        Counted through ``ir.model.data`` membership, not by matching reference prefixes: a name
        pattern is a heuristic, and it silently mixed datasets and miscounted derived documents.
        """
        env = self.env(su=True)
        ds = self._dataset_context(dataset)
        orders = self._tracked(env, ds, "sale.order")
        invoices = orders.invoice_ids.filtered(lambda move: move.move_type == "out_invoice")
        pickings = orders.picking_ids
        delivery_moves = env["stock.move"].search_count([("picking_id", "in", pickings.ids)]) \
            if pickings else 0
        inventory_moves = len(self._tracked(env, ds, "stock.move"))
        return {
            "dataset": ds["name"],
            "operating_units": len(self._tracked(env, ds, "operating.unit")),
            "partners": len(self._tracked(env, ds, "res.partner")),
            # Narrowed to the catalogue so this stays equal to the `products` parameter. The
            # no-cost fixture product is reported separately, below.
            "products": len(self._tracked(env, ds, "product.template", "product_")),
            # 1 once the dataset has been generated by a release that carries it, 0 for a dataset
            # last touched by an older one. Reported rather than folded into `products` because
            # its whole purpose is to be the ONE product with no standard_price, and a consumer
            # checking that mart_stock_position has an unvalued row needs to know it is there.
            "uncosted_products": len(
                self._tracked(env, ds, "product.template", "uncostedproduct")
            ),
            "billers": len(self._tracked(env, ds, "ppob.biller")),
            "sale_orders": len(orders),
            "sale_order_lines": env["sale.order.line"].search_count(
                [("order_id", "in", orders.ids)]
            ) if orders else 0,
            "invoices": len(invoices),
            "pickings": len(pickings),
            # Split on purpose: the deliveries are what a revenue mart joins, the inventory
            # adjustments are the one-off stock top-up. Reporting only the first understated the
            # table by exactly the number of storable products.
            "stock_moves_delivery": delivery_moves,
            "stock_moves_inventory": inventory_moves,
            "stock_moves": delivery_moves + inventory_moves,
            "pos_orders": len(self._tracked(env, ds, "pos.order")),
            "ppob_transactions": len(self._tracked(env, ds, "ppob.transaction")),
        }

    # ==================================================================
    # Idempotency
    # ==================================================================

    def _ensure(self, env, ds, xmlid, model, values):
        """Return the record registered under ``custom_demo_seed.<dataset>__<xmlid>``.

        This is the whole idempotency mechanism. Do not create a demo record any other way.
        """
        full = ds["prefix"] + xmlid
        existing = env.ref("%s.%s" % (MODULE, full), raise_if_not_found=False)
        if existing:
            return existing
        record = env[model].create(values)
        return self._tag(env, ds, xmlid, record)

    def _exists(self, env, ds, xmlid):
        return env.ref("%s.%s" % (MODULE, ds["prefix"] + xmlid), raise_if_not_found=False)

    def _tag(self, env, ds, xmlid, record):
        """Register an external ID for a record built by a business method.

        Named ``_tag`` and not ``_register`` because ``_register`` is a reserved ``BaseModel``
        class attribute (a bool); shadowing it fails with "'bool' object is not callable".
        """
        env["ir.model.data"].create({
            "module": MODULE,
            "name": ds["prefix"] + xmlid,
            "model": record._name,
            "res_id": record.id,
            "noupdate": True,
        })
        return record

    # ==================================================================
    # Master data
    # ==================================================================

    def _ensure_chart_of_accounts(self, env, company):
        if company.chart_template:
            return
        _logger.info("custom_demo_seed: loading generic_coa into %s", company.display_name)
        env["account.chart.template"].try_loading("generic_coa", company=company)

    def _ensure_operating_units(self, env, ds, company, count):
        units = env["operating.unit"].browse()
        for suffix, name in OPERATING_UNITS[:count]:
            code = "OU-DEMO-%s%s" % (ds["tag"], suffix)
            # The external ID is derived from the FINAL code, which is what the pre-dataset
            # release did. Deriving it from `suffix` instead would not match the migrated legacy
            # IDs, and _ensure would try to create a second unit with the same code.
            units |= self._ensure(env, ds, "ou_%s" % code.lower().replace("-", "_"),
                                  "operating.unit", {
                "name": "%s (Demo)" % name,
                "code": code,
                "company_id": company.id,
            })
        return units

    def _ensure_partners(self, env, ds, company, count, rng):
        """Create obviously-synthetic customers.

        Every value is unmistakably fake:

        * the display name carries a ``(Demo NNN)`` suffix;
        * e-mail uses ``@contoh.invalid`` - ``.invalid`` is reserved by RFC 2606 and can never
          resolve;
        * phone numbers use ``+62-800-555-NNNN``; ``800`` is not an assignable Indonesian mobile
          prefix and ``555`` is the long-standing fiction convention;
        * ``ref`` is ``DEMO-C-NNNN``.

        ``vat`` is deliberately left EMPTY. Odoo's ``base_vat`` validates the Indonesian NPWP
        checksum, so a value that survived validation would by definition be checksum-valid - i.e.
        it would look exactly like a real NPWP. That is the "invents data resembling a real
        person's identifiers" failure the brief forbids.
        """
        country = env.ref("base.id", raise_if_not_found=False)  # Indonesia
        partners = env["res.partner"].browse()
        for index in range(1, count + 1):
            given = GIVEN_NAMES[(index - 1) % len(GIVEN_NAMES)]
            family = FAMILY_NAMES[(index - 1) // len(GIVEN_NAMES) % len(FAMILY_NAMES)]
            values = {
                "name": "%s %s (Demo %03d)" % (given, family, index),
                "ref": "DEMO-%sC-%04d" % (ds["tag"], index),
                "email": "%s.%s.%s%03d@contoh.invalid" % (
                    given.lower(), family.lower(), ds["tag_lower"], index,
                ),
                "phone": "+62-800-555-%04d" % index,
                "street": "Jl. Contoh Demo No. %d" % (index % 200 + 1),
                "city": CITIES[index % len(CITIES)],
                "zip": "%05d" % (10000 + index),
                "is_company": False,
                "customer_rank": 1,
                "comment": "Data contoh untuk pengujian. Bukan orang sungguhan.",
            }
            if country:
                values["country_id"] = country.id
            partners |= self._ensure(env, ds, "partner_%04d" % index, "res.partner", values)
        return partners

    def _ensure_products(self, env, ds, company, count):
        products = env["product.product"].browse()
        for index, (code, name, price, cost, is_service) in enumerate(PRODUCTS[:count], start=1):
            template = self._ensure(env, ds, "product_%02d" % index, "product.template", {
                "name": name,
                "default_code": "DEMO-%s%s" % (ds["tag"], code),
                "list_price": price,
                "standard_price": cost,
                "type": "service" if is_service else "consu",
                "is_storable": not is_service,
                "sale_ok": True,
                "purchase_ok": not is_service,
                "available_in_pos": not is_service,
                "invoice_policy": "order",
                "company_id": False,
            })
            products |= template.product_variant_id
        return products

    def _ensure_billers(self, env, ds, company):
        billers = env["ppob.biller"].browse()
        for suffix, name, category, sla in BILLERS:
            code = "DEMO-%s%s" % (ds["tag"], suffix)
            billers |= self._ensure(
                env, ds, "biller_%s" % code.lower().replace("-", "_"), "ppob.biller", {
                    "name": name,
                    "code": code,
                    "category": category,
                    "sla_target_seconds": sla,
                    "company_id": company.id,
                }
            )
        return billers

    def _ensure_demo_users(self, env, ds, units):
        """Two internal users, each entitled to exactly one Operating Unit.

        They exist so the cross-unit isolation of ``custom_operating_unit`` can be demonstrated by
        logging in, not only by a unit test. Passwords are NOT set: the accounts cannot be logged
        into until an administrator sets one, which keeps a demo-seeded database from shipping a
        known credential.
        """
        group_ids = [
            env.ref("base.group_user").id,
            env.ref("custom_ppob.group_ppob_user").id,
        ]
        for index, unit in enumerate(units[:2], start=1):
            self._ensure(env, ds, "user_ou_%d" % index, "res.users", {
                "name": "Petugas %s (Demo)" % unit.name,
                "login": "demo.%sou%d@contoh.invalid" % (ds["tag_lower"], index),
                "group_ids": [(6, 0, group_ids)],
                "allowed_operating_unit_ids": [(6, 0, unit.ids)],
                "default_operating_unit_id": unit.id,
            })

    def _ensure_stock(self, env, ds, company, products):
        """Put stock on hand, so deliveries validate without going negative.

        Uses the inventory-adjustment flow rather than writing quants directly, so the resulting
        `stock.move` rows are the same shape a real adjustment produces. The moves it creates are
        tagged, so `summary` can report them instead of quietly omitting them.
        """
        if self._exists(env, ds, "stock_seeded"):
            # Back-fill: a dataset seeded by the pre-dataset release has the marker but no tagged
            # moves, so `summary` would report stock_moves_inventory as 0 - a misleading number
            # rather than a missing one. Tag them once, from the products this dataset owns.
            # Narrowed to the catalogue top-up's own xmlid family. Unnarrowed, the presence of
            # the uncosted product's single move would make this look "already tagged" and the
            # legacy moves would stay untagged forever.
            if not self._tracked(env, ds, "stock.move", "stockmove_"):
                existing = env["stock.move"].search([
                    ("is_inventory", "=", True),
                    ("product_id", "in", products.ids),
                    ("company_id", "=", company.id),
                ], order="id")
                for position, move in enumerate(existing, start=1):
                    self._tag(env, ds, "stockmove_%03d" % position, move)
                if existing:
                    _logger.info(
                        "custom_demo_seed: back-tagged %d pre-existing inventory move(s) "
                        "for dataset '%s'", len(existing), ds["name"],
                    )
            return
        warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            return
        storable = products.filtered(lambda p: p.is_storable)
        if not storable:
            return
        env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM stock_move")
        highest_before = env.cr.fetchone()[0]
        quants = env["stock.quant"].with_context(inventory_mode=True).create([
            {
                "product_id": product.id,
                "location_id": warehouse.lot_stock_id.id,
                "inventory_quantity": 100000.0,
            }
            for product in storable
        ])
        quants.action_apply_inventory()
        env.cr.execute("SELECT id FROM stock_move WHERE id > %s ORDER BY id", (highest_before,))
        for position, (move_id,) in enumerate(env.cr.fetchall(), start=1):
            self._tag(env, ds, "stockmove_%03d" % position, env["stock.move"].browse(move_id))
        self._ensure(env, ds, "stock_seeded", "ir.config_parameter", {
            "key": "custom_demo_seed.%s.stock_seeded" % ds["name"],
            "value": fields.Datetime.to_string(fields.Datetime.now()),
        })

    def _ensure_uncosted_product(self, env, ds, company):
        """Seed ONE storable product with stock and no cost, and prove it stayed uncosted.

        Requested by the Data Warehouse agent. ``mart_stock_position`` carries a
        ``has_unit_cost`` flag whose false branch had never executed against real data: every
        product without a ``standard_price`` in this fixture was non-storable, so it produced no
        stock move and never reached a position row. See ``UNCOSTED_PRODUCT_CODE`` above for why
        each property of the record is load-bearing.

        Deliberately NOT gated on the ``stock_seeded`` marker that ``_ensure_stock`` uses. That
        marker means "the catalogue top-up has run", and reusing it would mean a dataset seeded
        before this record existed - which is every dataset on this host - never gains it. A
        record that exists in the author's tree and not in the consumer's database is the defect
        this build has hit five times; a separate marker is what makes a re-run actually produce
        the product instead of skipping it.
        """
        first_time = not self._exists(env, ds, "uncostedproduct")
        template = self._ensure(env, ds, "uncostedproduct", "product.template", {
            "name": UNCOSTED_PRODUCT_NAME,
            "default_code": "DEMO-%s%s" % (ds["tag"], UNCOSTED_PRODUCT_CODE),
            # NOT a rounding of a real price: this item is deliberately unvalued, and a sales
            # price on it would invite coalesce(unit_cost, list_price) downstream.
            "list_price": 0.0,
            # standard_price is ABSENT on purpose. Writing 0.0 would materialise {"<company>": 0.0}
            # in the company_dependent jsonb map and dim_product_cost would emit a row for it.
            "type": "consu",
            "is_storable": True,
            "sale_ok": False,
            "purchase_ok": False,
            "available_in_pos": False,
            "invoice_policy": "order",
            "company_id": False,
        })
        variant = template.product_variant_id

        # POSTCONDITION, checked rather than assumed. If a future Odoo release, or a product
        # category configured for automated/AVCO valuation, materialises a cost for this product,
        # dim_product_cost gains a row, the LEFT join matches, has_unit_cost comes back TRUE and
        # this fixture silently stops exercising the branch - while every count still looks right.
        # That is exactly the "check that cannot fail" shape, so it is checked, repaired and
        # logged loudly rather than trusted.
        env.flush_all()
        env.cr.execute("SELECT standard_price FROM product_product WHERE id = %s", (variant.id,))
        stored = env.cr.fetchone()[0] or {}
        if str(company.id) in stored:
            env.cr.execute(
                "UPDATE product_product SET standard_price = standard_price - %s WHERE id = %s",
                (str(company.id), variant.id),
            )
            env.invalidate_all()
            _logger.warning(
                "custom_demo_seed: %s had a standard_price of %s materialised for company %s and "
                "it has been removed. This product must have NO cost or DWH's has_unit_cost=false "
                "branch stops being exercised. Check the product category's cost method.",
                template.default_code, stored.get(str(company.id)), company.id,
            )

        if self._exists(env, ds, "uncostedstockseeded"):
            return template

        warehouse = env["stock.warehouse"].search([("company_id", "=", company.id)], limit=1)
        if not warehouse:
            _logger.warning(
                "custom_demo_seed: no stock.warehouse for %s, so %s has no stock move and DWH's "
                "has_unit_cost=false branch is NOT exercised by this dataset.",
                company.display_name, template.default_code,
            )
            return template

        env.cr.execute("SELECT COALESCE(MAX(id), 0) FROM stock_move")
        highest_before = env.cr.fetchone()[0]
        quant = env["stock.quant"].with_context(inventory_mode=True).create({
            "product_id": variant.id,
            "location_id": warehouse.lot_stock_id.id,
            "inventory_quantity": UNCOSTED_PRODUCT_QTY,
        })
        quant.action_apply_inventory()
        env.cr.execute("SELECT id FROM stock_move WHERE id > %s ORDER BY id", (highest_before,))
        move_ids = [row[0] for row in env.cr.fetchall()]
        for position, move_id in enumerate(move_ids, start=1):
            self._tag(env, ds, "uncostedstockmove_%03d" % position,
                      env["stock.move"].browse(move_id))
        if not move_ids:
            # An inventory adjustment that produced no move means the branch is still unreachable.
            # Say so; a silent zero here is indistinguishable from success.
            raise UserError(_(
                "The no-cost demo product %(code)s produced no stock move, so "
                "mart_stock_position will never emit a has_unit_cost=false row for it. "
                "Refusing to record the marker: a fixture that looks seeded but exercises "
                "nothing is worse than one that failed.",
                code=template.default_code,
            ))
        self._ensure(env, ds, "uncostedstockseeded", "ir.config_parameter", {
            "key": "custom_demo_seed.%s.uncosted_stock_seeded" % ds["name"],
            "value": fields.Datetime.to_string(fields.Datetime.now()),
        })
        # Instance 12's rule: a mechanism that reconciles two copies of state must report what it
        # changed on an existing one, or "no output" is indistinguishable from "nothing diverged".
        _logger.info(
            "custom_demo_seed: %s the no-cost product %s (%s units, %d inventory move(s)) in "
            "dataset '%s'. mart_stock_position should now report at least one row with "
            "has_unit_cost = false.",
            "seeded" if first_time else "ADDED TO THE PRE-EXISTING dataset:",
            template.default_code, UNCOSTED_PRODUCT_QTY, len(move_ids), ds["name"],
        )
        return template

    def _ensure_pos_configs(self, env, ds, company, units):
        """One point of sale per Operating Unit, each with one long-lived open session.

        Odoo allows only one open ``pos.session`` per ``pos.config`` at a time, and closing a
        session posts accounting entries - doing that 12 times per unit would make the fixture slow
        and fragile for no analytic benefit. So the fixture opens one session per unit and spreads
        ``pos.order.date_order`` across the 12 months instead. The warehouse reads ``date_order``,
        which is faithful; what it does not reproduce is realistic session boundaries. Recorded in
        MODULE_KNOWLEDGE.md.
        """
        # Do NOT force payment_method_ids. A cash pos.payment.method may belong to exactly one
        # pos.config ("This cash payment method is already used in another Point of Sale"), so
        # assigning the company's single cash method to every unit's till raises on the second
        # one. Odoo's own pos.config.create already picks the available methods and skips a cash
        # method that is taken, which is precisely the behaviour wanted here.
        configs = {}
        for unit in units:
            key = unit.code.lower().replace("-", "_")
            values = {
                "name": "Kasir %s" % unit.name,
                "company_id": company.id,
            }
            config = self._ensure(env, ds, "pos_config_%s" % key, "pos.config", values)
            session = self._exists(env, ds, "pos_session_%s" % key)
            if not session:
                session = env["pos.session"].search(
                    [("config_id", "=", config.id), ("state", "!=", "closed")], limit=1
                )
                if not session:
                    session = env["pos.session"].create({
                        "config_id": config.id,
                        "user_id": env.uid,
                    })
                self._tag(env, ds, "pos_session_%s" % key, session)
            if session.state == "opening_control":
                session.set_opening_control(0, None)
            configs[unit.id] = (config, session)
        return configs

    # ==================================================================
    # Monthly volume
    # ==================================================================

    def _month_datetime(self, month_start, rng, index):
        """Spread a record across the month, in business hours, deterministically."""
        last_day = (month_start + relativedelta(months=1)) - timedelta(days=1)
        span = (last_day - month_start).days
        day = month_start + timedelta(days=rng.randint(0, max(span, 0)))
        if day > date.today():
            day = date.today()
        moment = time(hour=rng.randint(8, 19), minute=rng.randint(0, 59), second=rng.randint(0, 59))
        return datetime.combine(day, moment)

    def _seed_sale_orders(self, env, ds, company, month_start, offset, units, partners, products,
                          count, rng):
        sellable = products.filtered(lambda p: p.sale_ok)
        if not sellable or not partners:
            return
        for index in range(1, count + 1):
            xmlid = "so_%s_%02d" % (month_start.strftime("%Y%m"), index)
            if self._exists(env, ds, xmlid):
                continue
            when = self._month_datetime(month_start, rng, index)
            unit = units[rng.randrange(len(units))]
            partner = partners[rng.randrange(len(partners))]
            lines = []
            for _line in range(rng.randint(1, 4)):
                product = sellable[rng.randrange(len(sellable))]
                lines.append((0, 0, {
                    "product_id": product.id,
                    "product_uom_qty": rng.randint(1, 12),
                }))
            order = env["sale.order"].create({
                "partner_id": partner.id,
                "company_id": company.id,
                "operating_unit_id": unit.id,
                "date_order": when,
                "order_line": lines,
            })
            self._tag(env, ds, xmlid, order)
            order.action_confirm()
            # action_confirm resets date_order to now for a draft->sale transition in some flows;
            # pin it back so the 12-month spread survives.
            order.write({"date_order": when})
            self._deliver(env, order, when)
            self._invoice(env, order, when)

    def _deliver(self, env, order, when):
        for picking in order.picking_ids:
            if picking.state in ("done", "cancel"):
                continue
            picking.action_assign()
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking._action_done()
            picking.write({"date_done": when, "scheduled_date": when})
            picking.move_ids.write({"date": when})

    def _invoice(self, env, order, when):
        if order.invoice_status != "to invoice":
            return
        moves = order._create_invoices()
        if not moves:
            return
        invoice_date = when.date()
        moves.write({"invoice_date": invoice_date, "date": invoice_date})
        moves.action_post()

    def _seed_ppob(self, env, ds, company, month_start, offset, units, partners, products,
                   billers, count, rng):
        if not billers:
            return
        Transaction = env["ppob.transaction"]
        for index in range(1, count + 1):
            xmlid = "ppob_%s_%03d" % (month_start.strftime("%Y%m"), index)
            if self._exists(env, ds, xmlid):
                continue
            when = self._month_datetime(month_start, rng, index)
            unit = units[rng.randrange(len(units))]
            biller = billers[rng.randrange(len(billers))]
            partner = partners[rng.randrange(len(partners))] if rng.random() < 0.7 else None
            amount = float(rng.choice([20000, 50000, 100000, 150000, 200000, 350000, 500000]))
            admin_fee = float(rng.choice([1500, 2000, 2500, 3000]))
            commission = float(rng.choice([500, 750, 1000, 1250]))
            txn = Transaction.create({
                "biller_id": biller.id,
                "partner_id": partner.id if partner else False,
                "operating_unit_id": unit.id,
                "company_id": company.id,
                # "DEMO-" prefix makes the subscriber number unmistakably synthetic.
                "customer_ref": "DEMO-%011d" % (index + offset * 1000 + biller.id * 100000),
                "customer_name": partner.name if partner else "Pelanggan Tunai (Demo)",
                "amount": amount,
                "admin_fee": admin_fee,
                "commission": min(commission, admin_fee),
                "requested_at": when,
            })
            self._tag(env, ds, xmlid, txn)
            txn.action_submit()
            roll = rng.random()
            latency = rng.randint(3, int(biller.sla_target_seconds * 2.5) or 60)
            settled = when + timedelta(seconds=latency)
            if roll < 0.92:
                txn.action_succeed(
                    biller_reference="DEMO-REF-%s-%05d" % (month_start.strftime("%Y%m"), index),
                    settled_at=settled,
                )
                if rng.random() < 0.02:
                    txn.action_reverse(reason="Pembatalan atas permintaan pelanggan (demo)")
            else:
                txn.action_fail(
                    reason=rng.choice(FAILURE_REASONS),
                    settled_at=settled,
                )

    def _seed_pos(self, env, ds, company, month_start, offset, units, partners, products,
                  pos_configs, count, rng):
        sellable = products.filtered(lambda p: p.available_in_pos)
        if not sellable or not pos_configs:
            return
        for index in range(1, count + 1):
            xmlid = "pos_%s_%03d" % (month_start.strftime("%Y%m"), index)
            if self._exists(env, ds, xmlid):
                continue
            unit = units[rng.randrange(len(units))]
            entry = pos_configs.get(unit.id)
            if not entry:
                continue
            config, session = entry
            payment_method = config.payment_method_ids[:1]
            when = self._month_datetime(month_start, rng, index)
            partner = partners[rng.randrange(len(partners))] if rng.random() < 0.4 else None
            lines = []
            total = 0.0
            for _line in range(rng.randint(1, 3)):
                product = sellable[rng.randrange(len(sellable))]
                qty = rng.randint(1, 4)
                price = product.list_price
                subtotal = price * qty
                total += subtotal
                lines.append((0, 0, {
                    "product_id": product.id,
                    "qty": qty,
                    "price_unit": price,
                    "price_subtotal": subtotal,
                    "price_subtotal_incl": subtotal,
                    "full_product_name": product.display_name,
                    "tax_ids": [(6, 0, [])],
                }))
            order = env["pos.order"].create({
                "session_id": session.id,
                "company_id": company.id,
                "operating_unit_id": unit.id,
                "partner_id": partner.id if partner else False,
                "date_order": when,
                "amount_total": total,
                "amount_tax": 0.0,
                "amount_paid": 0.0,
                "amount_return": 0.0,
                "lines": lines,
            })
            self._tag(env, ds, xmlid, order)
            if payment_method:
                order.add_payment({
                    "pos_order_id": order.id,
                    "amount": total,
                    "payment_date": fields.Datetime.to_string(when),
                    "payment_method_id": payment_method.id,
                })
                order.action_pos_order_paid()
            order.write({"date_order": when})
