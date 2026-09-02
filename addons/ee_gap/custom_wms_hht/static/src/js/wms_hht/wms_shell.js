/** @odoo-module **/
// License: LGPL-3
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";

import { call } from "./rpc";
import { BURST_IDLE_MS, isScanBurst, now } from "./scanBurst";
import { ReceivePage } from "./pages/ReceivePage";
import { PutawayPage } from "./pages/PutawayPage";
import { PickPage } from "./pages/PickPage";
import { PackagePage } from "./pages/PackagePage";
import { CountPage } from "./pages/CountPage";
import { BinToBinPage } from "./pages/BinToBinPage";
import { StockPage } from "./pages/StockPage";

// A handheld belongs to one warehouse at a time; remembering the choice keeps
// an operator who reboots the device out of the wrong building's queue.
const WAREHOUSE_KEY = "wms_hht_warehouse_id";


// `badge` names the key returned by /hht/wms/queue. A page with no badge key
// (Package) is a lookup tool, not a work queue, so it shows no count.
const MENU = [
    { id: "receive", label: "Receive", icon: "fa-truck", badge: "receive", Comp: ReceivePage },
    { id: "putaway", label: "Put-away", icon: "fa-sitemap", badge: "putaway", Comp: PutawayPage },
    { id: "pick", label: "Pick & Pack", icon: "fa-shopping-basket", badge: "pick", Comp: PickPage },
    { id: "package", label: "Package", icon: "fa-cube", badge: null, Comp: PackagePage },
    { id: "count", label: "Stock Count", icon: "fa-list-ol", badge: "count", Comp: CountPage },
    { id: "bin2bin", label: "Bin to Bin", icon: "fa-exchange", badge: "bin2bin", Comp: BinToBinPage },
    { id: "stock", label: "Stock Check", icon: "fa-search", badge: null, Comp: StockPage },
];

export class WmsHhtShell extends Component {
    static template = "custom_wms_hht.WmsHhtShell";
    static components = {
        ReceivePage, PutawayPage, PickPage, PackagePage, CountPage, BinToBinPage, StockPage,
    };
    static props = {};

    setup() {
        this.state = useState({
            active: "receive",
            sidebarOpen: false,
            online: typeof navigator !== "undefined" ? navigator.onLine : true,
            queue: {},
            warehouse: "",
            warehouses: [],
            warehouseId: Number(localStorage.getItem(WAREHOUSE_KEY)) || null,
            user: "",
            banner: null, // {kind: 'ok'|'error', text}
            // The prompt lives in reactive state: kept on the plain
            // scanTarget object it went stale, so the box still read "Scan
            // transfer" after the operator had moved to another screen.
            scanPrompt: "Scan barcode",
            // Bumped on every select(); see pageKey.
            pageNonce: 0,
        });
        this.scanRef = useRef("scanInput");
        // The active page registers a handler here, so the single scan box at
        // the top of the shell always drives whatever screen is open — the
        // operator never has to find the right input on a 4" display.
        this.scanTarget = { handler: null };
        // Keystroke timing, so a scan can be recognised without a terminator.
        this._keyTimes = [];
        this._burstTimer = null;
        this._inFlight = false;

        // Bound ONCE, here, where `this` is the component itself.
        //
        // These used to be bound inside the `pageProps` getter, which OWL
        // evaluates while rendering — and there `this` is the reactive proxy,
        // not the instance. Writes to reactive state still landed (the scan
        // prompt and the banners updated, which is why the screens looked
        // alive), but `scanTarget` is a plain property, so every page's
        // handler was written to the proxy and never reached the instance the
        // scan box reads. The box therefore answered "This screen is not
        // waiting for a scan" for every scan on every screen. Binding once in
        // setup also stops handing the pages four new function identities on
        // every render.
        this._pageApi = {
            setScanTarget: this.setScanTarget.bind(this),
            notify: this.notify.bind(this),
            refreshQueue: this.refreshQueue.bind(this),
            focusScan: this.focusScan.bind(this),
        };

        this._onOnline = () => (this.state.online = true);
        this._onOffline = () => (this.state.online = false);

        onMounted(async () => {
            window.addEventListener("online", this._onOnline);
            window.addEventListener("offline", this._onOffline);
            await this.loadWarehouses();
            await this.refreshQueue();
            this._queueTimer = setInterval(() => this.refreshQueue(), 60000);
            this.focusScan();
        });
        onWillUnmount(() => {
            window.removeEventListener("online", this._onOnline);
            window.removeEventListener("offline", this._onOffline);
            clearInterval(this._queueTimer);
            clearTimeout(this._burstTimer);
        });
    }

    get menu() {
        return MENU;
    }

    get current() {
        return MENU.find((m) => m.id === this.state.active) || MENU[0];
    }

    async loadWarehouses() {
        const res = await call("/hht/wms/warehouses");
        if (!res.ok) return;
        this.state.warehouses = res.warehouses;
        const known = res.warehouses.some((w) => w.id === this.state.warehouseId);
        if (!known) {
            this.state.warehouseId = res.default_id || null;
            if (this.state.warehouseId) {
                localStorage.setItem(WAREHOUSE_KEY, String(this.state.warehouseId));
            }
        }
    }

