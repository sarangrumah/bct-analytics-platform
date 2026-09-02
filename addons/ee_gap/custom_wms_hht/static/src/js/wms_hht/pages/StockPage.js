/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onMounted } from "@odoo/owl";
import { call, qty } from "../rpc";

export class StockPage extends Component {
    static template = "custom_wms_hht.StockPage";
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
            result: null,
            busy: false,
            // Bin rows collapse their lot breakdown by default: a fast-moving
            // SKU can hold a dozen lots, and the operator's first question is
            // "which bin", not "which lot".
            openBins: {},
        });
        onMounted(() => {
            this.props.setScanTarget((code) => this.onScan(code), "Scan product to check stock");
        });
    }

    async onScan(code) {
        this.state.busy = true;
        const res = await call("/hht/wms/stock/lookup", {
            barcode: code,
            warehouse_id: this.props.warehouseId,
        });
        this.state.busy = false;
        if (!res.ok) {
            this.state.result = null;
            return this.props.notify("error", res.error);
        }
        this.state.result = res;
        this.state.openBins = {};
        if (!res.bins.length) {
            this.props.notify("ok", `${res.product.default_code || res.product.name}: no stock on hand.`);
        }
    }

    toggleBin(locationId) {
        this.state.openBins[locationId] = !this.state.openBins[locationId];
    }

    isOpen(locationId) {
        return !!this.state.openBins[locationId];
    }

    clear() {
        this.state.result = null;
        this.state.openBins = {};
        this.props.focusScan();
    }
}
