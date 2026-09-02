/** @odoo-module **/
// License: LGPL-3
import { Component, useState } from "@odoo/owl";

export class CountPage extends Component {
    static template = "custom_hht_bridge.CountPage";
    static props = {
        onScan: Function,
        lastScan: { type: [String, { value: null }], optional: true },
        online: { type: Boolean, optional: true },
    };
    setup() {
        this.state = useState({ location_id: "", qty: 0, lot: "" });
    }
    async submit() {
        if (!this.props.lastScan) return;
        await this.props.onScan(this.props.lastScan);
    }
}
