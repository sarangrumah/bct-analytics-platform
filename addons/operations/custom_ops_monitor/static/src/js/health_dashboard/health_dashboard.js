/** @odoo-module **/
/*
 * Health Dashboard — OWL 2 client action.
 *
 * Renders a heatmap of all active tenants. Each tile reflects the most
 * recent custom.ops.tenant.health snapshot for that tenant. Clicking a
 * tile opens a detail panel with sparkline timeseries (CPU + memory) and
 * an embedded Grafana iframe pointing at the tenant's per-DB dashboard.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

export class HealthDashboard extends Component {
    static template = "custom_ops_monitor.HealthDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({
            loading: true,
            tiles: [],
            selectedTenantId: null,
            timeseries: [],
            grafanaUrl: null,
            error: null,
        });

        onWillStart(async () => {
            await this._loadTiles();
        });
    }

    async _loadTiles() {
        try {
            // Latest snapshot per tenant: read_group on tenant_id with max(snapshot_at).
            const groups = await this.orm.formattedReadGroup(
                "custom.ops.tenant.health",
                [],
                ["tenant_id"],
                ["snapshot_at:max"],
                { orderby: "tenant_id" },
            );
            const tiles = [];
            for (const g of groups) {
                if (!g.tenant_id) continue;
                const tenantId = g.tenant_id[0];
                const rows = await this.orm.searchRead(
                    "custom.ops.tenant.health",
                    [["tenant_id", "=", tenantId]],
                    [
                        "tenant_id", "cpu_pct", "memory_pct", "disk_pct",
                        "error_rate_pct", "health_score", "status",
                        "snapshot_at", "backup_status",
                    ],
                    { limit: 1, order: "snapshot_at desc" },
                );
                if (rows.length) {
                    tiles.push(rows[0]);
                }
            }
            this.state.tiles = tiles;
            this.state.loading = false;
        } catch (e) {
            this.state.error = e.message || String(e);
            this.state.loading = false;
        }
    }

    tileClass(tile) {
        return `o_health_tile o_health_tile_${tile.status || "unknown"}`;
    }

    async onTileClick(tile) {
        this.state.selectedTenantId = tile.tenant_id[0];
        // Last 24h of snapshots.
        const since = new Date(Date.now() - 24 * 3600 * 1000)
            .toISOString().replace("T", " ").slice(0, 19);
        const rows = await this.orm.searchRead(
            "custom.ops.tenant.health",
            [
                ["tenant_id", "=", tile.tenant_id[0]],
                ["snapshot_at", ">=", since],
            ],
            ["snapshot_at", "cpu_pct", "memory_pct", "disk_pct"],
            { order: "snapshot_at asc" },
        );
        this.state.timeseries = rows;
        // Grafana URL from super-admin config.
        const params = await this.orm.searchRead(
            "ir.config_parameter",
            [["key", "=", "custom_super_admin.grafana_base_url"]],
            ["value"],
            { limit: 1 },
        );
        const tenant = await this.orm.read(
            "tenant.registry", [tile.tenant_id[0]], ["db_name"],
        );
        if (params.length && tenant.length) {
            this.state.grafanaUrl =
                params[0].value.replace(/\/$/, "") +
                "/d/tenant?var-db=" + encodeURIComponent(tenant[0].db_name) +
                "&kiosk=tv";
        } else {
            this.state.grafanaUrl = null;
        }
    }

    closeDetail() {
        this.state.selectedTenantId = null;
        this.state.timeseries = [];
        this.state.grafanaUrl = null;
    }

    sparklinePath(metric) {
        if (!this.state.timeseries.length) return "";
        const w = 300;
        const h = 60;
        const vals = this.state.timeseries.map((r) => r[metric] || 0);
        const max = Math.max(100, ...vals);
        const step = w / Math.max(1, vals.length - 1);
        return vals
            .map((v, i) => {
                const x = (i * step).toFixed(1);
                const y = (h - (v / max) * h).toFixed(1);
                return (i === 0 ? "M" : "L") + x + "," + y;
            })
            .join(" ");
    }
}

registry.category("actions").add(
    "custom_ops_monitor.health_dashboard",
    HealthDashboard,
);
