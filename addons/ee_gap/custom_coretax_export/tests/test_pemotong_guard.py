# -*- coding: utf-8 -*-
"""The pemotong identity guard is scoped to what each layout actually carries.

Only the three bupot layouts have an "NPWP Penandatangan" column. e-Faktur
Keluaran (FK/OF) and Retur Masukan do not, so requiring a signer there blocked a
valid export on a field the file never holds.
"""

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.exceptions import ValidationError
from odoo.tests import tagged


@tagged("post_install", "-at_install", "custom_coretax_export")
class TestPemotongGuard(AccountTestInvoicingCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data["company"]
        cls.company.partner_id.x_custom_npwp = "0012345678901000"
        cls.company.x_custom_nitku_suffix = "000000"
        cls.company.x_custom_npwp_penandatangan = False  # the pending field

    def _wizard(self, template):
        return (
            self.env["custom.coretax.template.export.wizard"]
            .sudo()
            .create(
                {
                    "template": template,
                    "masa_pajak": "07",
                    "tahun_pajak": 2026,
                    "company_id": self.company.id,
                }
            )
        )

    # --- layouts without a signer column: must not be blocked --------------
    def test_fk_does_not_need_signer(self):
        npwp, nitku, signer, _user = self._wizard("fk")._pemotong()
        self.assertEqual(npwp, "0012345678901000")
        self.assertEqual(nitku, "000000")
        self.assertEqual(signer, "")  # absent, and that is fine here

    def test_retur_does_not_need_signer(self):
        self.assertEqual(
            self.company._check_coretax_pemotong(require_signer=False),
            "0012345678901000",
        )

    # --- bupot layouts: signer still mandatory -----------------------------
    def test_bupot_templates_still_require_signer(self):
        for template in ("bppu", "bp21", "bpnr"):
            with self.subTest(template=template), self.assertRaises(ValidationError):
                self._wizard(template)._pemotong()

    def test_default_is_still_strict(self):
        # An un-migrated caller that passes nothing keeps the old behaviour.
        with self.assertRaises(ValidationError):
            self.company._check_coretax_pemotong()

    # --- NPWP and NITKU remain mandatory everywhere ------------------------
    def test_npwp_still_required_for_fk(self):
        self.company.partner_id.x_custom_npwp = False
        with self.assertRaises(ValidationError):
            self._wizard("fk")._pemotong()

    def test_nitku_still_required_for_fk(self):
        self.company.x_custom_nitku_suffix = False
        with self.assertRaises(ValidationError):
            self._wizard("fk")._pemotong()
