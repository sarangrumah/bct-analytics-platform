/** @odoo-module **/

import { registry } from "@web/core/registry";

/**
 * Tracks recently opened apps client-side. Backed by localStorage so
 * the list survives reloads without a server round-trip.
 *
 * Pinned shortcuts (the authoritative list) live on res.users and are
 * loaded via the home_console_bootstrap RPC; this service only manages
 * the volatile "recent" list.
 */
const STORAGE_KEY = "custom_home_console.recent_apps";
const MAX_RECENT = 6;

export const homeConsoleRecentService = {
    start() {
        const read = () => {
            try {
                return JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
            } catch (e) {
                return [];
            }
        };
        const write = (list) => {
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
            } catch (e) {
                // Quota exceeded or storage disabled — fall back silently.
            }
        };
        return {
            list() {
                return read();
            },
            push(app) {
                if (!app || !app.id) {
                    return;
                }
                const entry = {
                    id: app.id,
                    name: app.name,
                    xmlid: app.xmlid || null,
                    actionID: app.actionID || null,
                    webIcon: app.webIcon || null,
                };
                const without = read().filter((a) => a.id !== entry.id);
                without.unshift(entry);
                write(without.slice(0, MAX_RECENT));
            },
            clear() {
                write([]);
            },
        };
    },
};

registry
    .category("services")
    .add("home_console_recent", homeConsoleRecentService);
