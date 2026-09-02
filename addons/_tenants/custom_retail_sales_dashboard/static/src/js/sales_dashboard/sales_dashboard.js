/** @odoo-module **/
/*
 * Sales Command Centre — OWL 2 client action for the apparel brand retail vertical.
 *
 * Everything on screen comes from ONE rpc (`retail.sales.report.get_dashboard`).
 * The alternative — a searchRead per widget — costs a dozen round trips that
 * each re-scan the same range of a POS line table with millions of rows.
 *
 * Charts are hand-drawn SVG. Not for purity: an Odoo backend asset bundle has no
 * charting library, and pulling one in would mean vendoring it into the repo and
 * loading it on every backend page for one screen. The shapes needed here — a
 * line, a bar row, a donut, a calendar grid — are a few lines of path maths each.
 *
 * Every visual is a drill-through. Clicking a day, a store, a category slice or
 * a table row opens the Sales Analysis pivot with the matching domain applied, so
 * the dashboard answers "what happened" and then hands over to the data.
 */
import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";

const MODEL = "retail.sales.report";

const COLOR = {
    current: "#2563eb",
    previous: "#94a3b8",
    target: "#f59e0b",
    up: "#16a34a",
    down: "#dc2626",
};

// Categorical ramp for the mix donut and the store bars. Ordered so that the
// first few are distinguishable under the common forms of colour blindness —
// the top three slices are what anyone actually reads off a mix chart.
const RAMP = [
    "#2563eb", "#0d9488", "#f59e0b", "#a855f7", "#ef4444",
    "#0ea5e9", "#84cc16", "#ec4899", "#64748b", "#f97316",
];

const DOW_LABEL = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

