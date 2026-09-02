# -*- coding: utf-8 -*-
from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestQualityWorkflow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create({"name": "QC Product"})
        cls.point = cls.env["quality.point"].create(
            {
                "name": "Visual inspection",
                "product_id": cls.product.id,
                "check_kind": "pass_fail",
                "frequency": "every",
                "operation": "incoming",
            }
        )
        cls.test = cls.env["custom.quality.test"].create(
            {
                "name": "Visual test",
                "code": "VIS-1",
                "test_type": "visual",
                "line_ids": [
                    (0, 0, {"name": "Surface free of scratches", "response_type": "boolean", "is_required": True}),
                    (
                        0,
                        0,
                        {
                            "name": "Length (cm)",
                            "response_type": "number",
                            "is_required": True,
                            "expected_min": 9.5,
                            "expected_max": 10.5,
                        },
                    ),
                ],
            }
        )

    def test_apply_template_seeds_lines(self):
        check = self.env["quality.check"].create({"point_id": self.point.id})
        self.test.apply_to_check(check)
        self.assertEqual(len(check.inspection_line_ids), 2)
        self.assertEqual(check.overall_result, "fail")  # answers empty

    def test_pass_fail_compute_numeric(self):
        check = self.env["quality.check"].create({"point_id": self.point.id})
        self.test.apply_to_check(check)
        num_line = check.inspection_line_ids.filtered(lambda l: l.response_type == "number")
        num_line.actual_value = "10"
        check.inspection_line_ids.filtered(lambda l: l.response_type == "boolean").actual_value = "yes"
        check.invalidate_recordset(["overall_result"])
        self.assertEqual(check.overall_result, "pass")

    def test_fail_triggers_alert_and_capa_cascade(self):
        check = self.env["quality.check"].create({"point_id": self.point.id, "note": "scratched"})
        check.action_fail()
        self.assertTrue(check.alert_id)
        alert = check.alert_id
        capa = self.env["custom.quality.capa"].create(
            {
                "alert_id": alert.id,
                "action_type": "corrective",
                "description": "Rework",
                "responsible_id": self.env.user.id,
            }
        )
        capa.action_start()
        self.assertEqual(capa.state, "in_progress")
        # Move alert into investigating state to allow resolve cascade
        alert.action_investigate()
        capa.action_done()
        self.assertEqual(capa.state, "done")
        self.assertEqual(alert.state, "resolved")

    def test_signature_hash_tamper_evident(self):
        check = self.env["quality.check"].create({"point_id": self.point.id})
        sig = self.env["custom.quality.signature"].create(
            {
                "check_id": check.id,
                "purpose": "operator",
            }
        )
        self.assertTrue(sig.hash)
        self.assertTrue(sig.is_valid)
        from odoo.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            sig.purpose = "supervisor"
