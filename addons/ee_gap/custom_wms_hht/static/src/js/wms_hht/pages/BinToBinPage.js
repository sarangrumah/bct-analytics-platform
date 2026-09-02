/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onWillStart } from "@odoo/owl";
import { call, qty } from "../rpc";

export class BinToBinPage extends Component {
    static template = "custom_wms_hht.BinToBinPage";
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
            orders: [],
            filter: "", // scanned code the order list is narrowed by
            order: null,
            step: "source", // source -> target -> done
            sourceScan: "",
            busy: false,
        });
        onWillStart(() => this.loadList());
    }

    async loadList() {
        this.state.loading = true;
        const res = await call("/hht/wms/bin2bin/list");
        this.state.loading = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.orders = res.orders;
        this.state.order = null;
        this.state.filter = "";
        // Scanning the bin the operator is standing at answers "is there
        // anything to move out of here?" — better than refusing the scan.
        this.props.setScanTarget((code) => this.findOrder(code), "Scan bin / item / order no.");
    }

    async findOrder(code) {
        const res = await call("/hht/wms/bin2bin/list", { query: code });
        if (!res.ok) return this.props.notify("error", res.error);
        const hits = res.orders || [];
        if (!hits.length) return this.props.notify("error", `No move to make for ${code}.`);
        if (hits.length === 1) {
            this.state.filter = "";
            const order = hits[0];
            this.open(order);
            // Scanning the source bin is step one anyway — don't make the
            // operator scan the same label twice.
            if (order.source_barcode && order.source_barcode === code.trim()) {
                return this.onScan(code);
            }
            return;
        }
        this.state.orders = hits;
        this.state.filter = code;
        this.props.notify("ok", `${hits.length} moves match ${code} — tap one.`);
    }

    /** Drop the scan filter and go back to the full order list. */
    clearFilter() {
        this.loadList();
    }

    open(order) {
        this.state.order = order;
        this.state.step = "source";
        this.state.sourceScan = "";
        this.props.setScanTarget((code) => this.onScan(code), `Scan source bin ${order.source_barcode || ""}`);
    }

    back() {
        this.loadList();
    }

    async onScan(code) {
        const order = this.state.order;
        if (!order) return;
        if (this.state.step === "source") {
            if (order.source_barcode && code.trim() !== order.source_barcode) {
                return this.props.notify("error", `Wrong bin. Go to ${order.source}.`);
            }
            this.state.sourceScan = code.trim();
            this.state.step = "target";
            this.props.setScanTarget((c) => this.onScan(c), `Scan target bin ${order.target_barcode || ""}`);
            this.props.notify("ok", `Take ${qty(order.planned_qty)} × ${order.default_code}, walk to ${order.target}.`);
            return;
        }
        // target step — the backend re-checks both bins, this is just the UX
        this.state.busy = true;
        const res = await call("/hht/wms/bin2bin/execute", {
            transfer_order_id: order.id,
            source_barcode: this.state.sourceScan,
            target_barcode: code.trim(),
        });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        await this.props.refreshQueue();
        this.props.notify("ok", `${res.order.name} done — ${qty(res.moved)} moved.`);
        this.loadList();
    }
}
