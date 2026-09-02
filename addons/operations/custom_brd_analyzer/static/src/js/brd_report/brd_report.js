/** @odoo-module **/

import { Component, onMounted, useState, xml } from "@odoo/owl";
import { registry } from "@web/core/registry";

/**
 * BRD Report OWL component.
 *
 * Hydrates from the JSON payload embedded by the controller into a div with
 * class ``o_brd_report_owl`` and a ``data-payload`` attribute. The component
 * gracefully no-ops if the host page is missing the payload (e.g. plain QWeb
 * fallback view).
 */
export class BrdReport extends Component {
    static template = "custom_brd_analyzer.BrdReport";
    static props = {};

    setup() {
        this.state = useState({
            loading: true,
            payload: null,
            shareUrl: "",
        });
        onMounted(() => this._hydrate());
    }

    _hydrate() {
        const host = document.querySelector(".o_brd_report_owl");
        if (!host) {
            this.state.loading = false;
            return;
        }
        try {
            const raw = host.getAttribute("data-payload") || "{}";
            this.state.payload = JSON.parse(raw);
        } catch (err) {
            console.warn("BRD report: bad payload", err);
            this.state.payload = null;
        }
        this.state.loading = false;
    }

    severityClass(sev) {
        if (sev === "must_have") return "badge bg-danger";
        if (sev === "should_have") return "badge bg-warning";
        return "badge bg-secondary";
    }

    statusClass(status) {
        if (status === "covered") return "badge bg-success";
        if (status === "partial") return "badge bg-warning";
        if (status === "missing") return "badge bg-danger";
        return "badge bg-secondary";
    }

    async onShareClick() {
        if (!this.state.payload) return;
        const docId = this.state.payload.doc_id;
        try {
            const res = await fetch(`/brd/${docId}/share`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ jsonrpc: "2.0", method: "call", params: {} }),
            });
            const json = await res.json();
            if (json && json.result && json.result.url) {
                this.state.shareUrl = json.result.url;
            }
        } catch (err) {
            console.error("share failed", err);
        }
    }

    onDownloadPdf() {
        if (!this.state.payload) return;
        window.open(`/brd/${this.state.payload.doc_id}/report.pdf`, "_blank");
    }
}

registry.category("public_components").add("custom_brd_analyzer.BrdReport", BrdReport);
