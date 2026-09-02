/** @odoo-module **/

import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

/**
 * Generic on-screen table for every custom accounting report.
 *
 * Reuses the server contract shared with the Excel exporter: the dispatch
 * method ``get_report_table(report_code, options, context_extra)`` returns
 * ``{title, columns, lines, ...}`` where ``columns`` = the report's
 * ``_xlsx_columns()`` and ``lines`` = flattened ``_build_lines`` rows. No
 * per-report JS — one component renders them all with client-side sort +
 * quick filter and Excel/PDF export buttons.
 */
export class ReportTable extends Component {
    static template = "custom_accounting_reports.ReportTable";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        const params = (this.props.action && this.props.action.params) || {};
        this.reportCode = params.report_code;
        this.options = params.options || {};
        this.contextExtra = params.context_extra || {};
        this.state = useState({
            loading: true,
            title: params.title || "",
            companyNames: "",
            period: {},
            currency: { symbol: "", position: "before", decimals: 2 },
            columns: [],
            lines: [],
            sortField: null,
            sortDir: 1,
            filter: "",
        });
        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        const data = await this.orm.call(
            "report.custom_accounting_reports.report_dispatch",
            "get_report_table",
            [this.reportCode, this.options, this.contextExtra]
        );
        this.state.title = data.title || this.state.title;
        this.state.companyNames = data.company_names || "";
        this.state.period = data.period || {};
        this.state.currency = data.currency || this.state.currency;
        this.state.columns = data.columns || [];
        this.state.lines = data.lines || [];
        this.state.loading = false;
    }

    get firstTextField() {
        const col = this.state.columns.find(
            (c) => c.kind !== "number" && c.kind !== "date"
        );
        return col ? col.field : null;
    }

    formatNumber(value) {
        const decimals = this.state.currency.decimals ?? 2;
        return Number(value || 0).toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals,
        });
    }

    formatCell(column, row) {
        const value = row.values ? row.values[column.field] : undefined;
        if (value === undefined || value === null || value === "") {
            // Heading rows (section / group) carry no amounts — leave the
            // numeric cells blank rather than printing a misleading 0.00.
            return "";
        }
        if (column.kind === "number") {
            return this.formatNumber(value);
        }
        return value;
    }

    /**
     * An account row drills down into the General Ledger — except when we
     * already are the General Ledger.
     */
    canDrilldown(row) {
        return Boolean(row.account_id) && this.reportCode !== "general_ledger";
    }

    /**
     * A cell is its own link when the server flags its column with a
     * drilldown scope (e.g. Trial Balance's opening columns → the GL of the
     * fiscal year before this report's period) and the cell carries a figure.
     */
    canDrilldownCell(column, row) {
        return (
            Boolean(column.drilldown) &&
            this.canDrilldown(row) &&
            Boolean(row.values && row.values[column.field])
        );
    }

    async onRowClick(row) {
        if (!this.canDrilldown(row)) {
            return;
        }
        await this.openDrilldown(row, "period");
    }

    async onCellClick(column, row, ev) {
        if (!this.canDrilldownCell(column, row)) {
            return;
        }
        // Otherwise the row handler would fire too and win the race.
        ev.stopPropagation();
        await this.openDrilldown(row, column.drilldown);
    }

    async openDrilldown(row, scope) {
        const action = await this.orm.call(
            "report.custom_accounting_reports.report_dispatch",
            "get_drilldown_action",
            [this.options, row.account_id, scope]
        );
        this.actionService.doAction(action);
    }

    rowClass(row) {
        const type = row.type || "data";
        const classes = ["o_report_row", `o_report_row_${type}`];
        if (this.canDrilldown(row)) {
            classes.push("o_report_row_drilldown");
        }
        if (["grand_total", "total", "subtotal", "check", "header", "group"].includes(type)) {
            classes.push("fw-bold");
        }
        return classes.join(" ");
    }

    cellStyle(column, row) {
        let style = "";
        if (column.kind === "number") {
            style += "text-align:right;";
        }
        if (column.field === this.firstTextField && row.level) {
            style += `padding-left:${8 + row.level * 20}px;`;
        }
        return style;
    }

    get displayLines() {
        let lines = this.state.lines;
        const needle = (this.state.filter || "").trim().toLowerCase();
        if (needle) {
            lines = lines.filter((row) =>
                Object.values(row.values || {}).some(
                    (v) =>
                        v !== null &&
                        v !== undefined &&
                        String(v).toLowerCase().includes(needle)
                )
            );
        }
        const field = this.state.sortField;
        if (field) {
            lines = lines.slice().sort((a, b) => {
                const av = a.values ? a.values[field] : "";
                const bv = b.values ? b.values[field] : "";
                if (typeof av === "number" && typeof bv === "number") {
                    return (av - bv) * this.state.sortDir;
                }
                return String(av ?? "").localeCompare(String(bv ?? "")) * this.state.sortDir;
            });
        }
        return lines;
    }

    onSort(column) {
        if (this.state.sortField === column.field) {
            this.state.sortDir = -this.state.sortDir;
        } else {
            this.state.sortField = column.field;
            this.state.sortDir = 1;
        }
    }

    async onExportXlsx() {
        const action = await this.orm.call(
            "report.custom_accounting_reports.report_dispatch",
            "run_xlsx",
            [this.reportCode, this.options, this.contextExtra]
        );
        this.actionService.doAction(action);
    }

    async onExportPdf() {
        const action = await this.orm.call(
            "report.custom_accounting_reports.report_dispatch",
            "run_pdf",
            [this.reportCode, this.options]
        );
        this.actionService.doAction(action);
    }
}

registry.category("actions").add("custom_report_table", ReportTable);
