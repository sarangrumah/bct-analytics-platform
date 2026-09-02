/** @odoo-module **/
// License: LGPL-3
import { Component, useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";

import { buildSignedRequest } from "./crypto";
import * as SyncQueue from "./sync_queue";

import { ReceivePage } from "./pages/ReceivePage";
import { IssuePage } from "./pages/IssuePage";
import { TransferPage } from "./pages/TransferPage";
import { CountPage } from "./pages/CountPage";
import { HandoverPage } from "./pages/HandoverPage";

const TABS = [
    { id: "receive", label: "Receive", Comp: ReceivePage },
    { id: "issue", label: "Issue", Comp: IssuePage },
    { id: "transfer", label: "Transfer", Comp: TransferPage },
    { id: "count", label: "Count", Comp: CountPage },
    { id: "handover", label: "Handover", Comp: HandoverPage },
];

const DEVICE_KEY_KEY = "hht_device_key";
const DEVICE_SECRET_KEY = "hht_device_secret";

async function _getCredentials() {
    // Stored in localStorage after device pairing (out-of-band).
    const apiKey = localStorage.getItem(DEVICE_KEY_KEY) || "";
    const secret = localStorage.getItem(DEVICE_SECRET_KEY) || "";
    return { apiKey, secret };
}

export class HhtShell extends Component {
    static template = "custom_hht_bridge.HhtShell";
    static components = { ReceivePage, IssuePage, TransferPage, CountPage, HandoverPage };
    static props = {};

    setup() {
        this.state = useState({
            active_tab: "receive",
            online: typeof navigator !== "undefined" ? navigator.onLine : true,
            pending_count: 0,
            last_scan: null,
            error: null,
            me: null,
        });
        this.scanInputRef = useRef("scanInput");
        this._onlineHandler = () => this._setOnline(true);
        this._offlineHandler = () => this._setOnline(false);

        onMounted(async () => {
            window.addEventListener("online", this._onlineHandler);
            window.addEventListener("offline", this._offlineHandler);
            await this._refreshPendingCount();
            await this._loadMe();
            SyncQueue.startAutoFlush(this._signedFetch.bind(this), 30000);
            this._refocus();
        });

        onWillUnmount(() => {
            window.removeEventListener("online", this._onlineHandler);
            window.removeEventListener("offline", this._offlineHandler);
            SyncQueue.stopAutoFlush();
        });
    }

    _setOnline(v) {
        this.state.online = v;
    }

    async _refreshPendingCount() {
        try {
            this.state.pending_count = await SyncQueue.count();
        } catch (_e) { /* ignore */ }
    }

    async _signedFetch(url, payload) {
        const { apiKey, secret } = await _getCredentials();
        const { body, headers } = await buildSignedRequest(secret, payload);
        headers["X-Device-Key"] = apiKey;
        return fetch(url, { method: "POST", headers, body });
    }

    async _loadMe() {
        try {
            const { apiKey, secret } = await _getCredentials();
            if (!apiKey || !secret) return;
            const ts = Math.floor(Date.now() / 1000).toString();
            const sig = (await buildSignedRequest(secret, {})).headers["X-Signature"];
            const resp = await fetch("/api/hht/me", {
                method: "GET",
                headers: {
                    "X-Timestamp": ts,
                    "X-Signature": sig,
                    "X-Device-Key": apiKey,
                },
            });
            if (resp.ok) {
                const data = await resp.json();
                if (data && data.ok) this.state.me = data.result;
            }
        } catch (_e) { /* offline ok */ }
    }

    setTab(tabId) {
        this.state.active_tab = tabId;
        this._refocus();
    }

    _refocus() {
        setTimeout(() => {
            const el = this.scanInputRef.el;
            if (el) el.focus();
        }, 50);
    }

    async onScanKeydown(ev) {
        if (ev.key !== "Enter") return;
        ev.preventDefault();
        const input = ev.target;
        const barcode = (input.value || "").trim();
        if (!barcode) return;
        input.value = "";
        await this.onScan(barcode);
    }

    async onScan(barcode) {
        this.state.error = null;
        this.state.last_scan = barcode;
        const payload = {
            barcode,
            action: this.state.active_tab === "handover" ? "handover" : this.state.active_tab,
        };
        try {
            if (!this.state.online) {
                await SyncQueue.enqueue(payload);
                await this._refreshPendingCount();
                return;
            }
            const resp = await this._signedFetch("/api/hht/scan", payload);
            if (!resp.ok) {
                this.state.error = `HTTP ${resp.status}`;
                await SyncQueue.enqueue(payload);
                await this._refreshPendingCount();
                return;
            }
            const data = await resp.json();
            if (!data.ok) this.state.error = data.error || "scan failed";
        } catch (e) {
            this.state.error = String(e);
            await SyncQueue.enqueue(payload);
            await this._refreshPendingCount();
        } finally {
            this._refocus();
        }
    }

    get currentTab() {
        return TABS.find((t) => t.id === this.state.active_tab) || TABS[0];
    }

    get tabs() {
        return TABS;
    }
}
