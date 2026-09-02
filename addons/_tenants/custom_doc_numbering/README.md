# the tenant Document Numbering (`custom_doc_numbering`)

Applies the tenant's document-number format (master-data "Document #" sheet) to
the two companies of the tenant.

| Document        | Format                  | Source model / code            |
|-----------------|-------------------------|--------------------------------|
| Sales Quotation | `SQ/<CO>/YYYY/MM/NNN`    | `sale.order` (draft/sent), seq code `sale.order` |
| Sales Order     | `SO/<CO>/YYYY/MM/NNN`    | `sale.order` on confirm, seq code `tenant.sale_order` |
| Purchase Order  | `PO/<CO>/YYYY/MM/NNN`    | `purchase.order`, seq code `purchase.order` |
| Invoice         | `INV/<CO>/YYYY/MM/NNN`   | `account.move` (`out_invoice`) `_get_starting_sequence` |
| Delivery Order  | `DO/the tenant/YYYY/MM/NNN`     | `stock.picking` (the tenant outgoing picking types) |
| BAST            | `BAST/<CO>/YYYY/MM/NNN`  | `custom.bast.document`, seq code `custom.bast.document` |

`<CO>` = `res.company.x_doc_code` (the tenant / the tenant). `NNN` is 3 digits, **reset
monthly**. Monthly reset uses `use_date_range` + the scoped `ir.sequence`
override (`x_monthly_reset`) that auto-creates one date range per calendar month
(stock Odoo only makes yearly ranges).

## Install

Tenant-scoped — install only on tenant DBs:

```
docker exec odoo19-platform-odoo odoo -d trn_tenant -i custom_doc_numbering --stop-after-init
# then, after verifying:
docker exec odoo19-platform-odoo odoo -d prd_tenant -i custom_doc_numbering --stop-after-init
```

The post-init hook sets `x_doc_code` per company (heuristic on company name —
editable on Settings → Companies → *Document Numbering* tab) and builds the
per-company sequences. Re-running the hook (module upgrade) is idempotent.

## Caveats

- Only **new** documents are affected; existing records keep their numbers.
- `Quotation → Sales Order` re-numbers `name` on confirm; the original SQ number
  is preserved on `sale.order.x_quotation_name`.
- **Invoices** are the most sensitive: `_get_starting_sequence` only sets the
  first number of a *fresh* sequence chain. A sale journal that already has
  posted invoices in the current month keeps following its existing pattern
  until a new chain begins (new period). Prefer rolling this out at the start of
  a month / on a journal without prior invoices.
- Inert for any company whose `x_doc_code` is empty.