// --- date helpers ---------------------------------------------------------
// Local-time formatting on purpose: `toISOString()` is UTC, so between midnight
// and 07:00 WIB it would report yesterday and the "Today" preset would be wrong.
function isoDate(d) {
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function parseDate(s) {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
}
function addDays(d, n) {
    const out = new Date(d);
    out.setDate(out.getDate() + n);
    return out;
}

export class SalesDashboard extends Component {
    static template = "custom_retail_sales_dashboard.SalesDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.COLOR = COLOR;
        this.RAMP = RAMP;
        this.DOW_LABEL = DOW_LABEL;

        const today = new Date();
        this.state = useState({
            loading: true,
            error: null,
            preset: "mtd",
            trendMode: "daily", // "daily" | "cumulative"
            hover: null, // { index, label, lines: [] }
            storeMenuOpen: false,
            options: {
                date_from: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)),
                date_to: isoDate(today),
                compare: "previous",
                channel: "all",
                warehouse_ids: [],
                categ_ids: [],
                include_returns: true,
            },
            data: null,
        });

        onWillStart(() => this.load());
    }

    async load() {
        this.state.loading = true;
        this.state.error = null;
        try {
            this.state.data = await this.orm.call(MODEL, "get_dashboard", [this.state.options]);
        } catch (e) {
            this.state.error = e.message || e.data?.message || String(e);
        } finally {
            this.state.loading = false;
        }
    }

    // ==================================================================
    // Filters
    // ==================================================================
    get presets() {
        return [
            { key: "today", label: _t("Today") },
            { key: "wtd", label: _t("WTD") },
            { key: "mtd", label: _t("MTD") },
            { key: "last_month", label: _t("Last month") },
            { key: "qtd", label: _t("QTD") },
            { key: "ytd", label: _t("YTD") },
        ];
    }

    applyPreset(key) {
        const today = new Date();
        let from = today;
        let to = today;
        if (key === "wtd") {
            // ISO week: Monday-based, which is how the stores report.
            from = addDays(today, -((today.getDay() + 6) % 7));
        } else if (key === "mtd") {
            from = new Date(today.getFullYear(), today.getMonth(), 1);
        } else if (key === "last_month") {
            from = new Date(today.getFullYear(), today.getMonth() - 1, 1);
            to = new Date(today.getFullYear(), today.getMonth(), 0);
        } else if (key === "qtd") {
            from = new Date(today.getFullYear(), Math.floor(today.getMonth() / 3) * 3, 1);
        } else if (key === "ytd") {
            from = new Date(today.getFullYear(), 0, 1);
        }
        this.state.preset = key;
        this.state.options.date_from = isoDate(from);
        this.state.options.date_to = isoDate(to);
        this.load();
    }

    onDateInput(field, ev) {
        if (!ev.target.value) {
            return;
        }
        this.state.preset = "custom";
        this.state.options[field] = ev.target.value;
        this.load();
    }

    setOption(field, value) {
        this.state.options[field] = value;
        this.load();
    }

    toggleStore(id) {
        const selected = this.state.options.warehouse_ids;
        const at = selected.indexOf(id);
        if (at === -1) {
            selected.push(id);
        } else {
            selected.splice(at, 1);
        }
        this.load();
    }

    clearStores() {
        this.state.options.warehouse_ids = [];
        this.load();
    }

    toggleStoreMenu() {
        this.state.storeMenuOpen = !this.state.storeMenuOpen;
    }

    setTrendMode(mode) {
        this.state.trendMode = mode;
        this.state.hover = null;
    }

    get storeFilterLabel() {
        const n = this.state.options.warehouse_ids.length;
        if (!n) {
            return _t("All stores");
        }
        if (n === 1) {
            const store = (this.state.data?.filters.warehouses || []).find(
                (w) => w.id === this.state.options.warehouse_ids[0]
            );
            return store ? store.name : _t("1 store");
        }
        return _t("%s stores", n);
    }

    // ==================================================================
    // Formatting
    // ==================================================================
    get currency() {
        return this.state.data?.currency || { symbol: "", decimals: 0 };
    }

    /** Compact money, Indonesian scale. Retail totals run to 10^10 — a full
     *  number in a KPI tile is unreadable and pushes the layout around. */
    money(value) {
        const sign = value < 0 ? "-" : "";
        const abs = Math.abs(value || 0);
        const scale = [
            [1e12, "T"],
            [1e9, "M"],
            [1e6, "jt"],
            [1e3, "rb"],
        ].find(([step]) => abs >= step);
        if (!scale) {
            return `${sign}${this.currency.symbol} ${Math.round(abs)}`;
        }
        const [step, suffix] = scale;
        const scaled = abs / step;
        const digits = scaled >= 100 ? 0 : 1;
        return `${sign}${this.currency.symbol} ${scaled.toFixed(digits)} ${suffix}`;
    }

    /** Full precision, for tooltips and table cells where the exact number matters. */
    moneyFull(value) {
        const formatted = new Intl.NumberFormat("id-ID", {
            maximumFractionDigits: this.currency.decimals,
        }).format(Math.round(value || 0));
        return `${this.currency.symbol} ${formatted}`;
    }

    int(value) {
        return new Intl.NumberFormat("id-ID").format(Math.round(value || 0));
    }

    decimal(value, digits = 2) {
        return (value || 0).toFixed(digits);
    }

    pct(value, digits = 1) {
        return `${(value || 0).toFixed(digits)}%`;
    }

    formatKpi(tile) {
        switch (tile.format) {
            case "money":
                return this.money(tile.value);
            case "int":
                return this.int(tile.value);
            case "float":
                return this.int(tile.value);
            case "decimal":
                return this.decimal(tile.value);
            case "pct":
                return this.pct(tile.value);
            default:
                return String(tile.value);
        }
    }

    /** A delta is good or bad depending on the metric: discount depth and return
     *  rate improve by falling, so those tiles carry `invert`. */
    deltaClass(tile) {
        if (tile.delta_pct === null || tile.delta_pct === undefined) {
            return "o_lsd_delta_flat";
        }
        const good = tile.invert ? tile.delta_pct < 0 : tile.delta_pct > 0;
        return good ? "o_lsd_delta_up" : "o_lsd_delta_down";
    }

    deltaLabel(delta) {
        if (delta === null || delta === undefined) {
            return "—";
        }
        const arrow = delta >= 0 ? "▲" : "▼";
        return `${arrow} ${Math.abs(delta).toFixed(1)}%`;
    }

    get compareLabel() {
        const opts = this.state.data?.options;
        if (!opts || !opts.compare_from) {
            return _t("no comparison");
        }
        return `${opts.compare_from} → ${opts.compare_to}`;
    }

    // ==================================================================
    // Trend chart
    // ==================================================================
    get trendGeom() {
        const W = 960;
        const H = 260;
        const PAD = { top: 16, right: 16, bottom: 28, left: 68 };
        const series = this.state.data?.trend || [];
        const inner = { w: W - PAD.left - PAD.right, h: H - PAD.top - PAD.bottom };
        const cumulative = this.state.trendMode === "cumulative";

        const valueOf = (p) => (cumulative ? p.cumulative : p.net);
        const prevOf = (p) => (cumulative ? null : p.previous);
        const targetOf = (p) => (cumulative ? p.target_cumulative : p.target);

        const candidates = [];
        for (const p of series) {
            candidates.push(valueOf(p) || 0);
            if (prevOf(p) !== null && prevOf(p) !== undefined) {
                candidates.push(prevOf(p));
            }
            if (targetOf(p)) {
                candidates.push(targetOf(p));
            }
        }
        const max = Math.max(1, ...candidates);
        const min = Math.min(0, ...candidates);
        const span = max - min || 1;
        const step = series.length > 1 ? inner.w / (series.length - 1) : 0;

        const x = (i) => PAD.left + (series.length > 1 ? i * step : inner.w / 2);
        const y = (v) => PAD.top + inner.h - ((v - min) / span) * inner.h;

        const path = (accessor) => {
            let d = "";
            let open = false;
            series.forEach((p, i) => {
                const v = accessor(p);
                if (v === null || v === undefined) {
                    open = false;
                    return;
                }
                d += `${open ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)} `;
                open = true;
            });
            return d.trim();
        };

        const linePath = path(valueOf);
        const areaPath = linePath
            ? `${linePath} L${x(series.length - 1).toFixed(1)},${y(min).toFixed(1)} ` +
              `L${x(0).toFixed(1)},${y(min).toFixed(1)} Z`
            : "";

        // Four gridlines is enough to read a level off; more turns the plot into
        // a ledger and the line stops being the thing you see.
        const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => {
            const value = min + span * f;
            return { value, y: y(value), label: this.money(value) };
        });

        // Month boundaries make a long range readable without crowding the axis
        // with every date; short ranges fall back to roughly six evenly spaced.
        const labelEvery = Math.max(1, Math.ceil(series.length / 8));
        const xLabels = series
            .map((p, i) => ({ i, p }))
            .filter(({ i, p }) => i % labelEvery === 0 || p.date.endsWith("-01"))
            .map(({ i, p }) => ({ x: x(i), label: p.date.slice(5) }));

        return {
            W, H, PAD, inner, series, step,
            linePath, areaPath,
            prevPath: cumulative ? "" : path(prevOf),
            targetPath: path(targetOf),
            ticks, xLabels,
            points: series.map((p, i) => ({ ...p, cx: x(i), cy: y(valueOf(p) || 0), i })),
            baselineY: y(min),
            cumulative,
        };
    }

    onTrendHover(point) {
        const lines = [
            { label: _t("Net sales"), value: this.moneyFull(point.net), color: COLOR.current },
        ];
        if (point.previous !== null && point.previous !== undefined) {
            lines.push({
                label: `${_t("Comparison")} (${point.previous_date})`,
                value: this.moneyFull(point.previous),
                color: COLOR.previous,
            });
        }
        if (point.target) {
            lines.push({
                label: _t("Target pace"),
                value: this.moneyFull(this.state.trendMode === "cumulative"
                    ? point.target_cumulative
                    : point.target),
                color: COLOR.target,
            });
        }
        lines.push({ label: _t("Transactions"), value: this.int(point.transactions) });
        lines.push({ label: _t("Units"), value: this.int(point.units) });
        this.state.hover = {
            index: point.i,
            x: point.cx,
            y: point.cy,
            title: `${point.date} · ${DOW_LABEL[point.dow]}`,
            lines,
        };
    }

    clearHover() {
        this.state.hover = null;
    }

    /** Keep the tooltip inside the plot instead of letting it run off the right edge. */
    tooltipX(hover) {
        const geom = this.trendGeom;
        const width = 210;
        return Math.min(Math.max(hover.x + 12, geom.PAD.left), geom.W - width - geom.PAD.right);
    }

    // ==================================================================
    // Store leaderboard
    // ==================================================================
    get storeRows() {
        const stores = this.state.data?.stores || [];
        const max = Math.max(1, ...stores.map((s) => Math.abs(s.net)));
        return stores.map((s, i) => ({
            ...s,
            width: (Math.abs(s.net) / max) * 100,
            color: RAMP[i % RAMP.length],
            // Attainment bar is capped at 100 so an over-achieving store does not
            // stretch the row; the badge still shows the true figure.
            attainWidth: s.attainment_pct === null ? 0 : Math.min(100, s.attainment_pct),
            attainClass:
                s.attainment_pct === null
                    ? "o_lsd_attain_none"
                    : s.attainment_pct >= 100
                    ? "o_lsd_attain_ok"
                    : s.attainment_pct >= 85
                    ? "o_lsd_attain_near"
                    : "o_lsd_attain_behind",
        }));
    }

    // ==================================================================
    // Category donut
    // ==================================================================
    get donut() {
        const R = 70;
        const STROKE = 26;
        const circumference = 2 * Math.PI * R;
        const categories = (this.state.data?.categories || []).filter((c) => c.net > 0);
        const total = categories.reduce((acc, c) => acc + c.net, 0);
        let offset = 0;
        const segments = categories.slice(0, RAMP.length).map((c, i) => {
            const fraction = total ? c.net / total : 0;
            const seg = {
                ...c,
                color: RAMP[i % RAMP.length],
                dash: `${(fraction * circumference).toFixed(2)} ${circumference.toFixed(2)}`,
                offset: (-offset * circumference).toFixed(2),
            };
            offset += fraction;
            return seg;
        });
        return { R, STROKE, circumference, segments, total };
    }

    // ==================================================================
    // Day-of-week profile
    // ==================================================================
    get dowBars() {
        const rows = this.state.data?.dow || [];
        const max = Math.max(1, ...rows.map((r) => r.avg_net));
        return rows.map((r) => ({
            ...r,
            height: (r.avg_net / max) * 100,
            // Weekend carries a different colour: the split everyone eyeballs first.
            color: r.dow >= 6 ? COLOR.target : COLOR.current,
        }));
    }

    // ==================================================================
    // Calendar heatmap
    // ==================================================================
    get calendar() {
        const series = this.state.data?.trend || [];
        if (!series.length) {
            return { weeks: [], max: 0, cell: 15 };
        }
        const max = Math.max(1, ...series.map((p) => p.net));
        const first = parseDate(series[0].date);
        // Columns are ISO weeks; the offset aligns the first column so Monday is
        // always the top row even when the range starts mid-week.
        const leading = (first.getDay() + 6) % 7;
        const weeks = [];
        series.forEach((p, i) => {
            const slot = i + leading;
            const w = Math.floor(slot / 7);
            if (!weeks[w]) {
                weeks[w] = { index: w, days: [] };
            }
            weeks[w].days.push({
                ...p,
                row: slot % 7,
                intensity: p.net > 0 ? Math.max(0.12, p.net / max) : 0,
            });
        });
        return { weeks: weeks.filter(Boolean), max, cell: 15 };
    }

    cellFill(day) {
        if (!day.intensity) {
            return "#f1f5f9";
        }
        // Single-hue ramp: lightness carries the value, so it stays readable in
        // greyscale and nobody has to decode a rainbow.
        const alpha = 0.15 + day.intensity * 0.85;
        return `rgba(37, 99, 235, ${alpha.toFixed(3)})`;
    }

    // ==================================================================
    // Drill-through
    // ==================================================================
    baseDomain() {
        const opt = this.state.options;
        const domain = [
            ["date", ">=", opt.date_from],
            ["date", "<=", opt.date_to],
        ];
        if (opt.warehouse_ids.length) {
            domain.push(["warehouse_id", "in", opt.warehouse_ids]);
        }
        if (opt.categ_ids.length) {
            domain.push(["categ_id", "child_of", opt.categ_ids]);
        }
        if (opt.channel === "omni") {
            domain.push(["is_omni", "=", true]);
        } else if (opt.channel === "store") {
            domain.push(["is_omni", "=", false]);
        }
        if (!opt.include_returns) {
            domain.push(["is_return", "=", false]);
        }
        return domain;
    }

    async drill(extra, title, view = "pivot") {
        const action = await this.orm.call(MODEL, "action_drill", [
            [...this.baseDomain(), ...(extra || [])],
            title,
            view,
        ]);
        this.action.doAction(action);
    }

    drillDay(point) {
        this.drill([["date", "=", point.date]], `${_t("Sales")} · ${point.date}`, "list");
    }

    drillStore(store) {
        this.drill([["warehouse_id", "=", store.id]], store.name);
    }

    drillCategory(segment) {
        if (!segment.id) {
            return;
        }
        this.drill([["categ_id", "child_of", segment.id]], segment.name);
    }

    drillDow(bar) {
        this.drill([["day_of_week", "=", bar.dow]], `${_t("Sales")} · ${bar.label}`);
    }

    drillProduct(product) {
        this.drill([["product_id", "=", product.id]], product.name, "list");
    }

    drillStaff(person) {
        this.drill([["staff_name", "=", person.name]], person.name, "list");
    }

    drillPromo(promo) {
        this.drill([["discount_code", "=", promo.code]], promo.code, "list");
    }

    drillAll() {
        this.drill([], _t("Sales Analysis"));
    }
}

registry.category("actions").add(
    "custom_retail_sales_dashboard.sales_dashboard",
    SalesDashboard,
);
