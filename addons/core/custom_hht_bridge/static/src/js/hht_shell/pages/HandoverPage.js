/** @odoo-module **/
// License: LGPL-3
import { Component, useState } from "@odoo/owl";

export class HandoverPage extends Component {
    static template = "custom_hht_bridge.HandoverPage";
    static props = {
        onScan: Function,
        lastScan: { type: [String, { value: null }], optional: true },
        online: { type: Boolean, optional: true },
    };
    setup() {
        this.state = useState({ bast_ref: "", party: "to" });
    }
    async submit() {
        if (!this.props.lastScan) return;
        await this.props.onScan(this.props.lastScan);
    }
}
