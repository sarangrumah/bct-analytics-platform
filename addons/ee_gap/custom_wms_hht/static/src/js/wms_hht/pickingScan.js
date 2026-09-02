/** @odoo-module **/
// License: LGPL-3
import { call } from "./rpc";

/**
 * Drive a transfer-list screen from one scan.
 *
 * Receive, Put-away and Pick all show the same thing — a list of open
 * transfers — and all suffered the same defect: the scan box only ever matched
 * an exact stock.picking name, so scanning the vendor's delivery note, the PO
 * number or an item out of the carton left the list untouched and answered
 * "Unknown barcode". The backend now decides what the code means; this decides
 * what the screen does about it:
 *
 *   1 match  → open the document straight away (no tapping)
 *   n matches → narrow the list to those n and say why
 *   0 matches → say so, and leave the full list alone
 *
 * `page` must expose `state.pickings`, `state.filter` and an `open(id)`.
 */
export async function scanIntoPickingList(page, typeCode, code) {
    const res = await call("/hht/wms/pickings", {
        code: typeCode,
        warehouse_id: page.props.warehouseId,
        query: code,
    });
    if (!res.ok) return page.props.notify("error", res.error);

    const hits = res.pickings || [];
    if (!hits.length) {
        page.props.notify("error", `Nothing open matches ${code}.`);
        return;
    }
    if (hits.length === 1) {
        const only = hits[0];
        page.state.filter = "";
        // A transfer found outside the open states is not work — opening it
        // would show a screen whose buttons all refuse. Name its state instead.
        if (res.matched_by === "closed") {
            page.props.notify("error", `${only.name} is ${only.state}, not open work.`);
            page.state.pickings = hits;
            page.state.filter = code;
            return;
        }
        await page.open(only.id);
        return;
    }
    page.state.pickings = hits;
    page.state.filter = code;
    page.props.notify(
        "ok",
        `${hits.length} transfers match ${code}${res.matched_by === "product" ? " (by item)" : ""} — tap one.`
    );
}
