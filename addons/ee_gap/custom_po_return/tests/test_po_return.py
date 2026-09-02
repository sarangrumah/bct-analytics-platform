# -*- coding: utf-8 -*-
from odoo.addons.purchase_stock.tests.common import PurchaseTestCommon
from odoo.exceptions import UserError
from odoo.tests import tagged


@tagged("post_install", "-at_install")
class TestPoReturn(PurchaseTestCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.product"].create(
            {
                "name": "RTV Product",
                "type": "consu",
                "is_storable": True,
                "purchase_ok": True,
                "purchase_method": "receive",
            }
        )

    def _make_received_po(self, qty, price):
        return self._create_purchase(self.product, qty, price, receive=True)

    def _validate_pickings(self, pickings):
        """Validate return pickings manually (they are now created as draft)."""
        for picking in pickings:
            for move in picking.move_ids:
                move.quantity = move.product_uom_qty
                move.picked = True
            picking.with_context(
                skip_backorder=True,
                picking_ids_not_to_backorder=picking.ids,
            ).button_validate()

    def _make_return(self, lines, partner=None):
        return self.env["custom.po.return"].create(
            {
                "partner_id": (partner or self.vendor).id,
                "line_ids": [(0, 0, {"product_id": product.id, "qty_to_return": qty}) for product, qty in lines],
            }
        )

    def _setup_example_scenario(self):
        po1 = self._make_received_po(20, 100000)
        po2 = self._make_received_po(30, 120000)
        po3 = self._make_received_po(10, 110000)
        bill1 = self._create_bill(purchase_order=po1)
        bill2 = self._create_bill(purchase_order=po2)
        return po1, po2, po3, bill1, bill2

    def test_example_scenario(self):
        po1, po2, po3, bill1, bill2 = self._setup_example_scenario()
        qty_before = self.product.qty_available
        self.assertEqual(qty_before, 60)

        ret = self._make_return([(self.product, 55)])
        ret.action_compute_allocation()
        self.assertEqual(ret.state, "allocated")
        self.assertEqual(len(ret.allocation_ids), 3)

        expected = [
            (po1, 20, 100000, 2000000),
            (po2, 30, 120000, 3600000),
            (po3, 5, 110000, 550000),
        ]
        for alloc, (po, qty, price, amount) in zip(ret.allocation_ids, expected, strict=False):
            self.assertEqual(alloc.order_id, po)
            self.assertEqual(alloc.qty, qty)
            self.assertEqual(alloc.price_unit, price)
            self.assertEqual(alloc.amount, amount)
            self.assertEqual(alloc.picking_id, po.picking_ids)
        self.assertEqual(ret.amount_total, 6150000)

        ret.action_validate()
        self.assertEqual(ret.state, "done")

        # One return picking per source GR, created as DRAFT (ready) for manual
        # validation -- not auto-completed. Stock and qty_received are untouched
        # until the warehouse validates each picking.
        self.assertEqual(len(ret.return_picking_ids), 3)
        self.assertTrue(all(p.state not in ("done", "cancel") for p in ret.return_picking_ids))
        self.assertEqual(self.product.qty_available, qty_before)
        self.assertEqual(po1.order_line.qty_received, 20)
        self.assertEqual(po2.order_line.qty_received, 30)
        self.assertEqual(po3.order_line.qty_received, 10)

        # Validating the return pickings moves the stock and nets qty_received
        # per PO through the standard to_refund mechanism.
        self._validate_pickings(ret.return_picking_ids)
        self.assertTrue(all(p.state == "done" for p in ret.return_picking_ids))
        self.assertEqual(self.product.qty_available, qty_before - 55)
        self.assertEqual(po1.order_line.qty_received, 0)
        self.assertEqual(po2.order_line.qty_received, 0)
        self.assertEqual(po3.order_line.qty_received, 5)

        # GR + invoice traceability on allocations
        self.assertEqual(ret.allocation_ids[0].source_bill_id, bill1)
        self.assertEqual(ret.allocation_ids[1].source_bill_id, bill2)
        self.assertFalse(ret.allocation_ids[2].source_bill_id)

        # Credit notes: one per original bill + one standalone for PO3
        self.assertEqual(len(ret.credit_note_ids), 3)
        by_reversed = {cn.reversed_entry_id: cn for cn in ret.credit_note_ids}
        self.assertEqual(by_reversed[bill1].amount_total, 2000000)
        self.assertEqual(by_reversed[bill2].amount_total, 3600000)
        standalone = by_reversed[self.env["account.move"]]
        self.assertEqual(standalone.amount_total, 550000)
        self.assertTrue(all(cn.move_type == "in_refund" for cn in ret.credit_note_ids))
        self.assertTrue(all(cn.state == "draft" for cn in ret.credit_note_ids))

    def test_second_return_capped_by_previous_return(self):
        _, _, po3, _, _ = self._setup_example_scenario()
        first = self._make_return([(self.product, 55)])
        first.action_compute_allocation()
        first.action_validate()

        over = self._make_return([(self.product, 6)])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            over.action_compute_allocation()

        second = self._make_return([(self.product, 5)])
        second.action_compute_allocation()
        self.assertEqual(len(second.allocation_ids), 1)
        self.assertEqual(second.allocation_ids.order_id, po3)
        self.assertEqual(second.allocation_ids.qty, 5)
        second.action_validate()

        # PO3 received 10, returned 5 + 5 across both returns; qty_received nets
        # to 0 only once both draft return pickings are validated.
        self._validate_pickings(first.return_picking_ids | second.return_picking_ids)
        self.assertEqual(po3.order_line.qty_received, 0)

    def test_over_return_raises(self):
        self._setup_example_scenario()
        ret = self._make_return([(self.product, 61)])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            ret.action_compute_allocation()

    def test_pending_allocation_blocks_double_booking(self):
        self._setup_example_scenario()
        first = self._make_return([(self.product, 55)])
        first.action_compute_allocation()
        # 55 pending on an allocated (not yet validated) return: only 5 left
        over = self._make_return([(self.product, 6)])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            over.action_compute_allocation()
        # Cancelling releases the pending quantity
        first.action_cancel()
        again = self._make_return([(self.product, 55)])
        again.action_compute_allocation()
        self.assertEqual(sum(again.allocation_ids.mapped("qty")), 55)

    def test_partial_receipt_limits_returnable(self):
        po = self._create_purchase(self.product, 30, 100000)
        self._receive(po, quantity=20)
        self.assertEqual(po.order_line.qty_received, 20)

        over = self._make_return([(self.product, 25)])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            over.action_compute_allocation()

        ret = self._make_return([(self.product, 20)])
        ret.action_compute_allocation()
        self.assertEqual(sum(ret.allocation_ids.mapped("qty")), 20)

    def test_native_return_counted(self):
        po = self._make_received_po(20, 100000)
        receipt = po.picking_ids
        wizard = (
            self.env["stock.return.picking"]
            .with_context(active_id=receipt.id, active_model="stock.picking")
            .create({"picking_id": receipt.id})
        )
        wizard.product_return_moves.quantity = 5
        native_return = wizard._create_return()
        native_return.move_ids.quantity = 5
        native_return.move_ids.picked = True
        native_return.with_context(
            skip_backorder=True,
            picking_ids_not_to_backorder=native_return.ids,
        ).button_validate()
        self.assertEqual(po.order_line.qty_received, 15)

        over = self._make_return([(self.product, 16)])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            over.action_compute_allocation()

        ret = self._make_return([(self.product, 15)])
        ret.action_compute_allocation()
        self.assertEqual(sum(ret.allocation_ids.mapped("qty")), 15)

    def test_multi_product_lines(self):
        product2 = self.env["product.product"].create(
            {
                "name": "RTV Product 2",
                "type": "consu",
                "is_storable": True,
                "purchase_ok": True,
                "purchase_method": "receive",
            }
        )
        po_a = self._make_received_po(10, 1000)
        po_b = self._create_purchase(product2, 5, 2000, receive=True)

        ret = self._make_return([(self.product, 4), (product2, 2)])
        ret.action_compute_allocation()
        self.assertEqual(len(ret.allocation_ids), 2)
        alloc_a = ret.allocation_ids.filtered(lambda a: a.product_id == self.product)
        alloc_b = ret.allocation_ids.filtered(lambda a: a.product_id == product2)
        self.assertEqual(alloc_a.order_id, po_a)
        self.assertEqual((alloc_a.qty, alloc_a.amount), (4, 4000))
        self.assertEqual(alloc_b.order_id, po_b)
        self.assertEqual((alloc_b.qty, alloc_b.amount), (2, 4000))
        self.assertEqual(ret.amount_total, 8000)

    def test_credit_note_nets_qty_invoiced(self):
        po = self._make_received_po(10, 110000)
        ret = self._make_return([(self.product, 5)])
        ret.action_compute_allocation()
        ret.action_validate()

        credit_note = ret.credit_note_ids
        self.assertEqual(len(credit_note), 1)
        self.assertFalse(credit_note.reversed_entry_id)
        self.assertEqual(credit_note.invoice_line_ids.purchase_line_id, po.order_line)
        credit_note.action_post()
        self.assertEqual(po.order_line.qty_invoiced, -5)