    async onWarehouseChange(ev) {
        this.state.warehouseId = Number(ev.target.value) || null;
        localStorage.setItem(WAREHOUSE_KEY, String(this.state.warehouseId));
        await this.refreshQueue();
        // Re-enter the page so its list reloads against the new warehouse.
        this.select(this.state.active);
    }

    /** Identity of the mounted page. Changing it remounts, which is the point.
     *
     * A page claims the shell's scan box from onMounted, and select() clears
     * the claim — so any select() that does *not* remount leaves the box with
     * no owner and every scan answers "This screen is not waiting for a scan"
     * until the operator navigates elsewhere and back. Two ordinary actions
     * did exactly that: tapping the menu entry you are already on, and
     * changing warehouse (which re-enters the same page by design). The nonce
     * makes every select() a real remount, so the claim is always renewed.
     */
    get pageKey() {
        return `${this.state.active}:${this.state.warehouseId}:${this.state.pageNonce}`;
    }

    async refreshQueue() {
        const res = await call("/hht/wms/queue", { warehouse_id: this.state.warehouseId });
        if (res.ok) {
            this.state.queue = res.queue;
            this.state.warehouse = res.warehouse;
            this.state.user = res.user;
        }
    }

    badge(item) {
        if (!item.badge) return 0;
        return this.state.queue[item.badge] || 0;
    }

    select(id) {
        this.state.active = id;
        this.state.sidebarOpen = false;
        this.state.banner = null;
        this.scanTarget = { handler: null };
        this.state.scanPrompt = "Scan barcode";
        this.state.pageNonce++;
        this._cancelBurst();
        this.focusScan();
    }

    toggleSidebar() {
        this.state.sidebarOpen = !this.state.sidebarOpen;
    }

    focusScan() {
        setTimeout(() => this.scanRef.el && this.scanRef.el.focus(), 50);
    }

    /** Pages call this in setup/onMounted to own the shell's scan box. */
    setScanTarget(handler, prompt) {
        this.scanTarget = { handler };
        this.state.scanPrompt = prompt || "Scan barcode";
        this.focusScan();
    }

    notify(kind, text) {
        this.state.banner = { kind, text };
        if (kind === "ok") {
            setTimeout(() => {
                if (this.state.banner && this.state.banner.text === text) this.state.banner = null;
            }, 4000);
        }
    }

    /** Enter is the happy path, but never the only one — see onScanInput. */
    onScanKeydown(ev) {
        // Some wedge scanners send a bare keyCode 13 with no `key`, and some
        // are configured with a Tab suffix instead of a carriage return.
        const isSubmit = ev.key === "Enter" || ev.keyCode === 13 || ev.key === "Tab";
        if (!isSubmit) return;
        ev.preventDefault();
        this._cancelBurst();
        return this.submitScan();
    }

    /** Fire a scan that arrived without any terminator at all.
     *
     * The Denso BHT units on the floor can be configured with no suffix: the
     * barcode lands in the box and nothing happens, which reads as "the app
     * ignored my scan" — the Stock Check screen showed no stock for exactly
     * this reason, because the lookup was never called. A hardware scanner
     * types far faster than a thumb, so a burst of quick keystrokes followed
     * by a pause is a scan and can be submitted on its own. Anything typed
     * at human speed is left alone: that is what the Go button is for.
     */
    onScanInput() {
        this._keyTimes.push(now());
        if (this._keyTimes.length > 40) this._keyTimes.shift();
        clearTimeout(this._burstTimer);
        this._burstTimer = setTimeout(() => this._maybeSubmitBurst(), BURST_IDLE_MS);
    }

    _cancelBurst() {
        clearTimeout(this._burstTimer);
        this._burstTimer = null;
        this._keyTimes = [];
    }

    _maybeSubmitBurst() {
        const times = this._keyTimes;
        const code = ((this.scanRef.el && this.scanRef.el.value) || "").trim();
        this._keyTimes = [];
        if (!isScanBurst(times, code.length)) return;
        this.submitScan();
    }

    /** The Go button, for a code typed by hand or pasted. */
    onScanGo() {
        this._cancelBurst();
        return this.submitScan();
    }

    async submitScan() {
        const input = this.scanRef.el;
        if (!input) return;
        const code = (input.value || "").trim();
        if (!code) return;
        // A scanner that double-triggers, or a Go tap landing on top of a
        // burst, must not run the same movement twice.
        if (this._inFlight) return;
        input.value = "";
        if (!this.scanTarget.handler) {
            // Self-heal rather than blame the operator: remount the page so it
            // re-claims the box. Losing one scan to a re-scan beats a screen
            // that stays deaf until the operator navigates away and back.
            this.state.pageNonce++;
            this.notify("error", "Screen was not ready — scan again.");
            this.focusScan();
            return;
        }
        this._inFlight = true;
        try {
            await this.scanTarget.handler(code);
        } catch (e) {
            this.notify("error", String(e));
        } finally {
            this._inFlight = false;
            this.focusScan();
        }
    }

    get pageProps() {
        return { warehouseId: this.state.warehouseId, ...this._pageApi };
    }
}
