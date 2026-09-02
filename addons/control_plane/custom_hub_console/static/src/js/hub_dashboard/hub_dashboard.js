/** @odoo-module **/

import { Component, useState, onWillStart } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

/**
 * Hub Console landing dashboard.
 *
 * Renders a tile grid summarising the operational state of the platform.
 * Each tile is best-effort — if a sibling model is not installed in this
 * tenant the tile shows an em-dash instead of crashing the whole view.
 */
export class HubDashboard extends Component {
    static template = "custom_hub_console.HubDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            tenantsActive: null,
            tenantsTotal: null,
            modulesTotal: null,
            modulesProduction: null,
            brdPending: null,
            incidentsFiring: null,
            aiCost7d: null,
            hhtOnline: null,
            error: null,
        });

        onWillStart(async () => {
            await this._loadTiles();
        });
    }

    async _safeCount(model, domain = []) {
        try {
            return await this.orm.searchCount(model, domain);
        } catch (e) {
            return null;
        }
    }

    async _safeReadGroup(model, domain, groupby, aggregates) {
        try {
            return await this.orm.formattedReadGroup(
                model, domain, groupby, aggregates);
        } catch (e) {
            return null;
        }
    }

    async _loadTiles() {
        // Tenants
        this.state.tenantsTotal = await this._safeCount("tenant.registry", []);
        this.state.tenantsActive = await this._safeCount(
            "tenant.registry", [["state", "=", "active"]]);

        // Catalog
        this.state.modulesTotal = await this._safeCount(
            "custom.hub.module.catalog", []);
        this.state.modulesProduction = await this._safeCount(
            "custom.hub.module.catalog", [["maturity", "=", "production"]]);

        // BRD (optional)
        this.state.brdPending = await this._safeCount(
            "brd.document", [["state", "in", ["draft", "in_review"]]]);

        // Incidents (optional)
        this.state.incidentsFiring = await this._safeCount(
            "custom.ops.incident", [["state", "=", "firing"]]);

        // AI cost last 7d
        const since = new Date(Date.now() - 7 * 86400000)
            .toISOString().slice(0, 10);
        const aiGroups = await this._safeReadGroup(
            "custom.hub.ai.usage",
            [["date", ">=", since]],
            [],
            ["cost_usd:sum"],
        );
        this.state.aiCost7d = aiGroups && aiGroups.length
            ? (aiGroups[0].cost_usd || 0).toFixed(2)
            : "0.00";

        // HHT devices (optional)
        this.state.hhtOnline = await this._safeCount(
            "hht.device", [["state", "=", "online"]]);

        this.state.loading = false;
    }

    _fmt(v) {
        return v === null || v === undefined ? "—" : v;
    }

    _open(action) {
        if (!action) return;
        this.action.doAction(action);
    }

    onClickTenants() {
        this._open("custom_hub_console.action_hub_verticals");
    }
    onClickModules() {
        this._open("custom_hub_console.action_module_catalog");
    }
    onClickBrd() {
        this._open("custom_brd_analyzer.action_brd_document");
    }
    onClickIncidents() {
        this._open("custom_ops_monitor.action_ops_incident");
    }
    onClickAi() {
        this._open("custom_hub_console.action_ai_usage");
    }
    onClickHht() {
        this._open("custom_hht_bridge.action_hht_device");
    }
    onClickAudit() {
        this._open("custom_hub_console.action_audit_event");
    }
}

registry.category("actions").add(
    "custom_hub_console.hub_dashboard", HubDashboard
);
