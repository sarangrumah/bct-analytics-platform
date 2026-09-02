/** @odoo-module **/
// License: LGPL-3
import { Component, useState, onWillStart } from "@odoo/owl";
import { call, qty } from "../rpc";

export class CountPage extends Component {
    static template = "custom_wms_hht.CountPage";
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
            sessions: [],
            filter: "", // scanned code the session list is narrowed by
            session: null,
            lines: [],
            active: null, // line being counted
            entry: "",
            busy: false,
        });
        onWillStart(() => this.loadSessions());
    }

    async loadSessions() {
        this.state.loading = true;
        const res = await call("/hht/wms/count/sessions");
        this.state.loading = false;
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.sessions = res.sessions;
        this.state.session = null;
        this.state.filter = "";
        this.state.lines = [];
        // Refusing every scan until a session is tapped open was the same
        // dead-end as on Receive: the operator is standing at a bin holding a
        // scanner, so let the bin (or the item, or the sheet number) find it.
        this.props.setScanTarget((code) => this.findSession(code), "Scan count sheet / bin / item");
    }

    async findSession(code) {
        const res = await call("/hht/wms/count/sessions", { query: code });
        if (!res.ok) return this.props.notify("error", res.error);
        const hits = res.sessions || [];
        if (!hits.length) return this.props.notify("error", `No open count sheet covers ${code}.`);
        if (hits.length === 1) {
            this.state.filter = "";
            // Carry the scan into the session: a bin scan should land on the
            // line for that bin, not just open the sheet.
            await this.open(hits[0].id);
            return this.onScan(code);
        }
        this.state.sessions = hits;
        this.state.filter = code;
        this.props.notify("ok", `${hits.length} count sheets match ${code} — tap one.`);
    }

    /** Drop the scan filter and go back to the full session list. */
    clearFilter() {
        this.loadSessions();
    }

    async open(sessionId) {
        const res = await call("/hht/wms/count/lines", { session_id: sessionId });
        if (!res.ok) return this.props.notify("error", res.error);
        this.state.session = res.session;
        this.state.lines = res.lines;
        this.state.active = null;
        this.props.setScanTarget((code) => this.onScan(code), "Scan bin or item to count");
    }

    back() {
        this.loadSessions();
    }

    /** A scan selects the line to count — bin barcode narrows, item picks. */
    onScan(code) {
        const c = code.trim();
        const byBin = this.state.lines.filter((l) => l.location_barcode === c && l.status === "pending");
        if (byBin.length) {
            this.state.active = byBin[0];
            this.props.notify("ok", `${byBin.length} line(s) in ${byBin[0].location}`);
            return;
        }
        const byItem = this.state.lines.find(
            (l) => (l.barcode === c || l.default_code === c || l.lot === c) && l.status === "pending"
        );
        if (byItem) {
            this.state.active = byItem;
            return;
        }
        this.props.notify("error", `${c} is not on this count sheet.`);
    }

    select(line) {
        this.state.active = line;
        this.state.entry = "";
    }

    async submit() {
        if (!this.state.active) return;
        const value = Number(this.state.entry);
        if (Number.isNaN(value)) return this.props.notify("error", "Enter a number.");
        this.state.busy = true;
        const res = await call("/hht/wms/count/submit", {
            line_id: this.state.active.id,
            quantity: value,
        });
        this.state.busy = false;
        if (!res.ok) return this.props.notify("error", res.error);
        const line = this.state.lines.find((l) => l.id === res.line.id);
        if (line) {
            line.counted_qty = res.line.counted_qty;
            line.variance_qty = res.line.variance_qty;
            line.status = res.line.status;
        }
        this.state.entry = "";
        this.state.active = null;
        const variance = res.line.variance_qty;
        this.props.notify(
            variance ? "error" : "ok",
            variance ? `Variance ${qty(variance)} recorded.` : "Counted, no variance."
        );
        this.props.focusScan();
    }

    get pending() {
        return this.state.lines.filter((l) => l.status === "pending");
    }

    get counted() {
        return this.state.lines.filter((l) => l.status !== "pending");
    }
}
