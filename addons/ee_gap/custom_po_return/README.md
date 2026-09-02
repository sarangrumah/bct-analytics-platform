# Custom PO Return

Quantity-driven vendor return (retur pembelian / RTV) for Odoo 19 CE.

Odoo's native return works one goods receipt (GR) at a time. This module lets
the user state *"return N units of product X to vendor Y"* and the system
decides which POs/GRs to consume:

- **FIFO allocation** — oldest purchase order first, each slice priced at the
  original PO unit price.
- **Traceability** — every allocation slice records the source PO, GR picking,
  original vendor bill, return picking and credit note.
- **Standard stock behaviour** — return pickings are created through the core
  `stock.return.picking` wizard (one per source GR), so chain links,
  `qty_received` decrement (`to_refund`) and valuation follow core logic.
- **Auto credit notes** — draft `in_refund` moves grouped per original vendor
  bill (`reversed_entry_id` set), plus one standalone credit note for slices
  whose PO has no posted bill yet. Lines carry `purchase_line_id` so PO billed
  quantities stay consistent.
- **Over-return protection** — returnable qty per receipt move accounts for
  native Odoo returns and pending allocations on other PO Return documents.

## Flow

1. **Purchase > Orders > PO Returns > New** — pick the vendor, add product
   lines with total quantities to return.
2. **Compute Allocation** — the system builds the FIFO allocation; review the
   *Allocations* tab (PO / GR / bill / qty / price / amount). Raises an error
   with a per-PO breakdown if the requested qty exceeds what is returnable.
3. **Validate** — creates and validates the return pickings (stock decreases)
   and creates the draft credit notes. Finance reviews and posts the credit
   notes.

Example: PO1 20@100k, PO2 30@120k, PO3 10@110k all received; a return of 55
allocates 20 + 30 + 5 = total 6,150,000, and a later return can take at most
the remaining 5 from PO3.

## Notes

- Quantities are handled in the product's base UoM (v1).
- Multi-currency: a return cannot mix POs in different currencies.
- A validated return cannot be cancelled; reverse the generated documents
  instead.
- Does not depend on GR journal entries, so it works with tenants that
  suppress them (e.g. the apparel brand localization).

## Tests

`tests/test_po_return.py` covers the example scenario end-to-end plus
over-return, partial receipt, native-return interaction, pending-allocation
double booking, multi-product lines and credit-note `qty_invoiced` netting.

```
docker exec odoo19-platform-odoo odoo -d test_po_return -i custom_po_return \
  --test-enable --test-tags /custom_po_return --stop-after-init \
  --http-port 18069 --gevent-port 18072
```
