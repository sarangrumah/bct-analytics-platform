# -*- coding: utf-8 -*-
from unittest.mock import patch

from odoo.tests.common import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestLabelPrint(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {
                "name": "Widget",
                "default_code": "WDG-1",
            }
        )
        cls.template = cls.env["custom.label.template"].create(
            {
                "name": "ZPL Widget",
                "output_mode": "zpl",
                "template_source": (
                    "^XA\n^FO20,20^A0N,30,30^FD{{display_name}}^FS\n^FO20,60^BCN,80,Y,N,N^FD{{default_code}}^FS\n^XZ"
                ),
                "applies_to": cls.env.ref("product.model_product_product").id,
            }
        )
        cls.printer = cls.env["custom.printer.config"].create(
            {
                "name": "Fake Zebra",
                "printer_type": "zebra_network",
                "host": "127.0.0.1",
                "port": 9100,
                "label_template_id": cls.template.id,
            }
        )

    def test_render_substitutes_placeholders(self):
        payload = self.template.render(self.product, qty=1).decode("utf-8")
        self.assertIn("Widget", payload)
        self.assertIn("WDG-1", payload)
        self.assertNotIn("{{", payload)

    def test_render_qty_repeats_body(self):
        payload = self.template.render(self.product, qty=3).decode("utf-8")
        self.assertEqual(payload.count("^XA"), 3)

    def test_queue_process_done(self):
        job = self.env["custom.print.queue"].create(
            {
                "name": "Test Job",
                "label_template_id": self.template.id,
                "printer_id": self.printer.id,
                "res_model": "product.product",
                "res_ids": str(self.product.id),
                "copies": 1,
            }
        )
        with patch.object(type(self.printer), "send_raw", return_value=True) as send:
            job.action_process()
            send.assert_called_once()
        self.assertEqual(job.state, "done")
        self.assertTrue(job.sent_at)

    def test_queue_process_failed_on_send_error(self):
        job = self.env["custom.print.queue"].create(
            {
                "name": "Test Job",
                "label_template_id": self.template.id,
                "printer_id": self.printer.id,
                "res_model": "product.product",
                "res_ids": str(self.product.id),
            }
        )
        with patch.object(type(self.printer), "send_raw", side_effect=RuntimeError("boom")):
            job.action_process()
        self.assertEqual(job.state, "failed")
        self.assertIn("boom", job.error or "")

    def test_cron_picks_only_queued(self):
        done_job = self.env["custom.print.queue"].create(
            {
                "name": "already done",
                "label_template_id": self.template.id,
                "printer_id": self.printer.id,
                "res_model": "product.product",
                "res_ids": str(self.product.id),
                "state": "done",
            }
        )
        queued = self.env["custom.print.queue"].create(
            {
                "name": "queued",
                "label_template_id": self.template.id,
                "printer_id": self.printer.id,
                "res_model": "product.product",
                "res_ids": str(self.product.id),
            }
        )
        with patch.object(type(self.printer), "send_raw", return_value=True):
            self.env["custom.print.queue"]._cron_process_queue()
        self.assertEqual(queued.state, "done")
        self.assertEqual(done_job.state, "done")
