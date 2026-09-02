/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onWillStart } from "@odoo/owl";
import { call, qty } from "../rpc";
import { scanIntoPickingList } from "../pickingScan";

export class PickPage extends Component {
    static template = "custom_wms_hht.PickPage";
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
            cursor: 0, // index into pendingLines — the line being walked to
            busy: false,
            lastPackage: null,
        });
        onWillStart(() => this.loadList());
    }

    async loadList() {
        this.state.loading = true;
        const res = await call("/hht/wms/pickings", { code: "outgoing", warehouse_id: this.props.warehouseId });
        this.state.loading = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.pickings = res.pickings;
        this.state.picking = null;
        this.state.filter = "";
        this.state.lastPackage = null;
        this.props.setScanTarget((code) => this.openByBarcode(code), "Scan delivery order / SO / item");
    }

    openByBarcode(code) {
        return scanIntoPickingList(this, "outgoing", code);
    }

    /** Drop the scan filter and go back to the full work list. */
    clearFilter() {
        this.loadList();
    }

    async open(pickingId) {
        const res = await call("/hht/wms/picking", { picking_id: pickingId });
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        this.state.cursor = 0;
        this.props.setScanTarget((code) => this.onScan(code), "Scan item at the bin");
    }

    back() {
        this.loadList();
    }

    /** Lines still to pick, in walk order (source bin), so the route is sane. */
    get pendingLines() {
        if (!this.state.picking) return [];
        return this.state.picking.lines
            .filter((l) => !l.picked)
            .sort((a, b) => (a.location > b.location ? 1 : -1));
    }

    get pickedLines() {
        if (!this.state.picking) return [];
        return this.state.picking.lines.filter((l) => l.picked);
    }

    get currentLine() {
        return this.pendingLines[this.state.cursor] || this.pendingLines[0] || null;
    }

    async onScan(code) {
        const line = this.currentLine;
        if (!line) return this.props.notify("error", "Nothing left to pick on this order.");
        this.state.busy = true;
        const res = await call("/hht/wms/pick/confirm", {
            move_line_id: line.id,
            barcode: code,
            quantity: line.demand,
        });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.refreshPicking();
        this.props.notify("ok", `Picked ${qty(res.move_line.quantity)} × ${res.move_line.product}`);
    }

    async confirmManually(line) {
        this.state.busy = true;
        const res = await call("/hht/wms/pick/confirm", {
            move_line_id: line.id,
            quantity: line.demand,
        });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.refreshPicking();
    }

    async refreshPicking() {
        const res = await call("/hht/wms/picking", { picking_id: this.state.picking.id });
        if (res.ok) this.state.picking = res.picking;
    }

    async putInPack() {
        this.state.busy = true;
        const res = await call("/hht/wms/pick/pack", { picking_id: this.state.picking.id });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        this.state.lastPackage = res.package;
        this.props.notify("ok", `Packed as ${res.package ? res.package.name : "package"}`);
    }

    async validate() {
        this.state.busy = true;
        const res = await call("/hht/wms/pick/validate", { picking_id: this.state.picking.id });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.props.refreshQueue();
        this.props.notify("ok", `${res.picking.name} shipped.`);
        this.loadList();
    }
}
