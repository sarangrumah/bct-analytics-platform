/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onWillStart } from "@odoo/owl";
import { call, qty } from "../rpc";
import { scanIntoPickingList } from "../pickingScan";

export class ReceivePage extends Component {
    static template = "custom_wms_hht.ReceivePage";
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
            lastScan: null,
            manualQty: "",
            supplierBatch: "",
            busy: false,
        });
        onWillStart(() => this.loadList());
    }

    async loadList() {
        this.state.loading = true;
        const res = await call("/hht/wms/pickings", { code: "incoming", warehouse_id: this.props.warehouseId });
        this.state.loading = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.pickings = res.pickings;
        this.state.picking = null;
        this.state.filter = "";
        // On the list screen a scan jumps straight to the matching receipt —
        // the operator scans the paperwork instead of hunting in a list. Any
        // code on that paperwork works (receipt no., PO/origin, vendor,
        // tracking), and so does an item out of the carton.
        this.props.setScanTarget((code) => this.openByBarcode(code), "Scan receipt / PO / item");
    }

    openByBarcode(code) {
        return scanIntoPickingList(this, "incoming", code);
    }

    /** Drop the scan filter and go back to the full work list. */
    clearFilter() {
        this.loadList();
    }

    async open(pickingId) {
        const res = await call("/hht/wms/picking", { picking_id: pickingId });
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        this.props.setScanTarget((code) => this.onScan(code), "Scan item / GS1 / IMEI");
    }

    back() {
        this.loadList();
    }

    async onScan(code) {
        if (this.state.busy) return;
        this.state.busy = true;
        const params = { picking_id: this.state.picking.id, barcode: code };
        if (this.state.manualQty !== "") params.quantity = Number(this.state.manualQty);
        if (this.state.supplierBatch) params.supplier_batch = this.state.supplierBatch;
        const res = await call("/hht/wms/receive/scan", params);
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        this.state.lastScan = res.scanned;
        this.state.manualQty = "";
        this.props.notify(
            "ok",
            `${res.scanned.product} +${qty(res.scanned.quantity)}${res.scanned.lot ? " / " + res.scanned.lot : ""}`
        );
    }

    async validate() {
        this.state.busy = true;
        const res = await call("/hht/wms/receive/validate", { picking_id: this.state.picking.id });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.picking = res.picking;
        await this.props.refreshQueue();
        if (res.qc_required) {
            this.props.notify("ok", "Received. Goods are in quarantine — QC decision needed.");
        } else {
            this.props.notify("ok", `${res.picking.name} received.`);
            this.loadList();
        }
    }

    async qc(verdict) {
        this.state.busy = true;
        const res = await call("/hht/wms/qc", { picking_id: this.state.picking.id, verdict });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.props.refreshQueue();
        this.props.notify(
            verdict === "pass" ? "ok" : "error",
            verdict === "pass"
                ? `QC passed. Release transfer ${res.release_picking ? res.release_picking.name : "-"} ready for put-away.`
                : "QC failed — goods stay quarantined."
        );
        this.loadList();
    }

    get needsQc() {
        const p = this.state.picking;
        return p && p.state === "done" && p.qc_state === "pending";
    }
}
