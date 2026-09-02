/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onMounted } from "@odoo/owl";
import { call, qty } from "../rpc";

export class PackagePage extends Component {
    static template = "custom_wms_hht.PackagePage";
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
            package: null,
            location: null, // a bin scan shows its contents instead of erroring
            moveArmed: false,
            busy: false,
        });
        onMounted(() => {
            this.props.setScanTarget((code) => this.onScan(code), "Scan package or bin");
        });
    }

    async onScan(code) {
        if (this.state.moveArmed && this.state.package) {
            return this.moveTo(code);
        }
        this.state.busy = true;
        const res = await call("/hht/wms/scan/resolve", { barcode: code });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        if (res.kind === "package") {
            this.state.package = res.record;
            this.state.location = null;
        } else if (res.kind === "location") {
            this.state.location = res.record;
            this.state.package = null;
        } else {
            this.props.notify("error", `That is a ${res.kind}, not a package or bin.`);
        }
    }

    armMove() {
        this.state.moveArmed = true;
        this.props.notify("ok", "Scan the destination bin.");
        this.props.focusScan();
    }

    cancelMove() {
        this.state.moveArmed = false;
    }

    async moveTo(barcode) {
        this.state.busy = true;
        const res = await call("/hht/wms/package/move", {
            package_id: this.state.package.id,
            barcode,
            warehouse_id: this.props.warehouseId,
        });
        this.state.busy = false;
        this.state.moveArmed = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.package = res.package;
        this.props.notify("ok", `${res.package.name} → ${res.package.location} (${res.picking.name})`);
    }

    clear() {
        this.state.package = null;
        this.state.location = null;
        this.state.moveArmed = false;
        this.props.focusScan();
    }
}
