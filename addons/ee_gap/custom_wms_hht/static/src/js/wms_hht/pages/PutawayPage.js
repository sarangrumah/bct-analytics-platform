/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onWillStart } from "@odoo/owl";
import { call, qty } from "../rpc";
import { scanIntoPickingList } from "../pickingScan";

export class PutawayPage extends Component {
    static template = "custom_wms_hht.PutawayPage";
    static props = {
        warehouseId: { type: [Number, { value: null }], optional: true },
        setScanTarget: Function,
        notify: Function,
        refreshQueue: Function,
        focusScan: Function,
    };

    setup() {
        this.qty = qty;
        this.state = useState({
            loading: true,
            pickings: [],
            filter: "", // the scanned code the list is narrowed by, if any
            picking: null,
            rows: [],
            activeRow: null, // move_line id awaiting a bin scan (override)
            busy: false,
        });
        onWillStart(() => this.loadList());
    }

    async loadList() {
        this.state.loading = true;
        const res = await call("/hht/wms/pickings", { code: "internal", warehouse_id: this.props.warehouseId });
        this.state.loading = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.pickings = res.pickings;
        this.state.picking = null;
        this.state.filter = "";
        this.state.rows = [];
        this.props.setScanTarget((code) => this.openByBarcode(code), "Scan transfer / source doc / item");
    }

    openByBarcode(code) {
        return scanIntoPickingList(this, "internal", code);
    }

    /** Drop the scan filter and go back to the full work list. */
    clearFilter() {
        this.loadList();
    }

    async open(pickingId) {
        const res = await call("/hht/wms/putaway/suggest", { picking_id: pickingId });
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        this.state.rows = res.rows;
        this.state.activeRow = null;
        this.props.setScanTarget((code) => this.onBinScan(code), "Scan destination bin");
    }

    back() {
        this.loadList();
    }

    /** Accept the engine's own top-ranked bin without walking anywhere else. */
    async accept(row) {
        const top = row.proposals[0];
        if (!top) return this.props.notify("error", "No suggestion for this line.");
        await this.apply(row.move_line.id, { location_id: top.location_id });
    }

    armOverride(row) {
        this.state.activeRow = row.move_line.id;
        this.props.notify("ok", "Scan the bin you are actually putting it in.");
        this.props.focusScan();
    }

    async onBinScan(code) {
        const target = this.state.activeRow || (this.state.rows[0] && this.state.rows[0].move_line.id);
        if (!target) return this.props.notify("error", "Nothing to put away here.");
        await this.apply(target, { barcode: code });
    }

    async apply(moveLineId, extra) {
        this.state.busy = true;
        const res = await call("/hht/wms/putaway/apply", { move_line_id: moveLineId, ...extra });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        const row = this.state.rows.find((r) => r.move_line.id === moveLineId);
        if (row) row.move_line = res.move_line;
        this.state.activeRow = null;
        this.props.notify("ok", `${res.move_line.product} → ${res.move_line.location_dest}`);
    }

    async validate() {
        this.state.busy = true;
        const res = await call("/hht/wms/pick/validate", { picking_id: this.state.picking.id });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.props.refreshQueue();
        this.props.notify("ok", `${res.picking.name} stored.`);
        this.loadList();
    }
}
