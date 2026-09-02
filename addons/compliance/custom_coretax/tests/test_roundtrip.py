# -*- coding: utf-8 -*-
"""Round-trip and constraint tests for custom_coretax.

These tests cover:

1. ``test_export_efaktur_keluaran_roundtrip`` — seed an out_invoice with
   a partner that has an NPWP, run the export wizard for
   ``efaktur_keluaran``, assert XML is generated, parses with lxml, and
   (if a non-placeholder XSD exists) validates with ``xmlschema``.
   Then feed the generated XML back into the import wizard for
   ``faktur_masukan`` and assert it either updates an account.move or
   logs a clear "no matching invoice" line — round-trip exercises
   schema fidelity, not business semantics.

2. ``test_nsfp_constraint`` — ``'12345'`` is rejected, a 17-digit value
   is accepted.

3. ``test_audit_log_inserted_on_export`` — counts ``pdp.audit_log``
   rows before and after an export and asserts +1 with
   ``action='xml_export'``. ``pdp.audit_log`` is a SQL view, so we go
   through ``cr.execute`` rather than the ORM.

The whole module is decorated ``@tagged('post_install', '-at_install')``
because the wizards depend on ``account`` and ``custom_core`` being
fully loaded.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import date

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

_logger = logging.getLogger(__name__)

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None

try:
    import xmlschema
except ImportError:  # pragma: no cover
    xmlschema = None


# Marker substring written into every placeholder XSD by fetch_xsd.sh /
# SOURCES.md bootstrap. Real DJP schemas will not contain this token.
_PLACEHOLDER_TOKEN = b"PLACEHOLDER XSD"


@tagged("post_install", "-at_install")
class TestCoretaxRoundtrip(TransactionCase):
    """Export -> validate -> re-import smoke test for Coretax XML."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.Move = cls.env["account.move"]
        cls.Partner = cls.env["res.partner"]
        cls.ExportWizard = cls.env["custom.coretax.export.wizard"]
        cls.ImportWizard = cls.env["custom.coretax.import.wizard"]
        cls.Config = cls.env["custom.coretax.config"]

        # Make sure an active config exists (data file should have done it,
        # but tests should not depend on data load order).
        cls.config = cls.Config.search([("active", "=", True)], limit=1)
        if not cls.config:
            cls.config = cls.Config.create(
                {
                    "name": "Test Coretax Config",
                    "active": True,
                    "npwp": "0123456789012345",
                    "taxpayer_name": "PT Test Coretax",
                    "kpp_code": "001",
                    "adapter_type": "manual",
                }
            )

        # Counterparty with a 16-digit NIK-format NPWP (post-2024).
        cls.partner = cls.Partner.create(
            {
                "name": "PT Roundtrip Counterparty",
                "vat": "9988776655443322",
            }
        )

        # Seed an out_invoice in the current year with PPN.
        company = cls.env.company
        idr = cls.env.ref("base.IDR", raise_if_not_found=False) or company.currency_id
        if idr and not idr.active:
            idr.sudo().active = True

        product = cls.env["product.product"].search([], limit=1)
        if not product:
            product = cls.env["product.product"].create(
                {
                    "name": "Roundtrip Test Product",
                    "type": "service",
                    "list_price": 1_000_000.0,
                }
            )

        # Use whichever 11% sale tax is available, fall back to creating one.
        ppn = cls.env["account.tax"].search(
            [("type_tax_use", "=", "sale"), ("amount", "=", 11.0), ("company_id", "=", company.id)],
            limit=1,
        )
        if not ppn:
            ppn = cls.env["account.tax"].create(
                {
                    "name": "PPN 11% (test)",
                    "type_tax_use": "sale",
                    "amount": 11.0,
                    "amount_type": "percent",
                    "company_id": company.id,
                }
            )

        cls.invoice = cls.Move.create(
            {
                "move_type": "out_invoice",
                "partner_id": cls.partner.id,
                "invoice_date": date(date.today().year, max(1, date.today().month), 1),
                "currency_id": idr.id,
                "invoice_line_ids": [
                    (
                        0,
                        0,
                        {
                            "product_id": product.id,
                            "name": "Roundtrip line",
                            "quantity": 1,
                            "price_unit": 1_000_000.0,
                            "tax_ids": [(6, 0, [ppn.id])],
                        },
                    )
                ],
            }
        )
        cls.invoice.action_post()

    # ------------------------------------------------------------------
    # 1. Round-trip
    # ------------------------------------------------------------------
    def test_export_efaktur_keluaran_roundtrip(self):
        if etree is None:
            self.skipTest("lxml not installed")

        today = date.today()
        wiz = self.ExportWizard.create(
            {
                "config_id": self.config.id,
                "document_type": "efaktur_keluaran",
                "period_year_from": today.year,
                "period_month_from": 1,
                "period_year_to": today.year,
                "period_month_to": 12,
            }
        )
        wiz.action_generate_xml()

        self.assertTrue(wiz.xml_file, "Export wizard did not produce an XML payload.")
        self.assertGreaterEqual(wiz.record_count, 1, "Export should pick up the seeded invoice.")

        xml_bytes = base64.b64decode(wiz.xml_file)

        # (a) parses with lxml
        root = etree.fromstring(xml_bytes)
        self.assertEqual(etree.QName(root).localname, "CoretaxDocument")

        # (b) validate against XSD only if the bundled schema is real.
        module_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        xsd_path = os.path.join(module_root, "data", "xsd", "efaktur_keluaran.xsd")
        if xmlschema is None:
            _logger.warning("xmlschema not installed — skipping XSD validation")
        elif not os.path.isfile(xsd_path):
            _logger.warning("no XSD at %s — skipping validation", xsd_path)
        else:
            with open(xsd_path, "rb") as fh:
                xsd_bytes = fh.read()
            if _PLACEHOLDER_TOKEN in xsd_bytes:
                _logger.warning("efaktur_keluaran.xsd is a placeholder — skipping strict validation")
            else:
                schema = xmlschema.XMLSchema(xsd_path)
                # ``validate`` raises on failure; we want a hard assertion.
                schema.validate(xml_bytes)

        # (c) round-trip: re-import as faktur_masukan. The export schema
        #     and the inbound NSFP-update path share the <Faktur> shape,
        #     so this exercises tag fidelity end-to-end.
        imp = self.ImportWizard.create(
            {
                "document_type": "faktur_masukan",
                "xml_filename": wiz.xml_filename,
                "xml_file": wiz.xml_file,
                "source": "received",
            }
        )
        imp.action_import()

        # Either the exported invoice is matched by name and updated, OR
        # it is reported as unmatched in the log — both outcomes are
        # acceptable for a schema-fidelity smoke test.
        log = imp.log or ""
        matched = (imp.created_count or 0) > 0
        unmatched_logged = "no matching invoice" in log.lower()
        self.assertTrue(
            matched or unmatched_logged,
            "Re-import produced neither a match nor a clear unmatched-row log; log was: %r" % log,
        )

    # ------------------------------------------------------------------
    # 2. NSFP constraint
    # ------------------------------------------------------------------
    def test_nsfp_constraint(self):
        # Invalid: 5 digits.
        with self.assertRaises(ValidationError):
            self.invoice.write({"x_custom_nsfp": "12345"})

        # Valid: exactly 17 digits.
        self.invoice.write({"x_custom_nsfp": "01000123456789012"})
        self.assertEqual(self.invoice.x_custom_nsfp, "01000123456789012")

    # ------------------------------------------------------------------
    # 3. Audit log row inserted on export
    # ------------------------------------------------------------------
    def test_audit_log_inserted_on_export(self):
        cr = self.env.cr

        # ``pdp.audit_log`` ships as a SQL view in custom_core. If it is
        # missing in this build, the contract isn't observable here — skip.
        cr.execute(
            """
            SELECT 1
              FROM information_schema.tables
             WHERE table_schema = 'pdp'
               AND table_name = 'audit_log'
            """
        )
        if not cr.fetchone():
            self.skipTest("pdp.audit_log view not present in this database")

        cr.execute("SELECT count(*) FROM pdp.audit_log WHERE action = 'xml_export'")
        before = cr.fetchone()[0]

        today = date.today()
        wiz = self.ExportWizard.create(
            {
                "config_id": self.config.id,
                "document_type": "efaktur_keluaran",
                "period_year_from": today.year,
                "period_month_from": 1,
                "period_year_to": today.year,
                "period_month_to": 12,
            }
        )
        wiz.action_generate_xml()

        cr.execute("SELECT count(*) FROM pdp.audit_log WHERE action = 'xml_export'")
        after = cr.fetchone()[0]

        self.assertEqual(
            after - before,
            1,
            "Expected exactly one new pdp.audit_log row with action='xml_export' "
            "after running the export wizard (got delta=%d)." % (after - before),
        )
